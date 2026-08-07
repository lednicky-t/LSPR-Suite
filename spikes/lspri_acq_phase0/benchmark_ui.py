"""LSPRimaging Acquisition — Phase 0 throughput spike, with a live preview.

Not part of any app. Purpose: measure real Basler capture rate and per-frame
ROI-extraction cost on the actual camera. NOTE: there is no fixed rate target -
the goal is "as fast as achievable", not "clear 16 Hz". 16 Hz appears in labels
below only as a historical reference point from early planning, not a pass/fail
line. See docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md, §3.

Run: .venv\\Scripts\\python.exe spikes\\lspri_acq_phase0\\benchmark_ui.py

What this shows:
- A live preview (so you can confirm focus/exposure/framing while measuring -
  a pure console script can't do that).
- Pixel format, hardware binning (1x/2x/4x - trades resolution for lower data
  volume while keeping the full field of view), and exposure time, all
  adjustable and applied on the next "Start capture".
- N synthetic circular ROIs overlaid on the image, count adjustable live.
- Rolling capture FPS and per-frame ROI-extraction time (bounding-box-cropped
  mask, not a full-image mask - see the note above RoiMasks for why that
  distinction matters).
- Optional concurrent disk-write load (a dedicated writer thread + queue, same
  separation the real architecture plan calls for in §8) - to check that saving
  never slows capture/ROI-extraction down, not just assume it by design intent.
- A "Run timed benchmark" button that runs a fixed-duration test and prints a
  summary formatted to paste into the architecture doc's Phase 0 results.

Deliberately NOT built as a smaller version of the real app: no experiment
control, no illumination, no HDF5, no persisted ROIs. Only what's needed to
answer the throughput question.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg

from pypylon import pylon


# ── Camera capture thread ───────────────────────────────────────────────────
#
# Runs pypylon's blocking RetrieveResult() loop off the GUI thread - the same
# separation the real app's acquisition pipeline will need (§8 of the
# architecture plan). frameReady carries the raw ndarray and the capture
# timestamp; ROI extraction happens on the GUI thread for this spike (single-
# threaded on purpose first - see whether that alone sustains the target
# before building a separate processing thread, per the architecture plan's
# "don't build the more complex version speculatively" note).

class CameraGrabThread(QThread):
    frameReady = pyqtSignal(object, float)
    error = pyqtSignal(str)

    def __init__(self, pixel_format: str = "Mono8", binning: int = 1, exposure_us: float | None = None) -> None:
        super().__init__()
        self._pixel_format = pixel_format
        self._binning = binning
        self._exposure_us = exposure_us
        self._running = False
        self.cam: pylon.InstantCamera | None = None

    def run(self) -> None:
        try:
            tlf = pylon.TlFactory.GetInstance()
            devices = tlf.EnumerateDevices()
            if not devices:
                self.error.emit("No Basler camera found.")
                return
            self.cam = pylon.InstantCamera(tlf.CreateDevice(devices[0]))
            self.cam.Open()
            self.cam.PixelFormat.SetValue(self._pixel_format)
            # Binning reduces resolution while keeping the full field of view
            # (combines NxN sensor pixels into one output pixel) - unlike
            # Width/Height/Offset cropping, which keeps full pixel resolution
            # but narrows the FOV. Average mode (not Sum) keeps binned pixel
            # values in roughly the same intensity range as unbinned, which
            # matters once this feeds into intensity-ratio/absorbance math.
            self.cam.BinningHorizontal.SetValue(self._binning)
            self.cam.BinningVertical.SetValue(self._binning)
            try:
                self.cam.BinningHorizontalMode.SetValue("Average")
                self.cam.BinningVerticalMode.SetValue("Average")
            except Exception:
                pass  # older/other models may not expose the mode selector
            self.cam.Width.SetValue(self.cam.Width.Max)
            self.cam.Height.SetValue(self.cam.Height.Max)
            if self._exposure_us is not None:
                self.cam.ExposureTime.SetValue(self._exposure_us)
            self.cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
            self._running = True
            while self._running:
                result = self.cam.RetrieveResult(2000, pylon.TimeoutHandling_ThrowException)
                if result.GrabSucceeded():
                    frame = result.Array.copy()  # copy out of pylon's internal buffer before releasing
                    self.frameReady.emit(frame, time.perf_counter())
                result.Release()
        except Exception as exc:  # noqa: BLE001 - surface any pylon error to the UI
            self.error.emit(str(exc))
        finally:
            try:
                if self.cam is not None and self.cam.IsGrabbing():
                    self.cam.StopGrabbing()
                if self.cam is not None and self.cam.IsOpen():
                    self.cam.Close()
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        self.wait(3000)


# ── Save writer thread ──────────────────────────────────────────────────────
#
# Plain threading.Thread + queue.Queue, deliberately NOT a QThread - matches
# the real architecture plan's §8 recommendation (same-process thread/queue,
# not multiprocessing, no Qt signal overhead needed on this path since nothing
# here touches widgets). The whole point being tested: does pushing frames
# here and writing them to disk ever slow down capture or ROI extraction on
# the other threads? If yes, the queue depth will grow without bound - that's
# the signal to watch, not just "did fps drop" (fps could stay fine while a
# save backlog quietly grows, exactly the Lori SW bug this project started by
# auditing and fixing in a different codebase - see the build log entry for
# 2026-08-06).
#
# Disk usage is bounded by writing into a small rotating set of files instead
# of one ever-growing file - this is a throughput/backpressure test, not a
# test of how much data fits on disk.

_SCRATCH_DIR = Path(__file__).parent / "_disk_write_scratch"
_SCRATCH_FILE_COUNT = 20


class SaveWriterThread:
    def __init__(self) -> None:
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False
        self._frame_index = 0
        self._write_times_ms: deque[float] = deque(maxlen=200)
        self._max_queue_depth_seen = 0
        self._bytes_written = 0
        self._lock = threading.Lock()
        _SCRATCH_DIR.mkdir(exist_ok=True)

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)  # type: ignore[arg-type] - sentinel to unblock a waiting get()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def submit(self, frame: np.ndarray) -> None:
        """Called from the GUI thread's _on_frame - must stay cheap. queue.Queue.put()
        with no maxsize never blocks, which is the point: capture/display must never
        wait on the writer."""
        with self._lock:
            depth = self._queue.qsize()
            self._max_queue_depth_seen = max(self._max_queue_depth_seen, depth)
        self._queue.put(frame)

    def _run(self) -> None:
        while self._running:
            item = self._queue.get()
            if item is None:
                break
            path = _SCRATCH_DIR / f"frame_{self._frame_index % _SCRATCH_FILE_COUNT}.raw"
            self._frame_index += 1
            t0 = time.perf_counter()
            with open(path, "wb") as f:
                f.write(item.tobytes())
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            with self._lock:
                self._write_times_ms.append(elapsed_ms)
                self._bytes_written += item.nbytes

    @property
    def stats(self) -> tuple[int, float, float, int, int]:
        """(queued_now, avg_write_ms, max_write_ms, max_queue_depth_seen, mb_written)"""
        with self._lock:
            queued_now = self._queue.qsize()
            avg_ms = float(np.mean(self._write_times_ms)) if self._write_times_ms else 0.0
            max_ms = float(np.max(self._write_times_ms)) if self._write_times_ms else 0.0
            max_depth = self._max_queue_depth_seen
            mb = self._bytes_written // (1024 * 1024)
        return queued_now, avg_ms, max_ms, max_depth, mb


# ── ROI extraction primitive ─────────────────────────────────────────────────
#
# IMPORTANT: a full-image-sized boolean mask (image[full_size_mask]) is O(total
# image pixels) per ROI, not O(ROI area) - numpy has to scan every element of
# the mask to gather the True positions, regardless of how few of them there
# are. The first version of this script used that approach and measured
# ~27ms avg / ~44ms max for just 10 small ROIs on an 8.3MP frame - almost the
# entire frame period. Fixed here by cropping to each ROI's small bounding box
# first, then masking only that sub-array - O(ROI area) per ROI instead of
# O(image size). Worth carrying this lesson into the real app's
# processing/roi_extraction.py (§7 of the architecture plan) rather than
# reusing the naive full-mask version.

@dataclass
class RoiMasks:
    # Each entry: (y0, y1, x0, x1, local_mask) - local_mask has shape (y1-y0, x1-x0)
    boxes: list[tuple[int, int, int, int, np.ndarray]]


def build_grid_rois(height: int, width: int, count: int, radius_frac: float = 0.03) -> RoiMasks:
    """N circular ROIs laid out in a grid, cached as (bounding box, local
    mask) pairs - rebuilt only when count/image-size changes, not per frame
    (the whole point being tested is per-frame *extraction* cost)."""
    if count <= 0:
        return RoiMasks(boxes=[])
    cols = int(np.ceil(np.sqrt(count)))
    rows = int(np.ceil(count / cols))
    radius = max(1, int(min(height, width) * radius_frac))
    yy, xx = np.indices((radius * 2 + 1, radius * 2 + 1))
    template_mask = (yy - radius) ** 2 + (xx - radius) ** 2 <= radius * radius
    boxes: list[tuple[int, int, int, int, np.ndarray]] = []
    for i in range(count):
        r, c = divmod(i, cols)
        cy = int((r + 1) * height / (rows + 1))
        cx = int((c + 1) * width / (cols + 1))
        y0, y1 = max(0, cy - radius), min(height, cy + radius + 1)
        x0, x1 = max(0, cx - radius), min(width, cx + radius + 1)
        ly0 = y0 - (cy - radius)
        lx0 = x0 - (cx - radius)
        local_mask = template_mask[ly0:ly0 + (y1 - y0), lx0:lx0 + (x1 - x0)]
        boxes.append((y0, y1, x0, x1, local_mask))
    return RoiMasks(boxes=boxes)


def extract_roi_means(image: np.ndarray, roi_masks: RoiMasks) -> np.ndarray:
    if not roi_masks.boxes:
        return np.empty(0)
    means = np.empty(len(roi_masks.boxes))
    for i, (y0, y1, x0, x1, local_mask) in enumerate(roi_masks.boxes):
        means[i] = image[y0:y1, x0:x1][local_mask].mean()
    return means


# ── Rolling stats ────────────────────────────────────────────────────────────

class RollingStats:
    def __init__(self, window_s: float = 3.0) -> None:
        self._window_s = window_s
        self._frame_times: deque[float] = deque()
        self._roi_times_ms: deque[float] = deque()

    def add_frame(self, t: float) -> None:
        self._frame_times.append(t)
        cutoff = t - self._window_s
        while self._frame_times and self._frame_times[0] < cutoff:
            self._frame_times.popleft()

    def add_roi_time_ms(self, ms: float) -> None:
        self._roi_times_ms.append(ms)
        if len(self._roi_times_ms) > 200:
            self._roi_times_ms.popleft()

    @property
    def fps(self) -> float:
        if len(self._frame_times) < 2:
            return 0.0
        span = self._frame_times[-1] - self._frame_times[0]
        return (len(self._frame_times) - 1) / span if span > 0 else 0.0

    @property
    def roi_time_ms_avg(self) -> float:
        return float(np.mean(self._roi_times_ms)) if self._roi_times_ms else 0.0

    @property
    def roi_time_ms_max(self) -> float:
        return float(np.max(self._roi_times_ms)) if self._roi_times_ms else 0.0


# ── Main window ──────────────────────────────────────────────────────────────

class BenchmarkWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LSPRi acq — Phase 0 throughput spike")
        self.resize(1100, 800)

        self._stats = RollingStats()
        self._roi_masks = RoiMasks(boxes=[])
        self._grab_thread: CameraGrabThread | None = None
        self._save_writer: SaveWriterThread | None = None
        self._benchmark_until: float | None = None
        self._benchmark_frame_count = 0
        self._benchmark_drop_count = 0
        self._last_frame_t: float | None = None
        # Reference period only, not a pass/fail target - see module docstring.
        self._expected_period_s = 1.0 / 16.0

        root = QWidget()
        layout = QVBoxLayout(root)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Pixel format:"))
        self.pixel_format_combo = QComboBox()
        self.pixel_format_combo.addItems(["Mono8", "Mono10", "Mono12"])
        controls.addWidget(self.pixel_format_combo)

        controls.addWidget(QLabel("Binning:"))
        self.binning_combo = QComboBox()
        # Label shows the resulting resolution for THIS camera (3840x2160 native) -
        # if you run this against a different camera, the labels will be wrong
        # relative to its native size, but the binning factor applied is correct.
        self.binning_combo.addItems(["1x1 (3840x2160, ~8.3MP)", "2x2 (1920x1080, ~2.1MP)", "4x4 (960x540, ~0.5MP)"])
        controls.addWidget(self.binning_combo)

        controls.addWidget(QLabel("Exposure (us):"))
        self.exposure_spin = QSpinBox()
        self.exposure_spin.setRange(12, 100000)
        self.exposure_spin.setValue(1146)
        self.exposure_spin.setSingleStep(100)
        controls.addWidget(self.exposure_spin)

        controls.addWidget(QLabel("ROI count:"))
        self.roi_count_spin = QSpinBox()
        self.roi_count_spin.setRange(0, 500)
        self.roi_count_spin.setValue(10)
        self.roi_count_spin.valueChanged.connect(self._rebuild_rois)
        controls.addWidget(self.roi_count_spin)

        self.show_rois_check = QCheckBox("Show ROI overlay")
        self.show_rois_check.setChecked(True)
        controls.addWidget(self.show_rois_check)

        self.disk_write_check = QCheckBox("Also write frames to disk")
        controls.addWidget(self.disk_write_check)

        self.start_button = QPushButton("Start capture")
        self.start_button.clicked.connect(self._toggle_capture)
        controls.addWidget(self.start_button)

        self.benchmark_button = QPushButton("Run 30s timed benchmark")
        self.benchmark_button.clicked.connect(self._start_benchmark)
        self.benchmark_button.setEnabled(False)
        controls.addWidget(self.benchmark_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.image_view = pg.ImageView()
        self.image_view.ui.roiBtn.hide()
        self.image_view.ui.menuBtn.hide()
        layout.addWidget(self.image_view, 1)

        self._roi_overlay_items: list[pg.CircleROI] = []

        self.stats_label = QLabel("Not capturing.")
        self.stats_label.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.stats_label)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        layout.addWidget(self.log)

        self.setCentralWidget(root)
        self._first_frame_shown = False

    # -- capture lifecycle ----------------------------------------------------

    def _toggle_capture(self) -> None:
        if self._grab_thread is None:
            self._start_capture()
        else:
            self._stop_capture()

    def _start_capture(self) -> None:
        self._first_frame_shown = False
        self._roi_masks = RoiMasks(boxes=[])  # force rebuild - image size may have changed (binning)
        pf = self.pixel_format_combo.currentText()
        binning = int(self.binning_combo.currentText().split("x")[0])
        exposure_us = float(self.exposure_spin.value())
        self._grab_thread = CameraGrabThread(pixel_format=pf, binning=binning, exposure_us=exposure_us)
        self._grab_thread.frameReady.connect(self._on_frame)
        self._grab_thread.error.connect(self._on_error)
        self._grab_thread.start()
        self.start_button.setText("Stop capture")
        if self.disk_write_check.isChecked():
            self._save_writer = SaveWriterThread()
            self._save_writer.start()
            self._log(f"Disk-write load enabled - writing into {_SCRATCH_DIR} "
                      f"(rotating {_SCRATCH_FILE_COUNT} files, bounded disk use).")
        else:
            self._save_writer = None
        self._log(f"Starting: pixel_format={pf}, binning={binning}x{binning}, exposure={exposure_us:.0f}us "
                   "(settings from the controls above apply now - change them and Stop/Start again to re-apply).")
        self.benchmark_button.setEnabled(True)

    def _stop_capture(self) -> None:
        if self._grab_thread is not None:
            self._grab_thread.frameReady.disconnect(self._on_frame)
            self._grab_thread.stop()
            self._grab_thread = None
        if self._save_writer is not None:
            self._save_writer.stop()
            self._save_writer = None
        self.start_button.setText("Start capture")
        self.benchmark_button.setEnabled(False)
        self.stats_label.setText("Stopped.")

    def _on_error(self, message: str) -> None:
        self._log(f"ERROR: {message}")
        self._stop_capture()

    # -- ROI overlay ------------------------------------------------------------

    def _rebuild_rois(self) -> None:
        self._roi_masks = RoiMasks(boxes=[])  # rebuilt lazily on next frame once we know image size

    def _ensure_rois(self, height: int, width: int) -> None:
        count = self.roi_count_spin.value()
        if len(self._roi_masks.boxes) != count:
            self._roi_masks = build_grid_rois(height, width, count)
            self._redraw_roi_overlay(height, width)

    def _redraw_roi_overlay(self, height: int, width: int) -> None:
        view = self.image_view.getView()
        for item in self._roi_overlay_items:
            view.removeItem(item)
        self._roi_overlay_items.clear()
        if not self.show_rois_check.isChecked():
            return
        count = self.roi_count_spin.value()
        radius = int(min(height, width) * 0.03)
        cols = int(np.ceil(np.sqrt(count))) if count else 1
        rows = int(np.ceil(count / cols)) if count else 1
        for i in range(count):
            r, c = divmod(i, cols)
            cy = int((r + 1) * height / (rows + 1))
            cx = int((c + 1) * width / (cols + 1))
            circle = pg.CircleROI([cx - radius, cy - radius], [radius * 2, radius * 2], movable=False, pen=pg.mkPen("r", width=1))
            circle.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            view.addItem(circle)
            self._roi_overlay_items.append(circle)

    # -- per-frame handling -------------------------------------------------

    def _on_frame(self, frame: np.ndarray, capture_t: float) -> None:
        height, width = frame.shape[:2]
        self._ensure_rois(height, width)

        t0 = time.perf_counter()
        roi_means = extract_roi_means(frame, self._roi_masks)
        roi_ms = (time.perf_counter() - t0) * 1000.0

        submit_ms = 0.0
        if self._save_writer is not None:
            t1 = time.perf_counter()
            self._save_writer.submit(frame)
            submit_ms = (time.perf_counter() - t1) * 1000.0

        self._stats.add_frame(capture_t)
        self._stats.add_roi_time_ms(roi_ms)

        if self._last_frame_t is not None:
            gap = capture_t - self._last_frame_t
            if self._benchmark_until is not None and gap > self._expected_period_s * 1.5:
                self._benchmark_drop_count += 1
        self._last_frame_t = capture_t

        if self._benchmark_until is not None:
            self._benchmark_frame_count += 1
            if capture_t >= self._benchmark_until:
                self._finish_benchmark()

        self.image_view.setImage(frame.T, autoLevels=not self._first_frame_shown, autoRange=not self._first_frame_shown)
        self._first_frame_shown = True

        save_bit = ""
        if self._save_writer is not None:
            queued, avg_write_ms, max_write_ms, max_depth, mb = self._save_writer.stats
            save_bit = (
                f"   |   save: enqueue {submit_ms:.3f}ms, queue depth now={queued} (max seen={max_depth}), "
                f"write avg={avg_write_ms:.2f}ms max={max_write_ms:.2f}ms, {mb}MB written"
            )

        self.stats_label.setText(
            f"FPS (3s rolling): {self._stats.fps:6.2f}   |   "
            f"ROI extraction ({len(self._roi_masks.boxes)} ROIs): "
            f"avg {self._stats.roi_time_ms_avg:6.3f} ms, max {self._stats.roi_time_ms_max:6.3f} ms   |   "
            f"frame: {width}x{height}, dtype={frame.dtype}, last ROI mean[0]={roi_means[0] if len(roi_means) else float('nan'):.1f}"
            f"{save_bit}"
        )

    # -- timed benchmark ------------------------------------------------------

    def _start_benchmark(self) -> None:
        self._benchmark_frame_count = 0
        self._benchmark_drop_count = 0
        self._stats = RollingStats(window_s=30.0)
        self._benchmark_until = time.perf_counter() + 30.0
        self._log("Timed benchmark started (30s)...")
        self.benchmark_button.setEnabled(False)

    def _finish_benchmark(self) -> None:
        duration = 30.0
        fps = self._benchmark_frame_count / duration
        roi_count = len(self._roi_masks.boxes)
        pf = self.pixel_format_combo.currentText()
        binning = self.binning_combo.currentText()
        exposure_us = self.exposure_spin.value()
        save_line = "- Disk-write load: off\n"
        if self._save_writer is not None:
            _queued, avg_write_ms, max_write_ms, max_depth, mb = self._save_writer.stats
            save_line = (
                f"- Disk-write load: ON - write avg {avg_write_ms:.2f}ms, max {max_write_ms:.2f}ms, "
                f"max queue depth seen during run: {max_depth} (0 or near-0 = writer always kept up; "
                f"growing/high = it fell behind and would need a bigger safety margin or faster storage), "
                f"{mb}MB written to {_SCRATCH_DIR}\n"
            )
        summary = (
            f"### Phase 0 results — {time.strftime('%Y-%m-%d %H:%M')}\n"
            f"- Camera: Basler a2A3840-45umBAS, pixel format {pf}, binning {binning}, "
            f"exposure {exposure_us}us\n"
            f"- ROI count: {roi_count}\n"
            f"- Duration: {duration:.0f}s, frames captured: {self._benchmark_frame_count}\n"
            f"- Achieved capture rate: {fps:.2f} fps (no fixed target - as fast as achievable; "
            f"16Hz shown only as a historical reference point)\n"
            f"- Frames arriving >1.5x the 16Hz reference period late: {self._benchmark_drop_count}\n"
            f"- ROI extraction time (bounding-box-cropped, see module docstring): "
            f"avg {self._stats.roi_time_ms_avg:.3f} ms, "
            f"max {self._stats.roi_time_ms_max:.3f} ms, for {roi_count} ROIs\n"
            f"{save_line}"
        )
        self._log(summary)
        self._benchmark_until = None
        self.benchmark_button.setEnabled(True)

    def _log(self, text: str) -> None:
        self.log.append(text)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._stop_capture()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = BenchmarkWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
