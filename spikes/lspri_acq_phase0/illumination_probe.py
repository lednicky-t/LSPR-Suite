"""LSPRimaging Acquisition — Phase 0 spike: illumination source probe.

Not part of any app. General-purpose bench-test tool for a tunable
illumination source (currently: the VariSpec LCTF) combined with the Ocean
Optics spectrometer already used by sLSPR acq, optically coupled to the
filter's output. See
docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md,
§6.2, which plans to hardcode the future `IlluminationSource.settle_time_ms()`
to the manual's nominal spec (50ms VIS / 150ms NIR). This tool exists to
check that against both (a) the filter's own protocol-level busy-check
signal and (b) an independent optical measurement, and to record real
passband spectra for characterization. The manual lives at
docs/manuals/cri-varispec-lctf-manual.pdf next to this file.

Run: .venv\\Scripts\\python.exe spikes\\lspri_acq_phase0\\illumination_probe.py

Six tabs:
1. Settle-time sweep (protocol) — the original busy-check-based sweep across
   a wavelength range, up/down for hysteresis, repeats for jitter.
2. Palette vs direct — the manual's "define a palette, then jump between
   entries with P" mode is documented as marginally faster than plain W
   commands ("perhaps one or two milliseconds"). This runs the same
   wavelength list both ways back-to-back so the claim can be checked
   directly against real hardware and real measurement noise.
3. Optical transition capture — parks the filter, then watches the
   spectrometer signal at the source and destination wavelengths through a
   real wavelength change, to see the physical transition rather than
   inferring it from the busy-check character. See "Why the optical
   measurement isn't interleaved with the busy-check one" below
   (TransitionCaptureWorker's docstring) for the methodology.
4. Optical spectral sweep — records and auto-saves an actual spectrum at
   each requested wavelength (configurable integration time / averages /
   dark & nonlinearity correction), for passband characterization rather
   than timing. Can capture a dark/background spectrum (lamp off) and
   subtract it for live display, and always saves that dark spectrum as the
   first row of the sweep's CSV if one was captured - the saved per-point
   rows themselves stay raw/unsubtracted (never overwrite raw data), so
   dark subtraction is always redoable later rather than baked in.
5. Live spectrometer view — a free-running acquisition (no LCTF involved)
   for setting up integration time/averages/corrections while watching the
   signal update, independent of any timed test. Settings can be changed
   while it's running; the next acquisition picks them up.
6. Optical transition batch sweep — walks a wavelength range step by step
   (e.g. every 5 or 10nm), optically measuring EVERY consecutive transition
   via the same measure_transition() logic as tab 3. Built to separate two
   questions a single from/to test can't: does settle time depend on WHERE
   in the spectrum you are, or on the SIZE of the jump? One run with a fixed
   step answers the first (is the plot flat vs. wavelength); comparing two
   runs at different step sizes answers the second. Saves a one-row-per-
   transition summary CSV by default; full per-transition frame data is
   opt-in (a fine-step full-range batch would otherwise produce a lot of
   multi-MB files).

Shared across tabs 4 and 5: an optional "limit displayed wavelength range"
control (next to the spectrometer connection bar) that slices what's shown
on the wavelength-vs-intensity plots to a user-chosen sub-range - useful for
zooming into a passband without full-spectrum dynamic range flattening the
view. This only affects the plot, never the saved data.

Protocol notes discovered by testing against the real filter (VIS model,
400-720nm, on COM17 here) that are NOT obvious from the manual text alone:

- The filter echoes every character it receives before replying, including
  the busy-check `!` character itself. `VariSpecClient.poll_busy_char`
  reads byte-by-byte until it actually sees `<` or `>`, ignoring everything
  else in the stream (echoes, stray bytes from an overlapping command's
  echo) - a naive read(1)/read(2) can silently misread the echo as the
  status byte.

- MEASUREMENT RESOLUTION CAVEAT: each busy-check round trip over the
  USB-virtual-COM link has its own latency floor - on this machine/cable it
  measured a very consistent ~16ms per poll, the classic FTDI VCP driver
  default "Latency Timer" value, not real serial/USB transfer time. Every
  jump size tried (a 2nm nudge up to the full 320nm range) settled in
  ~32ms (2 polls) via busy-check - i.e. the protocol-level measurement was
  floor-limited, not actually resolving real settle-time-vs-jump-size
  behavior. `MainWindow` reports the measured idle round-trip latency in
  its LCTF status line at connect time. Fix: Device Manager > Ports >
  (the VariSpec's COM port) > Port Settings > Advanced... > Latency Timer
  (msec) -> 1. The spectrometer path in tabs 3/4 does not have this
  limitation (achieved ~300+ fps at 1ms integration time in testing), which
  is the whole reason tab 3 exists: it's a much higher-resolution way to
  answer "how long does this really take" than the busy-check character
  alone can currently provide on this machine.

Deliberately NOT built as a smaller version of the real app: no
IlluminationSource ABC, no camera, no HDF5. Only what's needed to answer
the settle-time and passband-characterization questions, same spirit as
benchmark_ui.py's camera-only Phase 0 spike.
"""

from __future__ import annotations

import csv
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import serial
import serial.tools.list_ports
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg

from lspr_app.device.ocean import OceanSpectrometer
from lspr_app.domain.models import AcquisitionSettings

RESULTS_DIR = Path(__file__).parent / "illumination_probe_results"


# ── VariSpec serial protocol client ─────────────────────────────────────────
#
# Command set and formats are from docs/manuals/cri-varispec-lctf-manual.pdf,
# Chapter 3 ("Controlling VariSpec Filters with Direct Serial Commands").
# Terminator is always <c/r> (b"\r", not b"\r\n"). Brief format (B 1) is used
# after connect to shorten replies, per the manual's own recommendation.

class VariSpecError(RuntimeError):
    """Raised when the filter reports a non-zero error code (Table 5 in the manual)."""


ERROR_CODE_MEANINGS = {
    -1: "Unparseable R ? reply (not in the manual's error table)",
    0: "No errors pending",
    1: "Syntax error",
    2: "Attempt was made to set a read-only parameter",
    3: "Exercise command issued with illegal argument",
    4: "Attempt to set wavelength/palette while uninitialized",
    5: "Initialize command issued with illegal argument",
    6: "Mode error",
    7: "Mode command issued with illegal argument",
    8: "Error calculating liquid crystal drive levels (internal)",
    9: "Palette not defined",
    10: "Palette not prepared (internal)",
    11: "Palette element out of range",
    12: "Wavelength out of range",
    13: "Liquid crystal drive level out of range (internal)",
    14: "Jump wavelength step too large",
    17: "Go (TTL sync) command issued with illegal argument",
}


@dataclass(slots=True)
class VariSpecInfo:
    firmware_rev: int
    min_nm: float
    max_nm: float
    serial_number: str


@dataclass(slots=True)
class SettleMeasurement:
    index: int
    direction: str  # "up" or "down"
    repeat: int
    requested_nm: float
    confirmed_nm: float
    settle_ms: float
    poll_count: int
    temperature_c: float
    error_code: int


@dataclass(slots=True)
class PaletteCompareMeasurement:
    index: int
    repeat: int
    method: str  # "direct" (W) or "palette" (P)
    requested_nm: float
    confirmed_nm: float
    settle_ms: float
    poll_count: int
    error_code: int


@dataclass(slots=True)
class TransitionFrame:
    t_abs: float  # raw time.perf_counter() at the moment this spectrum was captured
    intensities: np.ndarray


@dataclass(slots=True)
class SpectralSweepPoint:
    index: int
    requested_nm: float
    confirmed_nm: float
    settle_ms: float
    temperature_c: float
    wavelengths_nm: np.ndarray
    intensities: np.ndarray


class VariSpecClient:
    def __init__(self, port: str, baudrate: int = 115200) -> None:
        self._ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        time.sleep(0.2)  # let the virtual COM port settle before first write
        self._ser.reset_input_buffer()

    def close(self) -> None:
        self._ser.close()

    # -- low-level framing -----------------------------------------------

    def _write(self, data: bytes) -> None:
        self._ser.write(data)

    def command(self, cmd: str, timeout_s: float = 0.5) -> str:
        """Send a <cr>-terminated command and return the reply text, if any.

        The filter always echoes the command back first, then - only for
        queries (a "?" argument) or in Auto-Confirm mode - sends its own
        <cr>-terminated reply. Plain commands (e.g. "B 1") only echo, so an
        empty string is a normal return value for those.
        """
        self._write((cmd + "\r").encode("ascii"))
        deadline = time.perf_counter() + timeout_s
        buf = bytearray()
        terminators_seen = 0
        while terminators_seen < 2 and time.perf_counter() < deadline:
            chunk = self._ser.read(1)
            if not chunk:
                continue
            buf += chunk
            if chunk == b"\r":
                terminators_seen += 1
        parts = buf.decode("ascii", errors="replace").split("\r")
        return parts[1].strip() if len(parts) > 1 and parts[1].strip() else ""

    def poll_busy_char(self, timeout_s: float = 0.2) -> str | None:
        """Send the busy-check character and return '<' (busy) or '>' (idle).

        Scans incoming bytes one at a time for an actual '<' or '>', skipping
        the echoed '!' and any other stray bytes still in flight. Returns
        None if no definitive status byte arrived within timeout_s (caller
        should treat that as "still busy" and poll again).
        """
        self._write(b"!")
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            chunk = self._ser.read(1)
            if chunk in (b"<", b">"):
                return chunk.decode()
        return None

    def _wait_until_idle(self, t0: float, busy_timeout_s: float) -> tuple[float, int]:
        polls = 0
        deadline = t0 + busy_timeout_s
        while time.perf_counter() < deadline:
            status = self.poll_busy_char(timeout_s=0.15)
            polls += 1
            if status == ">":
                return (time.perf_counter() - t0) * 1000.0, polls
        raise TimeoutError(f"VariSpec did not report idle within {busy_timeout_s}s")

    # -- higher-level operations -------------------------------------------

    def set_brief_mode(self) -> None:
        self.command("B 1")

    def get_info(self) -> VariSpecInfo:
        reply = self.command("V ?", timeout_s=1.0)
        tokens = reply.split()
        # Brief-mode reply: "<rev> <min_nm> <max_nm> <serial>"
        return VariSpecInfo(
            firmware_rev=int(tokens[0]),
            min_nm=float(tokens[1]),
            max_nm=float(tokens[2]),
            serial_number=tokens[3],
        )

    def get_wavelength(self) -> float:
        reply = self.command("W ?")
        try:
            return float(reply)
        except ValueError:
            # The manual documents "*" as a legal W ? reply: it "appears
            # when the filter is set out of bounds, when initialization has
            # not occurred, or when the filter cannot tune to a specified
            # wavelength (not necessarily out of range)". Hit for real when
            # a batch sweep's range wasn't clamped to this filter's actual
            # min/max and requested an out-of-range wavelength - crashed the
            # whole batch with an uncaught ValueError before this fix.
            raise VariSpecError(
                f"W ? returned non-numeric reply {reply!r} - the filter is likely in an error "
                f"state (e.g. the last commanded wavelength was out of range). Check R ? / clear_error()."
            ) from None

    def get_temperature_c(self) -> float:
        reply = self.command("Y ?")
        try:
            return float(reply)
        except ValueError:
            raise VariSpecError(f"Y ? returned non-numeric reply {reply!r}") from None

    def get_error_code(self) -> int:
        reply = self.command("R ?")
        if not reply:
            return 0
        try:
            return int(reply)
        except ValueError:
            return -1  # unparseable reply - treat as "some error", let the caller decide what to do

    def clear_error(self) -> None:
        self.command("R 1")

    def measure_idle_roundtrip_ms(self, samples: int = 8) -> list[float]:
        """Round-trip time of a busy-check while definitely idle - the
        measurement resolution floor described in the module docstring."""
        times: list[float] = []
        for _ in range(samples):
            t0 = time.perf_counter()
            self.poll_busy_char(timeout_s=1.0)
            times.append((time.perf_counter() - t0) * 1000.0)
        return times

    def settle_time_ms(self, wavelength_nm: float, busy_timeout_s: float = 3.0) -> tuple[float, int]:
        """Send W <wavelength_nm> and poll the busy-check character until the
        filter reports idle. Returns (elapsed_ms, poll_count)."""
        t0 = time.perf_counter()
        self._write(f"W {wavelength_nm:.3f}\r".encode("ascii"))
        return self._wait_until_idle(t0, busy_timeout_s)

    def fire_wavelength(self, wavelength_nm: float) -> None:
        """Write W <nm> and return immediately - true fire-and-forget, no
        read at all, so this adds essentially zero latency between the
        caller's timestamp and the actual write() syscall. Used by
        TransitionCaptureWorker to mark t=0 as precisely as possible right
        before its timing-critical spectrometer capture loop.

        IMPORTANT: this leaves the command's echo unread in the RX buffer.
        Call drain_echo() before using this client for anything else (e.g.
        get_wavelength()) or the next command()'s echo/reply framing will
        desync and silently return the wrong text - this happened during
        testing (get_wavelength() returned "W ?" instead of a number).
        Draining was originally done inside this method, but that read
        turned out to cost ~15-20ms on this hardware (the same FTDI
        latency-timer floor documented at the top of this file for
        busy-check polls) - stamping t=0 *after* that hidden delay was
        quietly inflating every reported optical settle time by that same
        amount. Deferring the drain until after the capture loop (when
        nothing else needs this port) removes that error entirely.
        """
        self._write(f"W {wavelength_nm:.3f}\r".encode("ascii"))

    def drain_echo(self, timeout_s: float = 0.5) -> None:
        """Discards bytes through the next '\\r' - cleans up after
        fire_wavelength() before this port is used for anything else."""
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            if self._ser.read(1) == b"\r":
                return

    def clear_palette(self) -> None:
        self.command("C 1")

    def define_palette_element(self, wavelength_nm: float) -> None:
        """Appends a new palette element; palette elements are 0-indexed in
        the order they're defined (see manual, "Define Palette Element")."""
        self.command(f"D {wavelength_nm:.3f}")

    def palette_settle_time_ms(self, index: int, busy_timeout_s: float = 3.0) -> tuple[float, int]:
        t0 = time.perf_counter()
        self._write(f"P {index}\r".encode("ascii"))
        return self._wait_until_idle(t0, busy_timeout_s)


def build_wavelength_list(start: float, stop: float, step: float) -> list[float]:
    if stop < start:
        start, stop = stop, start
    wavelengths: list[float] = []
    w = start
    while w <= stop + 1e-9:
        wavelengths.append(round(min(w, stop), 3))
        w += step
    return wavelengths


def estimate_rise_time_ms(t_ms: np.ndarray, values: np.ndarray, threshold_fraction: float = 0.9) -> float | None:
    """First post-command (t_ms >= 0) time at which `values` crosses
    threshold_fraction of the way from its pre-command baseline to its
    late-post-command plateau. A simple, honest estimate (no debounce) -
    for the real answer, look at the plot or the saved CSV directly."""
    pre_mask = t_ms < 0
    post_mask = ~pre_mask
    if not pre_mask.any() or not post_mask.any():
        return None
    baseline = float(np.mean(values[pre_mask]))
    post_values = values[post_mask]
    tail = post_values[-max(1, len(post_values) // 5):]
    final = float(np.mean(tail))
    delta = final - baseline
    if abs(delta) < 1e-9:
        return None
    threshold = baseline + threshold_fraction * delta
    post_t = t_ms[post_mask]
    crossed = post_values >= threshold if delta > 0 else post_values <= threshold
    if not crossed.any():
        return None
    return float(post_t[np.argmax(crossed)])


@dataclass(slots=True)
class TransitionMeasurement:
    from_nm: float
    to_nm: float
    confirmed_nm: float
    busy_check_settle_ms: float
    busy_check_polls: int
    optical_settle_ms_90pct: float | None
    achieved_fps: float
    temperature_c: float
    error_code: int
    wavelengths_nm: np.ndarray
    t_ms: np.ndarray
    intensities: np.ndarray  # shape (n_frames, n_pixels), empty if nothing was captured


def measure_transition(
    lctf: VariSpecClient,
    spectrometer: OceanSpectrometer,
    settings: AcquisitionSettings,
    from_nm: float,
    to_nm: float,
    pre_roll_s: float,
    post_roll_s: float,
    stop_check: Callable[[], bool] | None = None,
) -> TransitionMeasurement:
    """Parks at from_nm, measures the busy-check settle time for the
    from->to jump, then repeats the identical jump while capturing spectra
    optically. Shared by the single-pair transition-capture tab (3) and the
    batch sweep tab (6) - factored out so the two tabs can't drift apart on
    this logic, which already had one subtle timing bug fixed in it once
    (see fire_wavelength()'s docstring for the fire-and-forget/echo-drain
    story - that fix only has to be gotten right in one place now).

    Why busy-check and optical aren't interleaved: both a busy-check round
    trip and one spectrometer acquisition take a few milliseconds each on
    this hardware, so reading LCTF status inside the tight spectrometer loop
    would serialize the two devices' latencies and degrade exactly the time
    resolution this measurement exists to get. Measuring busy-check as an
    immediately-preceding repeat of the identical jump is simpler and
    doesn't cost the optical measurement any time resolution, at the cost of
    not being truly simultaneous.

    stop_check, if given, is polled during the pre/post-roll capture loops
    so a batch run can be interrupted between (or mid-) transitions.
    """
    lctf.settle_time_ms(from_nm)
    busy_ms, busy_polls = lctf.settle_time_ms(to_nm)
    lctf.settle_time_ms(from_nm)  # back to start for the optical repeat

    frames: list[TransitionFrame] = []
    wavelengths_nm: np.ndarray | None = None

    pre_start = time.perf_counter()
    while time.perf_counter() - pre_start < pre_roll_s:
        if stop_check is not None and stop_check():
            break
        spectrum = spectrometer.acquire_spectrum(settings)
        if wavelengths_nm is None:
            wavelengths_nm = spectrum.wavelengths_nm
        frames.append(TransitionFrame(t_abs=time.perf_counter(), intensities=spectrum.values))

    command_sent_at = time.perf_counter()
    lctf.fire_wavelength(to_nm)

    while time.perf_counter() - command_sent_at < post_roll_s:
        if stop_check is not None and stop_check():
            break
        spectrum = spectrometer.acquire_spectrum(settings)
        if wavelengths_nm is None:
            wavelengths_nm = spectrum.wavelengths_nm
        frames.append(TransitionFrame(t_abs=time.perf_counter(), intensities=spectrum.values))

    lctf.drain_echo()  # the W command's echo, deferred from fire_wavelength() - see its docstring
    # get_wavelength()/get_temperature_c() can raise VariSpecError if the
    # filter replies with "*" instead of a number (documented in the manual
    # as an out-of-range/error-state reply) - caught here so one bad
    # wavelength (e.g. a batch sweep range not quite clamped to this
    # filter's real min/max) reports as an error on this single point
    # instead of aborting an entire multi-minute batch run.
    try:
        confirmed_nm = lctf.get_wavelength()
    except VariSpecError:
        confirmed_nm = float("nan")
    error_code = lctf.get_error_code()
    if error_code != 0:
        lctf.clear_error()
    try:
        temperature_c = lctf.get_temperature_c()
    except VariSpecError:
        temperature_c = float("nan")

    if not frames or wavelengths_nm is None:
        return TransitionMeasurement(
            from_nm=from_nm, to_nm=to_nm, confirmed_nm=confirmed_nm,
            busy_check_settle_ms=busy_ms, busy_check_polls=busy_polls,
            optical_settle_ms_90pct=None, achieved_fps=0.0, temperature_c=temperature_c,
            error_code=error_code, wavelengths_nm=np.array([]), t_ms=np.array([]),
            intensities=np.empty((0, 0)),
        )

    t_ms = (np.array([f.t_abs for f in frames]) - command_sent_at) * 1000.0
    intensities = np.array([f.intensities for f in frames])  # shape (n_frames, n_pixels)
    to_pixel = int(np.argmin(np.abs(wavelengths_nm - to_nm)))
    optical_ms = estimate_rise_time_ms(t_ms, intensities[:, to_pixel])
    n_post = int(np.sum(t_ms >= 0))
    fps = n_post / post_roll_s if post_roll_s > 0 else 0.0

    return TransitionMeasurement(
        from_nm=from_nm, to_nm=to_nm, confirmed_nm=confirmed_nm,
        busy_check_settle_ms=busy_ms, busy_check_polls=busy_polls,
        optical_settle_ms_90pct=optical_ms, achieved_fps=fps, temperature_c=temperature_c,
        error_code=error_code, wavelengths_nm=wavelengths_nm, t_ms=t_ms, intensities=intensities,
    )


# ── Workers ──────────────────────────────────────────────────────────────
#
# Each worker owns exclusive use of the LCTF serial port (and, for the two
# spectrometer-based workers, the spectrometer too) while running - see
# MainWindow._set_busy for the mutual-exclusion mechanism that guarantees
# only one worker touches these devices at a time.

class SweepWorker(QThread):
    """Tab 1: busy-check-based settle-time sweep across a wavelength range."""

    pointReady = pyqtSignal(object)  # SettleMeasurement
    logMessage = pyqtSignal(str)
    finished_ok = pyqtSignal(int, int)  # (points_measured, error_count)
    failed = pyqtSignal(str)

    def __init__(
        self,
        client: VariSpecClient,
        wavelengths_up: list[float],
        both_directions: bool,
        repeats: int,
        return_to_nm: float,
    ) -> None:
        super().__init__()
        self._client = client
        self._wavelengths_up = wavelengths_up
        self._both_directions = both_directions
        self._repeats = repeats
        self._return_to_nm = return_to_nm
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            index = 0
            error_count = 0
            passes: list[tuple[str, list[float]]] = [("up", self._wavelengths_up)]
            if self._both_directions:
                passes.append(("down", list(reversed(self._wavelengths_up))))
            for repeat in range(1, self._repeats + 1):
                for direction, wavelengths in passes:
                    for target in wavelengths:
                        if self._stop_requested:
                            self.logMessage.emit("Sweep stopped by user.")
                            self._finish(index, error_count)
                            return
                        measurement = self._measure_one(index, direction, repeat, target)
                        index += 1
                        if measurement.error_code != 0:
                            error_count += 1
                        self.pointReady.emit(measurement)
            self._finish(index, error_count)
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self.failed.emit(str(exc))

    def _measure_one(self, index: int, direction: str, repeat: int, target_nm: float) -> SettleMeasurement:
        settle_ms, polls = self._client.settle_time_ms(target_nm)
        confirmed_nm = self._client.get_wavelength()
        error_code = self._client.get_error_code()
        if error_code != 0:
            self.logMessage.emit(
                f"  ! error {error_code} ({ERROR_CODE_MEANINGS.get(error_code, 'unknown')}) "
                f"tuning to {target_nm:.2f} nm - clearing and continuing"
            )
            self._client.clear_error()
        temperature_c = self._client.get_temperature_c()
        return SettleMeasurement(
            index=index,
            direction=direction,
            repeat=repeat,
            requested_nm=target_nm,
            confirmed_nm=confirmed_nm,
            settle_ms=settle_ms,
            poll_count=polls,
            temperature_c=temperature_c,
            error_code=error_code,
        )

    def _finish(self, points_measured: int, error_count: int) -> None:
        try:
            self._client.settle_time_ms(self._return_to_nm)
            self.logMessage.emit(f"Returned filter to starting wavelength ({self._return_to_nm:.2f} nm).")
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup, don't mask the real result
            self.logMessage.emit(f"Could not return to starting wavelength: {exc}")
        self.finished_ok.emit(points_measured, error_count)


class PaletteCompareWorker(QThread):
    """Tab 2: runs the same wavelength list via direct W commands and via a
    palette (D to define, P to jump), back-to-back per repeat, so the two
    methods are compared under matched conditions rather than compared
    across separate runs with possible thermal/timing drift between them."""

    MAX_PALETTE_ELEMENTS = 128  # manual: legal palette indices are 0-127

    pointReady = pyqtSignal(object)  # PaletteCompareMeasurement
    logMessage = pyqtSignal(str)
    finished_ok = pyqtSignal(int, int)
    failed = pyqtSignal(str)

    def __init__(self, client: VariSpecClient, wavelengths: list[float], repeats: int, return_to_nm: float) -> None:
        super().__init__()
        self._client = client
        truncated = wavelengths[: self.MAX_PALETTE_ELEMENTS]
        self._truncated_count = len(wavelengths) - len(truncated)
        self._wavelengths = truncated
        self._repeats = repeats
        self._return_to_nm = return_to_nm
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            if self._truncated_count:
                self.logMessage.emit(
                    f"Wavelength list exceeds the filter's {self.MAX_PALETTE_ELEMENTS}-element "
                    f"palette capacity - dropped the last {self._truncated_count} point(s)."
                )
            self.logMessage.emit(f"Defining a {len(self._wavelengths)}-element palette on the filter...")
            self._client.clear_palette()
            for nm in self._wavelengths:
                self._client.define_palette_element(nm)

            index = 0
            error_count = 0
            for repeat in range(1, self._repeats + 1):
                for method in ("direct", "palette"):
                    for palette_index, nm in enumerate(self._wavelengths):
                        if self._stop_requested:
                            self.logMessage.emit("Comparison stopped by user.")
                            self._finish(index, error_count)
                            return
                        m = self._measure_one(index, repeat, method, palette_index, nm)
                        index += 1
                        if m.error_code != 0:
                            error_count += 1
                        self.pointReady.emit(m)
            self._finish(index, error_count)
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self.failed.emit(str(exc))

    def _measure_one(self, index: int, repeat: int, method: str, palette_index: int, nm: float) -> PaletteCompareMeasurement:
        client = self._client
        if method == "direct":
            settle_ms, polls = client.settle_time_ms(nm)
        else:
            settle_ms, polls = client.palette_settle_time_ms(palette_index)
        confirmed_nm = client.get_wavelength()
        error_code = client.get_error_code()
        if error_code != 0:
            self.logMessage.emit(f"  ! error {error_code} on {method} tune to {nm:.2f} nm - clearing")
            client.clear_error()
        return PaletteCompareMeasurement(
            index=index,
            repeat=repeat,
            method=method,
            requested_nm=nm,
            confirmed_nm=confirmed_nm,
            settle_ms=settle_ms,
            poll_count=polls,
            error_code=error_code,
        )

    def _finish(self, points_measured: int, error_count: int) -> None:
        try:
            self._client.clear_palette()
            self._client.settle_time_ms(self._return_to_nm)
        except Exception as exc:  # noqa: BLE001
            self.logMessage.emit(f"Cleanup after comparison failed: {exc}")
        self.finished_ok.emit(points_measured, error_count)


class TransitionCaptureWorker(QThread):
    """Tab 3: optically measures how long the filter really takes to settle,
    by watching the spectrometer signal at the source and destination
    wavelengths through a real wavelength change. Thin wrapper around
    measure_transition() - see that function's docstring for the
    busy-check-vs-optical methodology (shared with the tab 6 batch sweep).
    """

    logMessage = pyqtSignal(str)
    finished_ok = pyqtSignal(object)  # dict, see _finish
    failed = pyqtSignal(str)

    def __init__(
        self,
        lctf: VariSpecClient,
        spectrometer: OceanSpectrometer,
        from_nm: float,
        to_nm: float,
        integration_time_ms: float,
        pre_roll_s: float,
        post_roll_s: float,
        save_dir: Path,
    ) -> None:
        super().__init__()
        self._lctf = lctf
        self._spectrometer = spectrometer
        self._from_nm = from_nm
        self._to_nm = to_nm
        self._integration_time_ms = integration_time_ms
        self._pre_roll_s = pre_roll_s
        self._post_roll_s = post_roll_s
        self._save_dir = save_dir
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            settings = AcquisitionSettings(
                integration_time_ms=self._integration_time_ms,
                averages=1,
                correct_dark_counts=False,
                correct_nonlinearity=False,
                trigger_mode=0,
            )
            self.logMessage.emit(
                f"Parking at {self._from_nm:.2f} nm, measuring busy-check settle time (repeat 1 of 2), "
                f"then capturing optically (repeat 2 of 2): {self._pre_roll_s:.2f}s pre-roll, "
                f"{self._post_roll_s:.2f}s post-roll, integration {self._integration_time_ms:.2f} ms..."
            )
            measurement = measure_transition(
                self._lctf, self._spectrometer, settings,
                self._from_nm, self._to_nm, self._pre_roll_s, self._post_roll_s,
                stop_check=lambda: self._stop_requested,
            )
            self.logMessage.emit(
                f"Busy-check settle time: {measurement.busy_check_settle_ms:.2f} ms "
                f"({measurement.busy_check_polls} polls). Capture done: {len(measurement.t_ms)} frames, "
                f"confirmed final wavelength {measurement.confirmed_nm:.2f} nm."
            )
            self._finish(measurement, stopped=self._stop_requested)
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self.failed.emit(str(exc))

    def _finish(self, measurement: TransitionMeasurement, stopped: bool) -> None:
        if measurement.intensities.size == 0:
            self.finished_ok.emit({
                "frames_count": 0, "stopped": stopped,
                "busy_check_settle_ms": measurement.busy_check_settle_ms,
            })
            return

        wavelengths_nm = measurement.wavelengths_nm
        t_ms = measurement.t_ms
        intensities = measurement.intensities
        from_pixel = int(np.argmin(np.abs(wavelengths_nm - self._from_nm)))
        to_pixel = int(np.argmin(np.abs(wavelengths_nm - self._to_nm)))

        self._save_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        csv_path = self._save_dir / f"transition_{self._from_nm:.0f}to{self._to_nm:.0f}nm_{timestamp}.csv"
        with csv_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["t_ms", *[f"{w:.3f}" for w in wavelengths_nm]])
            for row_t, row_vals in zip(t_ms, intensities):
                writer.writerow([f"{row_t:.3f}", *row_vals.tolist()])

        busy_ms = measurement.busy_check_settle_ms
        busy_polls = measurement.busy_check_polls
        optical_ms = measurement.optical_settle_ms_90pct
        fps = measurement.achieved_fps

        summary = {
            "frames_count": len(t_ms),
            "t_ms": t_ms,
            "wavelengths_nm": wavelengths_nm,
            "intensities": intensities,
            "from_pixel": from_pixel,
            "to_pixel": to_pixel,
            "from_nm": self._from_nm,
            "to_nm": self._to_nm,
            "busy_check_settle_ms": busy_ms,
            "busy_check_polls": busy_polls,
            "optical_settle_ms_90pct": optical_ms,
            "achieved_fps": fps,
            "csv_path": csv_path,
            "stopped": stopped,
        }
        optical_text = "n/a - inspect plot/CSV" if optical_ms is None else f"{optical_ms:.2f} ms"
        self.logMessage.emit(
            f"Optical vs protocol for {self._from_nm:.1f}->{self._to_nm:.1f} nm: "
            f"busy-check={busy_ms:.2f} ms, optical (90% rise)={optical_text}, "
            f"achieved ~{fps:.0f} fps during post-roll."
        )
        self.logMessage.emit(f"Saved {len(t_ms)} frames to {csv_path}")
        self.finished_ok.emit(summary)


class SpectralSweepWorker(QThread):
    """Tab 4: settles at each requested wavelength (busy-check, with an
    optional extra safety margin) and records a real spectrometer spectrum
    there, saving incrementally so a stop or crash doesn't lose completed
    points."""

    pointReady = pyqtSignal(object)  # SpectralSweepPoint
    logMessage = pyqtSignal(str)
    finished_ok = pyqtSignal(int, str)  # points_measured, csv_path
    failed = pyqtSignal(str)

    def __init__(
        self,
        lctf: VariSpecClient,
        spectrometer: OceanSpectrometer,
        wavelengths: list[float],
        settings: AcquisitionSettings,
        extra_settle_margin_ms: float,
        save_dir: Path,
        return_to_nm: float,
        dark_spectrum: object | None = None,
    ) -> None:
        super().__init__()
        self._lctf = lctf
        self._spectrometer = spectrometer
        self._wavelengths = wavelengths
        self._settings = settings
        self._extra_settle_margin_ms = extra_settle_margin_ms
        self._save_dir = save_dir
        self._return_to_nm = return_to_nm
        self._dark_spectrum = dark_spectrum
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        csv_path: Path | None = None
        try:
            self._save_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            csv_path = self._save_dir / f"spectral_sweep_{timestamp}.csv"
            index = 0
            header_written = False
            with csv_path.open("w", newline="") as f:
                writer = csv.writer(f)
                if self._dark_spectrum is not None:
                    writer.writerow(
                        ["requested_nm", "confirmed_nm", "settle_ms", "temperature_c",
                         *[f"{w:.3f}" for w in self._dark_spectrum.wavelengths_nm]]
                    )
                    writer.writerow(["DARK", "", "", "", *self._dark_spectrum.values.tolist()])
                    f.flush()
                    header_written = True
                    self.logMessage.emit(
                        "Wrote the captured dark spectrum as the first row of this sweep's CSV "
                        "(requested_nm='DARK') - all other rows stay raw/unsubtracted."
                    )
                for nm in self._wavelengths:
                    if self._stop_requested:
                        self.logMessage.emit("Sweep stopped by user.")
                        break
                    settle_ms, _polls = self._lctf.settle_time_ms(nm)
                    if self._extra_settle_margin_ms > 0:
                        time.sleep(self._extra_settle_margin_ms / 1000.0)
                    confirmed_nm = self._lctf.get_wavelength()
                    error_code = self._lctf.get_error_code()
                    if error_code != 0:
                        self.logMessage.emit(f"  ! error {error_code} at {nm:.2f} nm - clearing, skipping capture")
                        self._lctf.clear_error()
                        continue
                    temperature_c = self._lctf.get_temperature_c()
                    spectrum = self._spectrometer.acquire_spectrum(self._settings)
                    if not header_written:
                        writer.writerow(
                            ["requested_nm", "confirmed_nm", "settle_ms", "temperature_c",
                             *[f"{w:.3f}" for w in spectrum.wavelengths_nm]]
                        )
                        header_written = True
                    writer.writerow(
                        [f"{nm:.3f}", f"{confirmed_nm:.3f}", f"{settle_ms:.2f}", f"{temperature_c:.2f}",
                         *spectrum.values.tolist()]
                    )
                    f.flush()
                    self.pointReady.emit(
                        SpectralSweepPoint(
                            index=index,
                            requested_nm=nm,
                            confirmed_nm=confirmed_nm,
                            settle_ms=settle_ms,
                            temperature_c=temperature_c,
                            wavelengths_nm=spectrum.wavelengths_nm,
                            intensities=spectrum.values,
                        )
                    )
                    index += 1
            try:
                self._lctf.settle_time_ms(self._return_to_nm)
            except Exception as exc:  # noqa: BLE001
                self.logMessage.emit(f"Could not return to starting wavelength: {exc}")
            self.finished_ok.emit(index, str(csv_path) if csv_path else "")
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self.failed.emit(str(exc))


@dataclass(slots=True)
class TransitionBatchPoint:
    index: int
    direction: str  # "up" or "down"
    repeat: int
    from_nm: float
    to_nm: float
    step_nm: float  # signed: to_nm - from_nm, i.e. the jump size and direction
    busy_check_settle_ms: float
    optical_settle_ms_90pct: float | None
    achieved_fps: float
    temperature_c: float
    error_code: int
    frames_csv_path: str | None  # only set if the batch was run with "save full frame data" on


class TransitionBatchWorker(QThread):
    """Tab 6: walks a wavelength list step by step (e.g. every 5 or 10nm
    across the filter's whole range), optically measuring the settle time of
    EVERY consecutive transition via measure_transition() - the same
    per-pair logic tab 3 uses for one transition, just looped. The point is
    to separate two questions that a single from/to test can't: does settle
    time depend on WHERE in the spectrum you are, or on the SIZE of the
    jump? Holding the step size fixed for one run and walking it across the
    range answers the first; running the batch again with a different step
    answers the second (compare the two runs' summary CSVs/plots).

    Only a one-row-per-transition summary is saved by default (from/to nm,
    step, busy-check ms, optical ms, fps, temperature, error code) - full
    per-transition frame data (the same wide CSV tab 3 saves, one file per
    transition) is opt-in, since a full-range fine-step batch can othewise
    produce a very large number of multi-MB files.
    """

    pointReady = pyqtSignal(object)  # TransitionBatchPoint
    logMessage = pyqtSignal(str)
    finished_ok = pyqtSignal(int, int, str)  # points_measured, error_count, summary_csv_path
    failed = pyqtSignal(str)

    def __init__(
        self,
        lctf: VariSpecClient,
        spectrometer: OceanSpectrometer,
        wavelengths_up: list[float],
        both_directions: bool,
        repeats: int,
        integration_time_ms: float,
        pre_roll_s: float,
        post_roll_s: float,
        save_full_frames: bool,
        save_dir: Path,
        return_to_nm: float,
    ) -> None:
        super().__init__()
        self._lctf = lctf
        self._spectrometer = spectrometer
        self._wavelengths_up = wavelengths_up
        self._both_directions = both_directions
        self._repeats = repeats
        self._integration_time_ms = integration_time_ms
        self._pre_roll_s = pre_roll_s
        self._post_roll_s = post_roll_s
        self._save_full_frames = save_full_frames
        self._save_dir = save_dir
        self._return_to_nm = return_to_nm
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        summary_path: Path | None = None
        try:
            settings = AcquisitionSettings(
                integration_time_ms=self._integration_time_ms,
                averages=1,
                correct_dark_counts=False,
                correct_nonlinearity=False,
                trigger_mode=0,
            )
            self._save_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            summary_path = self._save_dir / f"transition_batch_summary_{timestamp}.csv"

            passes: list[tuple[str, list[float]]] = [("up", self._wavelengths_up)]
            if self._both_directions:
                passes.append(("down", list(reversed(self._wavelengths_up))))

            index = 0
            error_count = 0
            with summary_path.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "index", "direction", "repeat", "from_nm", "to_nm", "step_nm",
                    "busy_check_settle_ms", "optical_settle_ms_90pct", "achieved_fps",
                    "temperature_c", "error_code", "frames_csv_path",
                ])
                for repeat in range(1, self._repeats + 1):
                    for direction, wavelengths in passes:
                        for i in range(len(wavelengths) - 1):
                            if self._stop_requested:
                                self.logMessage.emit("Batch stopped by user.")
                                self._finish(index, error_count, summary_path)
                                return
                            from_nm, to_nm = wavelengths[i], wavelengths[i + 1]
                            point = self._measure_pair(index, direction, repeat, from_nm, to_nm, settings)
                            index += 1
                            if point.error_code != 0:
                                error_count += 1
                            writer.writerow([
                                point.index, point.direction, point.repeat, f"{point.from_nm:.3f}",
                                f"{point.to_nm:.3f}", f"{point.step_nm:.3f}", f"{point.busy_check_settle_ms:.2f}",
                                "" if point.optical_settle_ms_90pct is None else f"{point.optical_settle_ms_90pct:.2f}",
                                f"{point.achieved_fps:.1f}", f"{point.temperature_c:.2f}", point.error_code,
                                point.frames_csv_path or "",
                            ])
                            f.flush()
                            self.pointReady.emit(point)
            self._finish(index, error_count, summary_path)
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self.failed.emit(str(exc))

    def _measure_pair(
        self, index: int, direction: str, repeat: int, from_nm: float, to_nm: float, settings: AcquisitionSettings,
    ) -> TransitionBatchPoint:
        measurement = measure_transition(
            self._lctf, self._spectrometer, settings, from_nm, to_nm,
            self._pre_roll_s, self._post_roll_s, stop_check=lambda: self._stop_requested,
        )
        if measurement.error_code != 0:
            self.logMessage.emit(
                f"  ! error {measurement.error_code} "
                f"({ERROR_CODE_MEANINGS.get(measurement.error_code, 'unknown')}) on {from_nm:.2f}->{to_nm:.2f} nm"
            )

        frames_csv_path: str | None = None
        if self._save_full_frames and measurement.intensities.size > 0:
            path = self._save_dir / (
                f"transition_batch_frames_{from_nm:.0f}to{to_nm:.0f}nm_{time.strftime('%Y%m%d_%H%M%S')}_{index}.csv"
            )
            with path.open("w", newline="") as ff:
                fw = csv.writer(ff)
                fw.writerow(["t_ms", *[f"{w:.3f}" for w in measurement.wavelengths_nm]])
                for row_t, row_vals in zip(measurement.t_ms, measurement.intensities):
                    fw.writerow([f"{row_t:.3f}", *row_vals.tolist()])
            frames_csv_path = str(path)

        optical_text = "n/a" if measurement.optical_settle_ms_90pct is None else f"{measurement.optical_settle_ms_90pct:.2f} ms"
        self.logMessage.emit(
            f"#{index:04d} {direction:4s} rep{repeat} {from_nm:7.2f} -> {to_nm:7.2f} nm "
            f"(step {to_nm - from_nm:+.1f}): busy-check={measurement.busy_check_settle_ms:6.2f} ms, "
            f"optical={optical_text}"
        )
        return TransitionBatchPoint(
            index=index, direction=direction, repeat=repeat, from_nm=from_nm, to_nm=to_nm,
            step_nm=to_nm - from_nm, busy_check_settle_ms=measurement.busy_check_settle_ms,
            optical_settle_ms_90pct=measurement.optical_settle_ms_90pct, achieved_fps=measurement.achieved_fps,
            temperature_c=measurement.temperature_c, error_code=measurement.error_code,
            frames_csv_path=frames_csv_path,
        )

    def _finish(self, points_measured: int, error_count: int, summary_path: Path) -> None:
        try:
            self._lctf.settle_time_ms(self._return_to_nm)
        except Exception as exc:  # noqa: BLE001
            self.logMessage.emit(f"Could not return to starting wavelength: {exc}")
        self.finished_ok.emit(points_measured, error_count, str(summary_path))


class ManualMoveWorker(QThread):
    """One-shot "go to wavelength" for the persistent manual-control row -
    kept off the GUI thread since even a single settle can take a few
    hundred ms and this codebase's hard rule is not to block the GUI thread
    for hardware I/O."""

    finished_ok = pyqtSignal(float, float, int, float, int)  # confirmed_nm, settle_ms, polls, temperature_c, error_code
    failed = pyqtSignal(str)

    def __init__(self, client: VariSpecClient, target_nm: float) -> None:
        super().__init__()
        self._client = client
        self._target_nm = target_nm

    def run(self) -> None:
        try:
            settle_ms, polls = self._client.settle_time_ms(self._target_nm)
            confirmed_nm = self._client.get_wavelength()
            error_code = self._client.get_error_code()
            if error_code != 0:
                self._client.clear_error()
            temperature_c = self._client.get_temperature_c()
            self.finished_ok.emit(confirmed_nm, settle_ms, polls, temperature_c, error_code)
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self.failed.emit(str(exc))


class DarkCaptureWorker(QThread):
    """One-shot dark/background spectrum capture (lamp off), off the GUI
    thread since it can take a while at high integration time/averages."""

    finished_ok = pyqtSignal(object)  # Spectrum
    failed = pyqtSignal(str)

    def __init__(self, spectrometer: OceanSpectrometer, settings: AcquisitionSettings) -> None:
        super().__init__()
        self._spectrometer = spectrometer
        self._settings = settings

    def run(self) -> None:
        try:
            spectrum = self._spectrometer.acquire_spectrum(self._settings)
            self.finished_ok.emit(spectrum)
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self.failed.emit(str(exc))


class LiveSettingsBox:
    """Thread-safe holder for the AcquisitionSettings a running LiveViewWorker
    should use next - lets the GUI thread change integration time/averages/
    corrections while the worker keeps acquiring, without a restart."""

    def __init__(self, settings: AcquisitionSettings) -> None:
        self._lock = threading.Lock()
        self._settings = settings

    def get(self) -> AcquisitionSettings:
        with self._lock:
            return self._settings

    def set(self, settings: AcquisitionSettings) -> None:
        with self._lock:
            self._settings = settings


class LatestFrameBox:
    """Thread-safe "latest value wins" mailbox from LiveViewWorker to the GUI.

    The worker can acquire far faster than any sane GUI redraw rate (hundreds
    of fps at short integration times) - rather than emit a Qt signal per
    frame and flood the event loop, the worker just publishes here and the
    GUI polls it on its own timer (see MainWindow._on_live_timer_tick).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spectrum: object | None = None
        self._frame_count = 0

    def set(self, spectrum: object) -> None:
        with self._lock:
            self._spectrum = spectrum
            self._frame_count += 1

    def get(self) -> tuple[object | None, int]:
        with self._lock:
            return self._spectrum, self._frame_count


class LiveViewWorker(QThread):
    """Tab 5: free-running spectrometer acquisition for setting up
    integration time/averages/corrections while watching the signal. Not
    tied to the LCTF at all - useful on its own for aligning optics, checking
    for saturation, etc."""

    failed = pyqtSignal(str)

    def __init__(self, spectrometer: OceanSpectrometer, live_settings: LiveSettingsBox, latest_frame: LatestFrameBox) -> None:
        super().__init__()
        self._spectrometer = spectrometer
        self._live_settings = live_settings
        self._latest_frame = latest_frame
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            while not self._stop_requested:
                settings = self._live_settings.get()
                spectrum = self._spectrometer.acquire_spectrum(settings)
                self._latest_frame.set(spectrum)
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self.failed.emit(str(exc))


def apply_dark_and_range(
    wavelengths: np.ndarray,
    values: np.ndarray,
    dark_values: np.ndarray | None,
    range_nm: tuple[float, float] | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Display-only transform shared by the live view and spectral sweep
    plots: optionally subtract a dark spectrum, then optionally restrict to
    a wavelength sub-range. Never touches saved data - see the module
    docstring's note on tab 4 for why dark subtraction is display-only."""
    if dark_values is not None and len(dark_values) == len(values):
        values = values - dark_values
    if range_nm is not None:
        lo, hi = range_nm
        mask = (wavelengths >= lo) & (wavelengths <= hi)
        wavelengths = wavelengths[mask]
        values = values[mask]
    return wavelengths, values


def _add_range_step_row(layout: QHBoxLayout, default_start: float, default_stop: float, default_step: float) -> tuple[QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox]:
    layout.addWidget(QLabel("Start (nm):"))
    start = QDoubleSpinBox()
    start.setRange(0, 3000)
    start.setDecimals(1)
    start.setValue(default_start)
    layout.addWidget(start)

    layout.addWidget(QLabel("Stop (nm):"))
    stop = QDoubleSpinBox()
    stop.setRange(0, 3000)
    stop.setDecimals(1)
    stop.setValue(default_stop)
    layout.addWidget(stop)

    layout.addWidget(QLabel("Step (nm):"))
    step = QDoubleSpinBox()
    step.setRange(0.1, 500)
    step.setDecimals(1)
    step.setValue(default_step)
    layout.addWidget(step)

    return start, stop, step


# ── Main window ──────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LSPRi acq — Illumination source probe (VariSpec LCTF + Ocean spectrometer)")
        self.resize(1250, 950)

        self._lctf: VariSpecClient | None = None
        self._lctf_info: VariSpecInfo | None = None
        self._start_wavelength_nm: float | None = None
        self._spectrometer: OceanSpectrometer | None = None
        self._spectrometer_wavelengths_nm: np.ndarray | None = None
        self._dark_spectrum: object | None = None  # a Spectrum, captured with lamp off
        self._lctf_busy = False
        self._spec_busy = False
        self._lctf_active_button: QPushButton | None = None
        self._spec_active_button: QPushButton | None = None

        self._sweep_worker: SweepWorker | None = None
        self._palette_worker: PaletteCompareWorker | None = None
        self._transition_worker: TransitionCaptureWorker | None = None
        self._spectral_worker: SpectralSweepWorker | None = None
        self._transition_batch_worker: TransitionBatchWorker | None = None
        self._manual_worker: ManualMoveWorker | None = None
        self._dark_worker: DarkCaptureWorker | None = None
        self._live_worker: LiveViewWorker | None = None
        self._live_settings_box: LiveSettingsBox | None = None
        self._live_frame_box: LatestFrameBox | None = None
        self._live_last_frame_count: int | None = None
        self._live_last_tick_time: float | None = None

        self._settle_results: list[SettleMeasurement] = []
        self._palette_results: list[PaletteCompareMeasurement] = []
        self._tbatch_results: list[TransitionBatchPoint] = []

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(100)  # GUI redraw rate, decoupled from acquisition rate - see LatestFrameBox
        self._live_timer.timeout.connect(self._on_live_timer_tick)

        root = QWidget()
        layout = QVBoxLayout(root)

        # -- LCTF connection row -------------------------------------------
        lctf_row = QHBoxLayout()
        lctf_row.addWidget(QLabel("LCTF port:"))
        self.port_combo = QComboBox()
        lctf_row.addWidget(self.port_combo)
        self.refresh_ports_button = QPushButton("Refresh")
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        lctf_row.addWidget(self.refresh_ports_button)
        lctf_row.addWidget(QLabel("Baud:"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["115200", "9600"])  # 9600 only for legacy/XNIR-09-20, per manual
        lctf_row.addWidget(self.baud_combo)
        self.lctf_connect_button = QPushButton("Connect LCTF")
        self.lctf_connect_button.clicked.connect(self._toggle_lctf_connection)
        lctf_row.addWidget(self.lctf_connect_button)
        lctf_row.addStretch(1)
        layout.addLayout(lctf_row)

        self.lctf_status_label = QLabel("LCTF: not connected.")
        self.lctf_status_label.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        layout.addWidget(self.lctf_status_label)

        # -- Spectrometer connection row -------------------------------------
        spec_row = QHBoxLayout()
        self.spec_connect_button = QPushButton("Connect spectrometer")
        self.spec_connect_button.clicked.connect(self._toggle_spectrometer_connection)
        spec_row.addWidget(self.spec_connect_button)
        spec_row.addStretch(1)
        layout.addLayout(spec_row)

        self.spec_status_label = QLabel("Spectrometer: not connected.")
        self.spec_status_label.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        layout.addWidget(self.spec_status_label)

        # -- shared spectrometer display controls (tabs 4 and 5) ----------------
        display_row = QHBoxLayout()
        self.display_range_check = QCheckBox("Limit displayed wavelength range")
        display_row.addWidget(self.display_range_check)
        display_row.addWidget(QLabel("Min (nm):"))
        self.display_min_spin = QDoubleSpinBox()
        self.display_min_spin.setRange(0, 3000)
        self.display_min_spin.setDecimals(1)
        self.display_min_spin.setValue(400.0)
        display_row.addWidget(self.display_min_spin)
        display_row.addWidget(QLabel("Max (nm):"))
        self.display_max_spin = QDoubleSpinBox()
        self.display_max_spin.setRange(0, 3000)
        self.display_max_spin.setDecimals(1)
        self.display_max_spin.setValue(750.0)
        display_row.addWidget(self.display_max_spin)
        display_row.addWidget(QLabel("(plot only - never affects saved data)"))
        display_row.addStretch(1)
        layout.addLayout(display_row)

        # -- persistent manual wavelength control -----------------------------
        manual_row = QHBoxLayout()
        manual_row.addWidget(QLabel("Go to wavelength (nm):"))
        self.manual_wavelength_spin = QDoubleSpinBox()
        self.manual_wavelength_spin.setDecimals(2)
        self.manual_wavelength_spin.setEnabled(False)
        manual_row.addWidget(self.manual_wavelength_spin)
        self.manual_go_button = QPushButton("Go")
        self.manual_go_button.setEnabled(False)
        self.manual_go_button.clicked.connect(self._on_manual_go)
        manual_row.addWidget(self.manual_go_button)
        self.manual_result_label = QLabel("")
        manual_row.addWidget(self.manual_result_label)
        manual_row.addStretch(1)
        layout.addLayout(manual_row)

        # -- tabs -------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_settle_sweep_tab(), "1. Settle-time sweep (protocol)")
        self.tabs.addTab(self._build_palette_tab(), "2. Palette vs direct")
        self.tabs.addTab(self._build_transition_tab(), "3. Optical transition capture")
        self.tabs.addTab(self._build_spectral_sweep_tab(), "4. Optical spectral sweep")
        self.tabs.addTab(self._build_live_view_tab(), "5. Live spectrometer view")
        self.tabs.addTab(self._build_transition_batch_tab(), "6. Optical transition batch sweep")
        layout.addWidget(self.tabs, 1)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(200)
        layout.addWidget(self.log)

        self.setCentralWidget(root)

        self._refresh_ports()
        self._update_device_dependent_controls()

    # -- tab builders -------------------------------------------------------

    def _build_settle_sweep_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        sweep_row = QHBoxLayout()
        self.sweep_start_spin, self.sweep_stop_spin, self.sweep_step_spin = _add_range_step_row(sweep_row, 450.0, 700.0, 10.0)
        sweep_row.addWidget(QLabel("Repeats:"))
        self.sweep_repeats_spin = QSpinBox()
        self.sweep_repeats_spin.setRange(1, 50)
        self.sweep_repeats_spin.setValue(3)
        sweep_row.addWidget(self.sweep_repeats_spin)
        self.sweep_both_directions_check = QCheckBox("Sweep down too (hysteresis check)")
        self.sweep_both_directions_check.setChecked(True)
        sweep_row.addWidget(self.sweep_both_directions_check)
        sweep_row.addStretch(1)
        layout.addLayout(sweep_row)

        run_row = QHBoxLayout()
        self.sweep_run_button = QPushButton("Run sweep")
        self.sweep_run_button.clicked.connect(self._toggle_sweep)
        run_row.addWidget(self.sweep_run_button)
        self.sweep_export_button = QPushButton("Export CSV...")
        self.sweep_export_button.clicked.connect(self._export_sweep_csv)
        self.sweep_export_button.setEnabled(False)
        run_row.addWidget(self.sweep_export_button)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.sweep_plot = pg.PlotWidget()
        self.sweep_plot.setLabel("left", "Settle time (busy-check)", units="ms")
        self.sweep_plot.setLabel("bottom", "Requested wavelength", units="nm")
        self.sweep_plot.addLegend()
        self._sweep_scatter_up = pg.ScatterPlotItem(pen=None, brush=pg.mkBrush("c"), size=8, name="up-sweep")
        self._sweep_scatter_down = pg.ScatterPlotItem(pen=None, brush=pg.mkBrush("m"), size=8, name="down-sweep")
        self.sweep_plot.addItem(self._sweep_scatter_up)
        self.sweep_plot.addItem(self._sweep_scatter_down)
        layout.addWidget(self.sweep_plot, 1)

        return tab

    def _build_palette_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        sweep_row = QHBoxLayout()
        self.palette_start_spin, self.palette_stop_spin, self.palette_step_spin = _add_range_step_row(sweep_row, 450.0, 700.0, 25.0)
        sweep_row.addWidget(QLabel("Repeats:"))
        self.palette_repeats_spin = QSpinBox()
        self.palette_repeats_spin.setRange(1, 50)
        self.palette_repeats_spin.setValue(3)
        sweep_row.addWidget(self.palette_repeats_spin)
        sweep_row.addStretch(1)
        layout.addLayout(sweep_row)

        run_row = QHBoxLayout()
        self.palette_run_button = QPushButton("Run comparison")
        self.palette_run_button.clicked.connect(self._toggle_palette_compare)
        run_row.addWidget(self.palette_run_button)
        self.palette_export_button = QPushButton("Export CSV...")
        self.palette_export_button.clicked.connect(self._export_palette_csv)
        self.palette_export_button.setEnabled(False)
        run_row.addWidget(self.palette_export_button)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.palette_plot = pg.PlotWidget()
        self.palette_plot.setLabel("left", "Settle time (busy-check)", units="ms")
        self.palette_plot.setLabel("bottom", "Requested wavelength", units="nm")
        self.palette_plot.addLegend()
        self._palette_scatter_direct = pg.ScatterPlotItem(pen=None, brush=pg.mkBrush("c"), size=8, name="direct (W)")
        self._palette_scatter_palette = pg.ScatterPlotItem(pen=None, brush=pg.mkBrush("y"), size=8, name="palette (P)")
        self.palette_plot.addItem(self._palette_scatter_direct)
        self.palette_plot.addItem(self._palette_scatter_palette)
        layout.addWidget(self.palette_plot, 1)

        return tab

    def _build_transition_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        row.addWidget(QLabel("From (nm):"))
        self.transition_from_spin = QDoubleSpinBox()
        self.transition_from_spin.setRange(0, 3000)
        self.transition_from_spin.setDecimals(2)
        self.transition_from_spin.setValue(550.0)
        row.addWidget(self.transition_from_spin)
        row.addWidget(QLabel("To (nm):"))
        self.transition_to_spin = QDoubleSpinBox()
        self.transition_to_spin.setRange(0, 3000)
        self.transition_to_spin.setDecimals(2)
        self.transition_to_spin.setValue(650.0)
        row.addWidget(self.transition_to_spin)
        row.addWidget(QLabel("Integration (ms):"))
        self.transition_integration_spin = QDoubleSpinBox()
        self.transition_integration_spin.setRange(0.001, 10000)
        self.transition_integration_spin.setDecimals(3)
        self.transition_integration_spin.setValue(1.0)
        row.addWidget(self.transition_integration_spin)
        row.addWidget(QLabel("Pre-roll (s):"))
        self.transition_preroll_spin = QDoubleSpinBox()
        self.transition_preroll_spin.setRange(0.0, 10.0)
        self.transition_preroll_spin.setDecimals(2)
        self.transition_preroll_spin.setValue(0.15)
        row.addWidget(self.transition_preroll_spin)
        row.addWidget(QLabel("Post-roll (s):"))
        self.transition_postroll_spin = QDoubleSpinBox()
        self.transition_postroll_spin.setRange(0.05, 30.0)
        self.transition_postroll_spin.setDecimals(2)
        self.transition_postroll_spin.setValue(1.0)
        row.addWidget(self.transition_postroll_spin)
        row.addStretch(1)
        layout.addLayout(row)

        run_row = QHBoxLayout()
        self.transition_run_button = QPushButton("Run capture")
        self.transition_run_button.clicked.connect(self._toggle_transition_capture)
        run_row.addWidget(self.transition_run_button)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.transition_result_label = QLabel("Run a capture to compare busy-check vs. optically measured settle time.")
        self.transition_result_label.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.transition_result_label)

        self.transition_plot = pg.PlotWidget()
        self.transition_plot.setLabel("left", "Intensity", units="counts")
        self.transition_plot.setLabel("bottom", "Time since W sent", units="ms")
        self.transition_plot.addLegend()
        self.transition_plot.addItem(pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen("gray", style=Qt.PenStyle.DashLine)))
        # Created once and updated via setData() on every run, never
        # recreated - see _start_transition_capture for why: repeatedly
        # calling PlotWidget.clear() + a fresh .plot(..., name=...) leaves
        # stale entries in the legend referencing deleted curve items, which
        # is a known pyqtgraph source of crashes after enough repeated runs.
        # Every other tab in this file already follows this "create once,
        # setData() after" pattern - tab 3 was the one exception.
        self._transition_curve_from = self.transition_plot.plot(pen=pg.mkPen("m", width=2), name="source (from)")
        self._transition_curve_to = self.transition_plot.plot(pen=pg.mkPen("c", width=2), name="destination (to)")
        layout.addWidget(self.transition_plot, 1)

        return tab

    def _build_spectral_sweep_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        sweep_row = QHBoxLayout()
        self.spectral_start_spin, self.spectral_stop_spin, self.spectral_step_spin = _add_range_step_row(sweep_row, 450.0, 700.0, 25.0)
        sweep_row.addStretch(1)
        layout.addLayout(sweep_row)

        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Integration (ms):"))
        self.spectral_integration_spin = QDoubleSpinBox()
        self.spectral_integration_spin.setRange(0.001, 10000)
        self.spectral_integration_spin.setDecimals(3)
        self.spectral_integration_spin.setValue(20.0)
        settings_row.addWidget(self.spectral_integration_spin)
        settings_row.addWidget(QLabel("Averages:"))
        self.spectral_averages_spin = QSpinBox()
        self.spectral_averages_spin.setRange(1, 500)
        self.spectral_averages_spin.setValue(5)
        settings_row.addWidget(self.spectral_averages_spin)
        self.spectral_dark_check = QCheckBox("Correct dark counts")
        settings_row.addWidget(self.spectral_dark_check)
        self.spectral_nonlin_check = QCheckBox("Correct nonlinearity")
        settings_row.addWidget(self.spectral_nonlin_check)
        settings_row.addWidget(QLabel("Extra settle margin (ms):"))
        self.spectral_margin_spin = QDoubleSpinBox()
        self.spectral_margin_spin.setRange(0.0, 5000.0)
        self.spectral_margin_spin.setDecimals(1)
        self.spectral_margin_spin.setValue(0.0)
        settings_row.addWidget(self.spectral_margin_spin)
        settings_row.addStretch(1)
        layout.addLayout(settings_row)

        dark_row = QHBoxLayout()
        self.dark_capture_button = QPushButton("Capture dark spectrum (lamp off)")
        self.dark_capture_button.clicked.connect(self._on_capture_dark)
        dark_row.addWidget(self.dark_capture_button)
        self.spectral_subtract_dark_check = QCheckBox("Subtract dark for live display")
        self.spectral_subtract_dark_check.setEnabled(False)
        dark_row.addWidget(self.spectral_subtract_dark_check)
        self.dark_status_label = QLabel("No dark spectrum captured yet.")
        dark_row.addWidget(self.dark_status_label)
        dark_row.addStretch(1)
        layout.addLayout(dark_row)

        run_row = QHBoxLayout()
        self.spectral_run_button = QPushButton("Run spectral sweep")
        self.spectral_run_button.clicked.connect(self._toggle_spectral_sweep)
        run_row.addWidget(self.spectral_run_button)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.spectral_plot = pg.PlotWidget()
        self.spectral_plot.setLabel("left", "Intensity", units="counts")
        self.spectral_plot.setLabel("bottom", "Wavelength", units="nm")
        self._spectral_curve = self.spectral_plot.plot(pen=pg.mkPen("c", width=2))
        layout.addWidget(self.spectral_plot, 1)

        return tab

    def _build_live_view_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        row.addWidget(QLabel("Integration (ms):"))
        self.live_integration_spin = QDoubleSpinBox()
        self.live_integration_spin.setRange(0.001, 10000)
        self.live_integration_spin.setDecimals(3)
        self.live_integration_spin.setValue(10.0)
        self.live_integration_spin.valueChanged.connect(self._on_live_settings_changed)
        row.addWidget(self.live_integration_spin)

        row.addWidget(QLabel("Averages:"))
        self.live_averages_spin = QSpinBox()
        self.live_averages_spin.setRange(1, 500)
        self.live_averages_spin.setValue(1)
        self.live_averages_spin.valueChanged.connect(self._on_live_settings_changed)
        row.addWidget(self.live_averages_spin)

        self.live_dark_counts_check = QCheckBox("Correct dark counts")
        self.live_dark_counts_check.stateChanged.connect(self._on_live_settings_changed)
        row.addWidget(self.live_dark_counts_check)

        self.live_nonlin_check = QCheckBox("Correct nonlinearity")
        self.live_nonlin_check.stateChanged.connect(self._on_live_settings_changed)
        row.addWidget(self.live_nonlin_check)

        self.live_subtract_dark_check = QCheckBox("Subtract dark spectrum")
        self.live_subtract_dark_check.setEnabled(False)
        row.addWidget(self.live_subtract_dark_check)
        row.addStretch(1)
        layout.addLayout(row)

        run_row = QHBoxLayout()
        self.live_run_button = QPushButton("Start live view")
        self.live_run_button.clicked.connect(self._toggle_live_view)
        run_row.addWidget(self.live_run_button)
        self.live_fps_label = QLabel("")
        run_row.addWidget(self.live_fps_label)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.live_plot = pg.PlotWidget()
        self.live_plot.setLabel("left", "Intensity", units="counts")
        self.live_plot.setLabel("bottom", "Wavelength", units="nm")
        self._live_curve = self.live_plot.plot(pen=pg.mkPen("c", width=2))
        layout.addWidget(self.live_plot, 1)

        return tab

    def _build_transition_batch_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        sweep_row = QHBoxLayout()
        self.tbatch_start_spin, self.tbatch_stop_spin, self.tbatch_step_spin = _add_range_step_row(sweep_row, 450.0, 700.0, 10.0)
        sweep_row.addWidget(QLabel("Repeats:"))
        self.tbatch_repeats_spin = QSpinBox()
        self.tbatch_repeats_spin.setRange(1, 50)
        self.tbatch_repeats_spin.setValue(1)
        sweep_row.addWidget(self.tbatch_repeats_spin)
        self.tbatch_both_directions_check = QCheckBox("Sweep down too (hysteresis check)")
        sweep_row.addWidget(self.tbatch_both_directions_check)
        sweep_row.addStretch(1)
        layout.addLayout(sweep_row)

        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Integration (ms):"))
        self.tbatch_integration_spin = QDoubleSpinBox()
        self.tbatch_integration_spin.setRange(0.001, 10000)
        self.tbatch_integration_spin.setDecimals(3)
        self.tbatch_integration_spin.setValue(1.0)
        settings_row.addWidget(self.tbatch_integration_spin)
        settings_row.addWidget(QLabel("Pre-roll (s):"))
        self.tbatch_preroll_spin = QDoubleSpinBox()
        self.tbatch_preroll_spin.setRange(0.0, 10.0)
        self.tbatch_preroll_spin.setDecimals(2)
        self.tbatch_preroll_spin.setValue(0.05)
        settings_row.addWidget(self.tbatch_preroll_spin)
        settings_row.addWidget(QLabel("Post-roll (s):"))
        self.tbatch_postroll_spin = QDoubleSpinBox()
        self.tbatch_postroll_spin.setRange(0.05, 30.0)
        self.tbatch_postroll_spin.setDecimals(2)
        self.tbatch_postroll_spin.setValue(0.3)
        settings_row.addWidget(self.tbatch_postroll_spin)
        self.tbatch_save_frames_check = QCheckBox("Save full frame data per transition (many large files)")
        settings_row.addWidget(self.tbatch_save_frames_check)
        settings_row.addStretch(1)
        layout.addLayout(settings_row)

        run_row = QHBoxLayout()
        self.tbatch_run_button = QPushButton("Run batch")
        self.tbatch_run_button.clicked.connect(self._toggle_transition_batch)
        run_row.addWidget(self.tbatch_run_button)
        self.tbatch_progress_label = QLabel("")
        run_row.addWidget(self.tbatch_progress_label)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.tbatch_plot = pg.PlotWidget()
        self.tbatch_plot.setLabel("left", "Settle time", units="ms")
        self.tbatch_plot.setLabel("bottom", "Transition midpoint wavelength", units="nm")
        self.tbatch_plot.addLegend()
        self._tbatch_scatter_busy = pg.ScatterPlotItem(pen=None, brush=pg.mkBrush("c"), size=7, name="busy-check")
        self._tbatch_scatter_optical = pg.ScatterPlotItem(pen=None, brush=pg.mkBrush("m"), size=7, name="optical")
        self.tbatch_plot.addItem(self._tbatch_scatter_busy)
        self.tbatch_plot.addItem(self._tbatch_scatter_optical)
        layout.addWidget(self.tbatch_plot, 1)

        return tab

    # -- LCTF connection lifecycle -------------------------------------------

    def _refresh_ports(self) -> None:
        self.port_combo.clear()
        for p in serial.tools.list_ports.comports():
            self.port_combo.addItem(f"{p.device}  ({p.description})", p.device)

    def _toggle_lctf_connection(self) -> None:
        if self._lctf is None:
            self._connect_lctf()
        else:
            self._disconnect_lctf()

    def _connect_lctf(self) -> None:
        if self.port_combo.currentData() is None:
            self._log("No LCTF port selected.")
            return
        port = self.port_combo.currentData()
        baud = int(self.baud_combo.currentText())
        try:
            client = VariSpecClient(port, baudrate=baud)
            client.set_brief_mode()
            info = client.get_info()
            error_code = client.get_error_code()
            if error_code != 0:
                self._log(
                    f"Clearing pre-existing error {error_code} "
                    f"({ERROR_CODE_MEANINGS.get(error_code, 'unknown')}) from before connect."
                )
                client.clear_error()
            start_wavelength = client.get_wavelength()
            temperature = client.get_temperature_c()
            latencies = client.measure_idle_roundtrip_ms(samples=8)
        except (serial.SerialException, ValueError, IndexError, TimeoutError) as exc:
            self._log(f"LCTF connect failed: {exc}")
            return

        self._lctf = client
        self._lctf_info = info
        self._start_wavelength_nm = start_wavelength

        for spin in (
            self.manual_wavelength_spin, self.sweep_start_spin, self.sweep_stop_spin,
            self.palette_start_spin, self.palette_stop_spin,
            self.transition_from_spin, self.transition_to_spin,
            self.spectral_start_spin, self.spectral_stop_spin,
            self.tbatch_start_spin, self.tbatch_stop_spin,
        ):
            spin.setRange(info.min_nm, info.max_nm)
        self.manual_wavelength_spin.setValue(start_wavelength)

        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        self.lctf_status_label.setText(
            f"LCTF: firmware rev {info.firmware_rev}, range {info.min_nm:.1f}-{info.max_nm:.1f} nm, "
            f"serial {info.serial_number}   |   wavelength {start_wavelength:.2f} nm, "
            f"LC temp {temperature:.1f} C   |   idle busy-check round trip: avg {avg_latency:.2f} ms, "
            f"min {min_latency:.2f} ms (measurement resolution floor - see module docstring; "
            f"lower the FTDI Latency Timer to 1ms in Device Manager for finer resolution)"
        )
        self.lctf_connect_button.setText("Disconnect LCTF")
        self._log(f"LCTF connected on {port} @ {baud} baud.")
        self._update_device_dependent_controls()

    def _disconnect_lctf(self) -> None:
        if self._lctf_busy:
            self._log("Cannot disconnect the LCTF while a test using it is running - stop it first.")
            return
        if self._lctf is not None:
            self._lctf.close()
            self._lctf = None
        self._lctf_info = None
        self.lctf_connect_button.setText("Connect LCTF")
        self.lctf_status_label.setText("LCTF: not connected.")
        self._update_device_dependent_controls()

    # -- spectrometer connection lifecycle -----------------------------------

    def _toggle_spectrometer_connection(self) -> None:
        if self._spectrometer is None:
            self._connect_spectrometer()
        else:
            self._disconnect_spectrometer()

    def _connect_spectrometer(self) -> None:
        try:
            spectrometer = OceanSpectrometer()
            limits_us = spectrometer.integration_time_limits_us()
            # A quick throwaway acquisition just to learn the pixel wavelength
            # calibration array (min/max, pixel count) - needed to populate the
            # display-range controls before any real capture has happened.
            probe_settings = AcquisitionSettings(
                integration_time_ms=max(limits_us[0] / 1000.0, 1.0),
                averages=1,
                correct_dark_counts=False,
                correct_nonlinearity=False,
                trigger_mode=0,
            )
            probe_spectrum = spectrometer.acquire_spectrum(probe_settings)
        except Exception as exc:  # noqa: BLE001 - anything uncaught here would leave the GUI permanently "busy"
            self._log(f"Spectrometer connect failed: {exc}")
            return

        self._spectrometer = spectrometer
        self._spectrometer_wavelengths_nm = probe_spectrum.wavelengths_nm
        min_ms = limits_us[0] / 1000.0
        max_ms = limits_us[1] / 1000.0
        wl_min = float(probe_spectrum.wavelengths_nm.min())
        wl_max = float(probe_spectrum.wavelengths_nm.max())

        for spin in (self.transition_integration_spin, self.spectral_integration_spin, self.live_integration_spin):
            spin.setRange(min_ms, max_ms)
        self.transition_integration_spin.setValue(min_ms)
        self.live_integration_spin.setValue(min_ms)
        for spin in (self.display_min_spin, self.display_max_spin):
            spin.setRange(wl_min, wl_max)
        self.display_min_spin.setValue(wl_min)
        self.display_max_spin.setValue(wl_max)

        self.spec_status_label.setText(
            f"Spectrometer: {spectrometer.device_name()}   |   {len(probe_spectrum.wavelengths_nm)} pixels, "
            f"{wl_min:.1f}-{wl_max:.1f} nm   |   integration limits {min_ms:.3f}-{max_ms:.0f} ms   |   "
            f"max intensity {spectrometer.max_intensity():.0f} counts"
        )
        self.spec_connect_button.setText("Disconnect spectrometer")
        self._log(
            f"Spectrometer connected: {spectrometer.device_name()}. "
            f"Note this needs exclusive access - close the sLSPR acquisition app first if it's also running."
        )
        self._update_device_dependent_controls()

    def _disconnect_spectrometer(self) -> None:
        if self._spec_busy:
            self._log("Cannot disconnect the spectrometer while a test using it is running - stop it first.")
            return
        if self._spectrometer is not None:
            self._spectrometer.close()
            self._spectrometer = None
        self._spectrometer_wavelengths_nm = None
        self.spec_connect_button.setText("Connect spectrometer")
        self.spec_status_label.setText("Spectrometer: not connected.")
        self._update_device_dependent_controls()

    def _update_device_dependent_controls(self) -> None:
        self._refresh_enabled_states()

    # -- display helpers (dark subtraction + wavelength range), tabs 4 and 5 --

    def _current_display_range(self) -> tuple[float, float] | None:
        if not self.display_range_check.isChecked():
            return None
        lo, hi = self.display_min_spin.value(), self.display_max_spin.value()
        return (lo, hi) if lo <= hi else (hi, lo)

    def _display_transform(self, wavelengths: np.ndarray, values: np.ndarray, subtract_checkbox: QCheckBox) -> tuple[np.ndarray, np.ndarray]:
        dark_values = None
        if subtract_checkbox.isChecked() and self._dark_spectrum is not None:
            dark_values = self._dark_spectrum.values
        return apply_dark_and_range(wavelengths, values, dark_values, self._current_display_range())

    def _dark_mismatch_warning(self, integration_ms: float, averages: int) -> str | None:
        if self._dark_spectrum is None:
            return None
        dark_integration = self._dark_spectrum.metadata.get("integration_time_ms")
        dark_averages = self._dark_spectrum.metadata.get("averages")
        if dark_integration is not None and abs(dark_integration - integration_ms) > 1e-6:
            return (
                f"dark spectrum was captured at {dark_integration:.3f} ms integration / {dark_averages} "
                f"averages, current setting is {integration_ms:.3f} ms / {averages} averages - dark current "
                f"scales with integration time, so subtraction may not be valid until they match."
            )
        return None

    # -- mutual exclusion -----------------------------------------------------
    #
    # Busy state is tracked per-device (LCTF vs. spectrometer), not globally -
    # a test that only touches one device (e.g. tab 5's live spectrometer
    # view, which never sends the LCTF a byte) must not block controls for
    # the OTHER device (e.g. the manual "go to wavelength" control). Tests
    # that use both devices (transition capture, spectral sweep, batch sweep)
    # set both flags at once.
    #
    # _lctf_active_button / _spec_active_button are two INDEPENDENT slots,
    # not one shared one - each _set_busy() call only touches the slot(s) for
    # the busy flag(s) it's actually setting (guarded by `is not None`, so a
    # call that only passes lctf_busy leaves spec's slot completely alone).
    # This was a real bug: with a single shared "active toggle button" slot,
    # starting live view (spec_busy=True, active=live_run_button) and then
    # clicking the manual "go to wavelength" button (lctf_busy=True, no
    # button - it's a one-shot, not a toggle) overwrote that shared slot to
    # None, making live_run_button look like it belonged to no one - so it
    # went gray and stayed stuck that way, even though live view was still
    # genuinely running in the background. Two independent busy states need
    # two independent "who's allowed to stay clickable" slots.

    def _set_busy(
        self,
        lctf_busy: bool | None = None,
        spec_busy: bool | None = None,
        active_toggle_button: QPushButton | None = None,
    ) -> None:
        if lctf_busy is not None:
            self._lctf_busy = lctf_busy
            self._lctf_active_button = active_toggle_button if lctf_busy else None
        if spec_busy is not None:
            self._spec_busy = spec_busy
            self._spec_active_button = active_toggle_button if spec_busy else None
        self._refresh_enabled_states()

    def _refresh_enabled_states(self) -> None:
        lctf_ready = self._lctf is not None
        spec_ready = self._spectrometer is not None

        def usable_lctf(widget: QPushButton) -> bool:
            return widget is self._lctf_active_button or not self._lctf_busy

        def usable_spec(widget: QPushButton) -> bool:
            return widget is self._spec_active_button or not self._spec_busy

        def usable_both(widget: QPushButton) -> bool:
            return usable_lctf(widget) and usable_spec(widget)

        self.lctf_connect_button.setEnabled(usable_lctf(self.lctf_connect_button))
        self.spec_connect_button.setEnabled(usable_spec(self.spec_connect_button))
        self.manual_go_button.setEnabled(lctf_ready and usable_lctf(self.manual_go_button))
        self.manual_wavelength_spin.setEnabled(lctf_ready)
        self.sweep_run_button.setEnabled(lctf_ready and usable_lctf(self.sweep_run_button))
        self.palette_run_button.setEnabled(lctf_ready and usable_lctf(self.palette_run_button))
        self.transition_run_button.setEnabled(lctf_ready and spec_ready and usable_both(self.transition_run_button))
        self.spectral_run_button.setEnabled(lctf_ready and spec_ready and usable_both(self.spectral_run_button))
        self.live_run_button.setEnabled(spec_ready and usable_spec(self.live_run_button))
        self.dark_capture_button.setEnabled(spec_ready and usable_spec(self.dark_capture_button))
        self.tbatch_run_button.setEnabled(lctf_ready and spec_ready and usable_both(self.tbatch_run_button))

    # -- manual control ---------------------------------------------------

    def _on_manual_go(self) -> None:
        if self._lctf_busy or self._lctf is None:
            return
        target = self.manual_wavelength_spin.value()
        self._set_busy(lctf_busy=True)
        self.manual_result_label.setText("Moving...")
        worker = ManualMoveWorker(self._lctf, target)
        worker.finished_ok.connect(self._on_manual_move_done)
        worker.failed.connect(self._on_manual_move_failed)
        self._manual_worker = worker
        worker.start()

    def _on_manual_move_done(self, confirmed_nm: float, settle_ms: float, polls: int, temperature_c: float, error_code: int) -> None:
        flag = f"  [error {error_code}: {ERROR_CODE_MEANINGS.get(error_code, 'unknown')}]" if error_code else ""
        self.manual_result_label.setText(
            f"-> {confirmed_nm:.2f} nm, settle {settle_ms:.2f} ms ({polls} polls), LC {temperature_c:.1f} C{flag}"
        )
        self._manual_worker = None
        self._set_busy(lctf_busy=False)

    def _on_manual_move_failed(self, message: str) -> None:
        self.manual_result_label.setText(f"Failed: {message}")
        self._manual_worker = None
        self._set_busy(lctf_busy=False)

    # -- tab 1: settle-time sweep --------------------------------------------

    def _toggle_sweep(self) -> None:
        if self._sweep_worker is None:
            self._start_sweep()
        else:
            self._sweep_worker.stop()
            self.sweep_run_button.setText("Stopping...")

    def _start_sweep(self) -> None:
        assert self._lctf is not None and self._start_wavelength_nm is not None
        wavelengths = build_wavelength_list(self.sweep_start_spin.value(), self.sweep_stop_spin.value(), self.sweep_step_spin.value())
        if not wavelengths:
            self._log("Sweep range/step produced no wavelengths - nothing to do.")
            return

        self._settle_results = []
        self._sweep_scatter_up.clear()
        self._sweep_scatter_down.clear()
        self.sweep_export_button.setEnabled(False)
        self._log(
            f"Starting settle-time sweep: {len(wavelengths)} wavelengths, "
            f"{self.sweep_repeats_spin.value()} repeat(s), "
            f"{'both directions' if self.sweep_both_directions_check.isChecked() else 'up only'}."
        )

        worker = SweepWorker(
            client=self._lctf,
            wavelengths_up=wavelengths,
            both_directions=self.sweep_both_directions_check.isChecked(),
            repeats=self.sweep_repeats_spin.value(),
            return_to_nm=self._start_wavelength_nm,
        )
        worker.pointReady.connect(self._on_sweep_point)
        worker.logMessage.connect(self._log)
        worker.finished_ok.connect(self._on_sweep_finished)
        worker.failed.connect(self._on_sweep_failed)
        self._sweep_worker = worker
        self._set_busy(lctf_busy=True, active_toggle_button=self.sweep_run_button)
        worker.start()
        self.sweep_run_button.setText("Stop sweep")

    def _on_sweep_point(self, m: SettleMeasurement) -> None:
        self._settle_results.append(m)
        scatter = self._sweep_scatter_up if m.direction == "up" else self._sweep_scatter_down
        scatter.addPoints([{"pos": (m.requested_nm, m.settle_ms)}])
        flag = f"  [error {m.error_code}]" if m.error_code else ""
        self._log(
            f"#{m.index:03d} {m.direction:4s} rep{m.repeat} {m.requested_nm:7.2f} nm -> "
            f"confirmed {m.confirmed_nm:7.2f} nm, settle {m.settle_ms:7.2f} ms ({m.poll_count} polls), "
            f"LC {m.temperature_c:5.1f} C{flag}"
        )

    def _on_sweep_finished(self, points_measured: int, error_count: int) -> None:
        self.sweep_run_button.setText("Run sweep")
        self._sweep_worker = None
        self._set_busy(lctf_busy=False)
        self.sweep_export_button.setEnabled(bool(self._settle_results))
        if self._settle_results:
            settle_values = [r.settle_ms for r in self._settle_results if r.error_code == 0]
            if settle_values:
                self._log(
                    f"Sweep finished: {points_measured} points, {error_count} error(s). "
                    f"Settle time: min {min(settle_values):.2f} ms, "
                    f"avg {sum(settle_values) / len(settle_values):.2f} ms, max {max(settle_values):.2f} ms."
                )
            else:
                self._log(f"Sweep finished: {points_measured} points, all {error_count} flagged as errors.")

    def _on_sweep_failed(self, message: str) -> None:
        self.sweep_run_button.setText("Run sweep")
        self._sweep_worker = None
        self._set_busy(lctf_busy=False)
        self._log(f"Sweep failed: {message}")

    def _export_sweep_csv(self) -> None:
        if not self._settle_results:
            return
        default_name = f"lctf_settle_time_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path_str, _ = QFileDialog.getSaveFileName(self, "Export settle-time CSV", default_name, "CSV files (*.csv)")
        if not path_str:
            return
        with open(path_str, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["index", "direction", "repeat", "requested_nm", "confirmed_nm",
                 "settle_ms", "poll_count", "temperature_c", "error_code"]
            )
            for m in self._settle_results:
                writer.writerow(
                    [m.index, m.direction, m.repeat, m.requested_nm, m.confirmed_nm,
                     m.settle_ms, m.poll_count, m.temperature_c, m.error_code]
                )
        self._log(f"Exported {len(self._settle_results)} rows to {path_str}")

    # -- tab 2: palette vs direct --------------------------------------------

    def _toggle_palette_compare(self) -> None:
        if self._palette_worker is None:
            self._start_palette_compare()
        else:
            self._palette_worker.stop()
            self.palette_run_button.setText("Stopping...")

    def _start_palette_compare(self) -> None:
        assert self._lctf is not None and self._start_wavelength_nm is not None
        wavelengths = build_wavelength_list(self.palette_start_spin.value(), self.palette_stop_spin.value(), self.palette_step_spin.value())
        if not wavelengths:
            self._log("Palette range/step produced no wavelengths - nothing to do.")
            return

        self._palette_results = []
        self._palette_scatter_direct.clear()
        self._palette_scatter_palette.clear()
        self.palette_export_button.setEnabled(False)
        self._log(f"Starting palette-vs-direct comparison: {len(wavelengths)} wavelengths, {self.palette_repeats_spin.value()} repeat(s).")

        worker = PaletteCompareWorker(
            client=self._lctf,
            wavelengths=wavelengths,
            repeats=self.palette_repeats_spin.value(),
            return_to_nm=self._start_wavelength_nm,
        )
        worker.pointReady.connect(self._on_palette_point)
        worker.logMessage.connect(self._log)
        worker.finished_ok.connect(self._on_palette_finished)
        worker.failed.connect(self._on_palette_failed)
        self._palette_worker = worker
        self._set_busy(lctf_busy=True, active_toggle_button=self.palette_run_button)
        worker.start()
        self.palette_run_button.setText("Stop comparison")

    def _on_palette_point(self, m: PaletteCompareMeasurement) -> None:
        self._palette_results.append(m)
        scatter = self._palette_scatter_direct if m.method == "direct" else self._palette_scatter_palette
        scatter.addPoints([{"pos": (m.requested_nm, m.settle_ms)}])
        flag = f"  [error {m.error_code}]" if m.error_code else ""
        self._log(
            f"#{m.index:03d} {m.method:7s} rep{m.repeat} {m.requested_nm:7.2f} nm -> "
            f"confirmed {m.confirmed_nm:7.2f} nm, settle {m.settle_ms:7.2f} ms ({m.poll_count} polls){flag}"
        )

    def _on_palette_finished(self, points_measured: int, error_count: int) -> None:
        self.palette_run_button.setText("Run comparison")
        self._palette_worker = None
        self._set_busy(lctf_busy=False)
        self.palette_export_button.setEnabled(bool(self._palette_results))
        direct_ms = [r.settle_ms for r in self._palette_results if r.method == "direct" and r.error_code == 0]
        palette_ms = [r.settle_ms for r in self._palette_results if r.method == "palette" and r.error_code == 0]
        if direct_ms and palette_ms:
            avg_direct = sum(direct_ms) / len(direct_ms)
            avg_palette = sum(palette_ms) / len(palette_ms)
            self._log(
                f"Comparison finished: {points_measured} points, {error_count} error(s). "
                f"avg direct(W)={avg_direct:.2f} ms, avg palette(P)={avg_palette:.2f} ms, "
                f"difference={avg_direct - avg_palette:+.2f} ms "
                f"(manual claims palette saves 'perhaps one or two ms' - compare against that, "
                f"but see the measurement-resolution caveat in the module docstring first)."
            )

    def _on_palette_failed(self, message: str) -> None:
        self.palette_run_button.setText("Run comparison")
        self._palette_worker = None
        self._set_busy(lctf_busy=False)
        self._log(f"Comparison failed: {message}")

    def _export_palette_csv(self) -> None:
        if not self._palette_results:
            return
        default_name = f"lctf_palette_vs_direct_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path_str, _ = QFileDialog.getSaveFileName(self, "Export palette comparison CSV", default_name, "CSV files (*.csv)")
        if not path_str:
            return
        with open(path_str, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["index", "method", "repeat", "requested_nm", "confirmed_nm", "settle_ms", "poll_count", "error_code"]
            )
            for m in self._palette_results:
                writer.writerow(
                    [m.index, m.method, m.repeat, m.requested_nm, m.confirmed_nm, m.settle_ms, m.poll_count, m.error_code]
                )
        self._log(f"Exported {len(self._palette_results)} rows to {path_str}")

    # -- tab 3: optical transition capture -----------------------------------

    def _toggle_transition_capture(self) -> None:
        if self._transition_worker is None:
            self._start_transition_capture()
        else:
            self._transition_worker.stop()
            self.transition_run_button.setText("Stopping...")

    def _start_transition_capture(self) -> None:
        assert self._lctf is not None and self._spectrometer is not None
        from_nm = self.transition_from_spin.value()
        to_nm = self.transition_to_spin.value()
        if from_nm == to_nm:
            self._log("From and To wavelengths are the same - pick two different values.")
            return

        self._transition_curve_from.setData([], [])
        self._transition_curve_to.setData([], [])
        self.transition_result_label.setText("Capturing...")

        worker = TransitionCaptureWorker(
            lctf=self._lctf,
            spectrometer=self._spectrometer,
            from_nm=from_nm,
            to_nm=to_nm,
            integration_time_ms=self.transition_integration_spin.value(),
            pre_roll_s=self.transition_preroll_spin.value(),
            post_roll_s=self.transition_postroll_spin.value(),
            save_dir=RESULTS_DIR,
        )
        worker.logMessage.connect(self._log)
        worker.finished_ok.connect(self._on_transition_finished)
        worker.failed.connect(self._on_transition_failed)
        self._transition_worker = worker
        self._set_busy(lctf_busy=True, spec_busy=True, active_toggle_button=self.transition_run_button)
        worker.start()
        self.transition_run_button.setText("Stop capture")

    def _on_transition_finished(self, summary: dict) -> None:
        self.transition_run_button.setText("Run capture")
        self._transition_worker = None
        self._set_busy(lctf_busy=False, spec_busy=False)

        if not summary.get("frames_count"):
            self.transition_result_label.setText("No frames captured (stopped too early or an error occurred).")
            return

        t_ms = summary["t_ms"]
        intensities = summary["intensities"]
        from_pixel = summary["from_pixel"]
        to_pixel = summary["to_pixel"]
        # Legend entries are fixed labels set once at tab-build time ("source
        # (from)" / "destination (to)") rather than re-labeled with the
        # actual nm values per run - see the persistent-curve comment in
        # _build_transition_tab for why these items are never recreated.
        # The actual from/to values are in transition_result_label below.
        self._transition_curve_from.setData(t_ms, intensities[:, from_pixel])
        self._transition_curve_to.setData(t_ms, intensities[:, to_pixel])

        busy_ms = summary["busy_check_settle_ms"]
        optical_ms = summary["optical_settle_ms_90pct"]
        optical_text = "n/a - inspect plot/CSV" if optical_ms is None else f"{optical_ms:.2f} ms"
        self.transition_result_label.setText(
            f"{summary['from_nm']:.1f} -> {summary['to_nm']:.1f} nm   |   Busy-check settle: {busy_ms:.2f} ms   |   "
            f"Optical settle (90% rise at destination pixel): {optical_text}   |   "
            f"achieved ~{summary['achieved_fps']:.0f} fps   |   saved to {summary['csv_path']}"
        )

    def _on_transition_failed(self, message: str) -> None:
        self.transition_run_button.setText("Run capture")
        self._transition_worker = None
        self._set_busy(lctf_busy=False, spec_busy=False)
        self.transition_result_label.setText(f"Failed: {message}")
        self._log(f"Transition capture failed: {message}")

    # -- tab 4: optical spectral sweep ---------------------------------------

    def _toggle_spectral_sweep(self) -> None:
        if self._spectral_worker is None:
            self._start_spectral_sweep()
        else:
            self._spectral_worker.stop()
            self.spectral_run_button.setText("Stopping...")

    def _start_spectral_sweep(self) -> None:
        assert self._lctf is not None and self._spectrometer is not None and self._start_wavelength_nm is not None
        wavelengths = build_wavelength_list(self.spectral_start_spin.value(), self.spectral_stop_spin.value(), self.spectral_step_spin.value())
        if not wavelengths:
            self._log("Spectral sweep range/step produced no wavelengths - nothing to do.")
            return

        settings = AcquisitionSettings(
            integration_time_ms=self.spectral_integration_spin.value(),
            averages=self.spectral_averages_spin.value(),
            correct_dark_counts=self.spectral_dark_check.isChecked(),
            correct_nonlinearity=self.spectral_nonlin_check.isChecked(),
            trigger_mode=0,
        )
        if self.spectral_subtract_dark_check.isChecked():
            warning = self._dark_mismatch_warning(settings.integration_time_ms, settings.averages)
            if warning:
                self._log(f"Warning: {warning}")
        self._log(
            f"Starting optical spectral sweep: {len(wavelengths)} wavelengths, "
            f"integration {settings.integration_time_ms:.3f} ms, averages {settings.averages}. "
            f"Spectra will auto-save incrementally to {RESULTS_DIR}."
        )

        worker = SpectralSweepWorker(
            lctf=self._lctf,
            spectrometer=self._spectrometer,
            wavelengths=wavelengths,
            settings=settings,
            extra_settle_margin_ms=self.spectral_margin_spin.value(),
            save_dir=RESULTS_DIR,
            return_to_nm=self._start_wavelength_nm,
            dark_spectrum=self._dark_spectrum,
        )
        worker.pointReady.connect(self._on_spectral_point)
        worker.logMessage.connect(self._log)
        worker.finished_ok.connect(self._on_spectral_finished)
        worker.failed.connect(self._on_spectral_failed)
        self._spectral_worker = worker
        self._set_busy(lctf_busy=True, spec_busy=True, active_toggle_button=self.spectral_run_button)
        worker.start()
        self.spectral_run_button.setText("Stop sweep")

    def _on_spectral_point(self, p: SpectralSweepPoint) -> None:
        wavelengths, values = self._display_transform(p.wavelengths_nm, p.intensities, self.spectral_subtract_dark_check)
        self._spectral_curve.setData(wavelengths, values)
        self._log(
            f"#{p.index:03d} {p.requested_nm:7.2f} nm -> confirmed {p.confirmed_nm:7.2f} nm, "
            f"settle {p.settle_ms:7.2f} ms, LC {p.temperature_c:5.1f} C, peak {p.intensities.max():.0f} counts"
        )

    def _on_spectral_finished(self, points_measured: int, csv_path: str) -> None:
        self.spectral_run_button.setText("Run spectral sweep")
        self._spectral_worker = None
        self._set_busy(lctf_busy=False, spec_busy=False)
        self._log(f"Spectral sweep finished: {points_measured} spectra saved to {csv_path}")

    def _on_spectral_failed(self, message: str) -> None:
        self.spectral_run_button.setText("Run spectral sweep")
        self._spectral_worker = None
        self._set_busy(lctf_busy=False, spec_busy=False)
        self._log(f"Spectral sweep failed: {message}")

    # -- dark spectrum capture (shared by tabs 4 and 5) ----------------------

    def _on_capture_dark(self) -> None:
        if self._spec_busy or self._spectrometer is None:
            return
        settings = AcquisitionSettings(
            integration_time_ms=self.spectral_integration_spin.value(),
            averages=self.spectral_averages_spin.value(),
            correct_dark_counts=self.spectral_dark_check.isChecked(),
            correct_nonlinearity=self.spectral_nonlin_check.isChecked(),
            trigger_mode=0,
        )
        self.dark_status_label.setText("Capturing dark spectrum...")
        self._set_busy(spec_busy=True)
        worker = DarkCaptureWorker(self._spectrometer, settings)
        worker.finished_ok.connect(self._on_dark_captured)
        worker.failed.connect(self._on_dark_capture_failed)
        self._dark_worker = worker
        worker.start()

    def _on_dark_captured(self, spectrum: object) -> None:
        self._dark_spectrum = spectrum
        self._dark_worker = None
        self._set_busy(spec_busy=False)

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / f"dark_spectrum_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["wavelength_nm", "intensity"])
            for w, v in zip(spectrum.wavelengths_nm, spectrum.values):
                writer.writerow([f"{w:.3f}", v])

        integration_ms = spectrum.metadata.get("integration_time_ms")
        averages = spectrum.metadata.get("averages")
        self.dark_status_label.setText(
            f"Dark spectrum captured: {integration_ms:.3f} ms x {averages} avg, "
            f"peak {spectrum.values.max():.0f} counts."
        )
        self.spectral_subtract_dark_check.setEnabled(True)
        self.live_subtract_dark_check.setEnabled(True)
        self._log(f"Dark spectrum captured and saved to {path}")

    def _on_dark_capture_failed(self, message: str) -> None:
        self._dark_worker = None
        self._set_busy(spec_busy=False)
        self.dark_status_label.setText(f"Dark capture failed: {message}")
        self._log(f"Dark capture failed: {message}")

    # -- tab 5: live spectrometer view ---------------------------------------

    def _toggle_live_view(self) -> None:
        if self._live_worker is None:
            self._start_live_view()
        else:
            self._stop_live_view()

    def _start_live_view(self) -> None:
        assert self._spectrometer is not None
        settings = AcquisitionSettings(
            integration_time_ms=self.live_integration_spin.value(),
            averages=self.live_averages_spin.value(),
            correct_dark_counts=self.live_dark_counts_check.isChecked(),
            correct_nonlinearity=self.live_nonlin_check.isChecked(),
            trigger_mode=0,
        )
        if self.live_subtract_dark_check.isChecked():
            warning = self._dark_mismatch_warning(settings.integration_time_ms, settings.averages)
            if warning:
                self._log(f"Warning: {warning}")

        self._live_settings_box = LiveSettingsBox(settings)
        self._live_frame_box = LatestFrameBox()
        self._live_last_frame_count = None
        self._live_last_tick_time = None

        worker = LiveViewWorker(self._spectrometer, self._live_settings_box, self._live_frame_box)
        worker.failed.connect(self._on_live_view_failed)
        self._live_worker = worker
        self._set_busy(spec_busy=True, active_toggle_button=self.live_run_button)
        worker.start()
        self._live_timer.start()
        self.live_run_button.setText("Stop live view")
        self._log("Live spectrometer view started - integration time/averages/corrections can be changed while running.")

    def _stop_live_view(self) -> None:
        if self._live_worker is not None:
            self._live_worker.stop()
            if not self._live_worker.wait(5000):
                self._log(
                    "Live view worker did not stop promptly (a long integration/average was likely still in "
                    "flight) - wait a moment before starting another test."
                )
            self._live_worker = None
        self._live_timer.stop()
        self._live_settings_box = None
        self._live_frame_box = None
        self.live_run_button.setText("Start live view")
        self._set_busy(spec_busy=False)
        self._log("Live spectrometer view stopped.")

    def _on_live_view_failed(self, message: str) -> None:
        self._log(f"Live view failed: {message}")
        self._stop_live_view()

    def _on_live_settings_changed(self, *_args: object) -> None:
        if self._live_settings_box is None:
            return
        self._live_settings_box.set(
            AcquisitionSettings(
                integration_time_ms=self.live_integration_spin.value(),
                averages=self.live_averages_spin.value(),
                correct_dark_counts=self.live_dark_counts_check.isChecked(),
                correct_nonlinearity=self.live_nonlin_check.isChecked(),
                trigger_mode=0,
            )
        )

    def _on_live_timer_tick(self) -> None:
        if self._live_frame_box is None:
            return
        spectrum, frame_count = self._live_frame_box.get()
        if spectrum is None:
            return

        now = time.perf_counter()
        if self._live_last_frame_count is not None and self._live_last_tick_time is not None:
            delta_frames = frame_count - self._live_last_frame_count
            delta_t = now - self._live_last_tick_time
            fps = delta_frames / delta_t if delta_t > 0 else 0.0
            self.live_fps_label.setText(f"{fps:.1f} fps ({frame_count} frames total)")
        self._live_last_frame_count = frame_count
        self._live_last_tick_time = now

        wavelengths, values = self._display_transform(spectrum.wavelengths_nm, spectrum.values, self.live_subtract_dark_check)
        self._live_curve.setData(wavelengths, values)

    # -- tab 6: optical transition batch sweep -------------------------------

    def _toggle_transition_batch(self) -> None:
        if self._transition_batch_worker is None:
            self._start_transition_batch()
        else:
            self._transition_batch_worker.stop()
            self.tbatch_run_button.setText("Stopping...")

    def _start_transition_batch(self) -> None:
        assert self._lctf is not None and self._spectrometer is not None and self._start_wavelength_nm is not None
        wavelengths = build_wavelength_list(self.tbatch_start_spin.value(), self.tbatch_stop_spin.value(), self.tbatch_step_spin.value())
        if len(wavelengths) < 2:
            self._log("Batch range/step produced fewer than 2 wavelengths - nothing to transition between.")
            return

        both = self.tbatch_both_directions_check.isChecked()
        repeats = self.tbatch_repeats_spin.value()
        pre_roll = self.tbatch_preroll_spin.value()
        post_roll = self.tbatch_postroll_spin.value()
        n_transitions = (len(wavelengths) - 1) * (2 if both else 1) * repeats
        est_s = n_transitions * (pre_roll + post_roll + 0.15)  # +0.15s: park/busy-check/return overhead, from measurement

        self._tbatch_results = []
        self._tbatch_scatter_busy.clear()
        self._tbatch_scatter_optical.clear()
        self._log(
            f"Starting transition batch: {len(wavelengths)} wavelengths ({self.tbatch_step_spin.value():.1f} nm step), "
            f"{n_transitions} transitions total ({'both directions, ' if both else ''}{repeats} repeat(s)), "
            f"estimated ~{est_s:.0f}s. Summary CSV will auto-save incrementally to {RESULTS_DIR}."
        )

        worker = TransitionBatchWorker(
            lctf=self._lctf,
            spectrometer=self._spectrometer,
            wavelengths_up=wavelengths,
            both_directions=both,
            repeats=repeats,
            integration_time_ms=self.tbatch_integration_spin.value(),
            pre_roll_s=pre_roll,
            post_roll_s=post_roll,
            save_full_frames=self.tbatch_save_frames_check.isChecked(),
            save_dir=RESULTS_DIR,
            return_to_nm=self._start_wavelength_nm,
        )
        worker.pointReady.connect(self._on_tbatch_point)
        worker.logMessage.connect(self._log)
        worker.finished_ok.connect(self._on_tbatch_finished)
        worker.failed.connect(self._on_tbatch_failed)
        self._transition_batch_worker = worker
        self._set_busy(lctf_busy=True, spec_busy=True, active_toggle_button=self.tbatch_run_button)
        worker.start()
        self.tbatch_run_button.setText("Stop batch")

    def _on_tbatch_point(self, p: TransitionBatchPoint) -> None:
        self._tbatch_results.append(p)
        midpoint = (p.from_nm + p.to_nm) / 2.0
        self._tbatch_scatter_busy.addPoints([{"pos": (midpoint, p.busy_check_settle_ms)}])
        if p.optical_settle_ms_90pct is not None:
            self._tbatch_scatter_optical.addPoints([{"pos": (midpoint, p.optical_settle_ms_90pct)}])
        self.tbatch_progress_label.setText(f"{len(self._tbatch_results)} transitions measured...")

    def _on_tbatch_finished(self, points_measured: int, error_count: int, summary_csv_path: str) -> None:
        self.tbatch_run_button.setText("Run batch")
        self._transition_batch_worker = None
        self._set_busy(lctf_busy=False, spec_busy=False)
        busy_values = [p.busy_check_settle_ms for p in self._tbatch_results if p.error_code == 0]
        optical_values = [p.optical_settle_ms_90pct for p in self._tbatch_results if p.error_code == 0 and p.optical_settle_ms_90pct is not None]
        summary_bits = [f"Batch finished: {points_measured} transitions, {error_count} error(s)."]
        if busy_values:
            summary_bits.append(
                f"busy-check: min {min(busy_values):.2f} / avg {sum(busy_values) / len(busy_values):.2f} / "
                f"max {max(busy_values):.2f} ms"
            )
        if optical_values:
            summary_bits.append(
                f"optical: min {min(optical_values):.2f} / avg {sum(optical_values) / len(optical_values):.2f} / "
                f"max {max(optical_values):.2f} ms"
            )
        summary_bits.append(f"saved to {summary_csv_path}")
        self._log("   ".join(summary_bits))

    def _on_tbatch_failed(self, message: str) -> None:
        self.tbatch_run_button.setText("Run batch")
        self._transition_batch_worker = None
        self._set_busy(lctf_busy=False, spec_busy=False)
        self._log(f"Transition batch failed: {message}")

    # -- misc ---------------------------------------------------------------

    def _log(self, text: str) -> None:
        self.log.append(text)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        for worker in (
            self._sweep_worker, self._palette_worker, self._transition_worker, self._spectral_worker,
            self._transition_batch_worker, self._live_worker,
        ):
            if worker is not None:
                worker.stop()
        self._live_timer.stop()
        for worker in (
            self._sweep_worker, self._palette_worker, self._transition_worker, self._spectral_worker,
            self._transition_batch_worker, self._live_worker, self._manual_worker, self._dark_worker,
        ):
            if worker is not None:
                worker.wait(3000)
        if self._lctf is not None:
            self._lctf.close()
        if self._spectrometer is not None:
            self._spectrometer.close()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
