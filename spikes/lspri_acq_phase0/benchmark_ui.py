"""LSPRimaging Acquisition — Phase 0 throughput spike, with a live preview.

Not part of any app. Purpose: measure real camera capture rate and per-frame
ROI-extraction cost on the actual camera. NOTE: there is no fixed rate target -
the goal is "as fast as achievable", not "clear 16 Hz". 16 Hz appears in labels
below only as a historical reference point from early planning, not a pass/fail
line. See docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md, §3.

Supports two camera vendors behind a small CameraBackend interface (see
below), auto-detecting whichever is plugged in - no model check, and no
hardcoded resolution/format/exposure assumptions:
- Basler, via pypylon (GenICam). Tested against both the a2A3840-45umBAS
  (3840x2160, 45fps) and the acA5472-17um (5472x3648, 17fps).
- IDS uEye, via pyueye (tested against a UI-3160CP-M-GL Rev.2.1). pyueye is a
  ctypes wrapper around IDS's own ueye_api.dll driver, which ships separately
  in IDS's "IDS Software Suite" installer (not pip-installable) - `from
  pyueye import ueye` fails at import time if that driver isn't installed, so
  this backend is written NOT TO BE IMPORTED until it's actually
  selected/probed (see UeyeBackend's docstring). Verified end-to-end on real
  hardware: model/resolution readback, Mono8/Mono10/Mono12 capture, and 2x2
  binning all confirmed correct.

Pixel format, binning, and exposure are all validated/adapted against
whatever camera actually connects (see each backend's apply_* methods) and
logged rather than failing outright if a different model doesn't support what
was requested.

Run: .venv\\Scripts\\python.exe spikes\\lspri_acq_phase0\\benchmark_ui.py

What this shows:
- A live preview (so you can confirm focus/exposure/framing while measuring -
  a pure console script can't do that).
- Camera selection (auto-detect or force a specific vendor), pixel format,
  hardware binning (1x/2x/4x - trades resolution for lower data volume while
  keeping the full field of view), and exposure time, all adjustable and
  applied on the next "Start capture".
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
import warnings
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


# ── Camera backend interface ────────────────────────────────────────────────
#
# CameraGrabThread drives cameras only through this interface, never through a
# vendor SDK directly - that's what lets it support Basler and IDS (and any
# future vendor) without knowing which one is plugged in. Each backend owns
# all vendor-specific translation (units, struct types, capability queries)
# internally so the rest of this tool stays vendor-neutral (exposure is
# always microseconds here, regardless of what the native SDK uses).

class CameraBackend(ABC):
    def __init__(self) -> None:
        self.model_name: str = "unknown"

    @staticmethod
    @abstractmethod
    def count_available() -> int:
        """Number of cameras this backend can see, without opening one.
        Must NEVER raise - return 0 if the vendor SDK/driver isn't even
        installed. Used for auto-detect and must stay cheap/side-effect-free."""

    @abstractmethod
    def open(self) -> None:
        """Open the first available device and populate self.model_name."""

    @abstractmethod
    def native_size(self) -> tuple[int, int]:
        """(width, height) at full sensor resolution, no binning applied."""

    @abstractmethod
    def apply_pixel_format(self, requested: str, log: Callable[[str], None]) -> str:
        """Apply `requested` ("Mono8"/"Mono10"/"Mono12") if supported, else
        fall back to something the camera does support and log why. Returns
        the format actually applied."""

    @abstractmethod
    def apply_binning(self, n: int, log: Callable[[str], None]) -> None:
        """Apply NxN binning if supported, else log and leave at 1x1."""

    @abstractmethod
    def apply_exposure_us(self, requested_us: float, log: Callable[[str], None]) -> None:
        """Apply exposure time (microseconds), clamped to the camera's real
        range with a log message if the requested value was out of range."""

    @abstractmethod
    def maximize_throughput(self, log: Callable[[str], None]) -> None:
        """Undo any conservative post-connect defaults (e.g. a low pixel
        clock) that would cap achievable fps below what the current format/
        binning/AOI can actually sustain - a no-op where the vendor SDK
        doesn't need it (e.g. Basler already runs at full capability by
        default). Call after apply_binning (frame size affects the ceiling)
        and before apply_exposure_us."""

    @abstractmethod
    def start_grabbing(self) -> None: ...

    @abstractmethod
    def retrieve_frame(self, timeout_ms: int) -> np.ndarray | None:
        """Block up to timeout_ms for the next frame. Returns None for "no
        frame this cycle, not fatal" (e.g. a dropped grab); raises for a real
        error (e.g. camera unplugged, hard timeout)."""

    @abstractmethod
    def stop_grabbing(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class PylonBackend(CameraBackend):
    """Basler cameras via pypylon (GenICam)."""

    def __init__(self) -> None:
        super().__init__()
        self._pylon = None
        self.cam = None

    @staticmethod
    def count_available() -> int:
        try:
            from pypylon import pylon
        except Exception:
            return 0
        try:
            tlf = pylon.TlFactory.GetInstance()
            return len(tlf.EnumerateDevices())
        except Exception:
            return 0

    def open(self) -> None:
        from pypylon import pylon
        self._pylon = pylon
        tlf = pylon.TlFactory.GetInstance()
        devices = tlf.EnumerateDevices()
        if not devices:
            raise RuntimeError("No Basler camera found.")
        self.cam = pylon.InstantCamera(tlf.CreateDevice(devices[0]))
        self.cam.Open()
        self.model_name = self.cam.DeviceModelName.Value

    def native_size(self) -> tuple[int, int]:
        return int(self.cam.Width.Max), int(self.cam.Height.Max)

    def apply_pixel_format(self, requested: str, log: Callable[[str], None]) -> str:
        # Different camera models expose different pixel-format sets - fall
        # back to whatever the connected camera actually supports instead of
        # failing outright on a format chosen against a different model.
        pixel_format = requested
        try:
            supported_formats = list(self.cam.PixelFormat.Symbolics)
        except Exception:
            supported_formats = []
        if supported_formats and pixel_format not in supported_formats:
            fallback = supported_formats[0]
            log(
                f"{self.model_name} does not support pixel format {pixel_format} "
                f"(supports: {', '.join(supported_formats)}) - using {fallback} instead."
            )
            pixel_format = fallback
        self.cam.PixelFormat.SetValue(pixel_format)
        return pixel_format

    def apply_binning(self, n: int, log: Callable[[str], None]) -> None:
        # Binning reduces resolution while keeping the full field of view
        # (combines NxN sensor pixels into one output pixel) - unlike
        # Width/Height/Offset cropping, which keeps full pixel resolution
        # but narrows the FOV. Average mode (not Sum) keeps binned pixel
        # values in roughly the same intensity range as unbinned, which
        # matters once this feeds into intensity-ratio/absorbance math.
        # Some camera models don't expose binning at all - degrade to 1x1
        # rather than failing the whole connection over it.
        try:
            self.cam.BinningHorizontal.SetValue(n)
            self.cam.BinningVertical.SetValue(n)
            try:
                self.cam.BinningHorizontalMode.SetValue("Average")
                self.cam.BinningVerticalMode.SetValue("Average")
            except Exception:
                pass  # older/other models may not expose the mode selector
        except Exception as exc:
            if n != 1:
                log(f"{self.model_name} does not support {n}x binning ({exc}) - continuing at 1x1.")
        self.cam.Width.SetValue(self.cam.Width.Max)
        self.cam.Height.SetValue(self.cam.Height.Max)

    def apply_exposure_us(self, requested_us: float, log: Callable[[str], None]) -> None:
        exposure_us = requested_us
        try:
            lo, hi = self.cam.ExposureTime.Min, self.cam.ExposureTime.Max
            if not (lo <= exposure_us <= hi):
                clamped = min(max(exposure_us, lo), hi)
                log(
                    f"Requested exposure {exposure_us:.0f}us is outside {self.model_name}'s range "
                    f"({lo:.0f}-{hi:.0f}us) - clamped to {clamped:.0f}us."
                )
                exposure_us = clamped
        except Exception:
            pass  # if the range can't be read, let SetValue below surface the real error
        self.cam.ExposureTime.SetValue(exposure_us)

    def maximize_throughput(self, log: Callable[[str], None]) -> None:
        pass  # pypylon cameras already run at full capability by default - nothing to unlock

    def start_grabbing(self) -> None:
        self.cam.StartGrabbing(self._pylon.GrabStrategy_LatestImageOnly)

    def retrieve_frame(self, timeout_ms: int) -> np.ndarray | None:
        result = self.cam.RetrieveResult(timeout_ms, self._pylon.TimeoutHandling_ThrowException)
        try:
            if result.GrabSucceeded():
                return result.Array.copy()  # copy out of pylon's internal buffer before releasing
            return None
        finally:
            result.Release()

    def stop_grabbing(self) -> None:
        if self.cam is not None and self.cam.IsGrabbing():
            self.cam.StopGrabbing()

    def close(self) -> None:
        if self.cam is not None and self.cam.IsOpen():
            self.cam.Close()


class UeyeBackend(CameraBackend):
    """IDS uEye cameras via pyueye - a low-level ctypes wrapper around IDS's
    own ueye_api.dll. That DLL is NOT part of the pip package - it ships with
    IDS's separate "IDS Software Suite" / "IDS peak" installer, and `from
    pyueye import ueye` fails at IMPORT time (not just at use) if that driver
    isn't installed. So unlike PylonBackend, every use of `ueye` here is
    behind a lazy, guarded import - this backend must be safe to construct
    and probe (count_available()) even on a machine that never installed the
    IDS driver, without taking the rest of this tool down with it.

    Modeled on IDS's own official pyueye_example (the Camera / ImageBuffer /
    ImageData pattern from pyueye_example_camera.py and
    pyueye_example_utils.py, (c) IDS Imaging Development Systems GmbH,
    BSD-style license) rather than invented from scratch, since the uEye
    SDK's sequence-buffer/queue lifecycle is easy to get subtly wrong (e.g.
    buffer size not matching the post-binning AOI).

    Verified end-to-end against a real UI-3160CP-M-GL Rev.2.1 once the IDS
    Software Suite driver was installed: model name and native size read
    back correctly (1920x1200), Mono8/Mono10/Mono12 all captured real frames
    with the expected value range (Mono10 <=1023, Mono12 <=4095, confirming
    the 16-bit-container assumption below), and 2x2 binning produced exactly
    the expected 960x600 frames. Before that driver was installed on this
    machine, `from pyueye import ueye` failed at import time (device showed
    "Error" status in Windows Device Manager) - pip installing pyueye alone
    does not make the camera usable, the separate driver install is required.
    """

    def __init__(self) -> None:
        super().__init__()
        self._ueye = None
        self.h_cam = None
        self._img_buffers: list[tuple] = []
        self._native_w = 0
        self._native_h = 0
        self._color_mode = None
        self._bits_per_pixel = 8
        self._frame_w = 0
        self._frame_h = 0

    @staticmethod
    def count_available() -> int:
        try:
            from pyueye import ueye
        except Exception:
            return 0  # covers both "pyueye not installed" and "IDS driver DLL not found"
        try:
            n = ueye.c_int(0)
            if ueye.is_GetNumberOfCameras(n) != ueye.IS_SUCCESS:
                return 0
            return int(n.value)
        except Exception:
            return 0

    def open(self) -> None:
        from pyueye import ueye
        # is_WaitForNextImage/is_InitImageQueue (used in retrieve_frame/
        # start_grabbing below) are marked deprecated in this pyueye release,
        # with no replacement shipped for the queue-capture pattern IDS's own
        # official example uses - confirmed still functionally correct
        # against real hardware, so this silences the otherwise very noisy
        # per-frame warning rather than switching to an unverified API.
        # Filtered by message, not module: pyueye's deprecated() wrapper
        # passes stacklevel=2, which attributes the warning to whichever
        # module CALLED the deprecated function (this one, or __main__) -
        # not to pyueye itself - so a module-based filter silently never
        # matches.
        warnings.filterwarnings(
            "ignore",
            message=r"Call to deprecated function is_(WaitForNextImage|InitImageQueue|ExitImageQueue)\.",
            category=DeprecationWarning,
        )
        self._ueye = ueye
        self.h_cam = ueye.HIDS(0)  # 0 = first available camera, mirrors pypylon's devices[0]
        ret = ueye.is_InitCamera(self.h_cam, None)
        if ret != ueye.IS_SUCCESS:
            self.h_cam = None
            raise RuntimeError(
                f"is_InitCamera failed (error code {ret}) - is the IDS Software Suite driver "
                "installed, and the camera not already held open by another program "
                "(e.g. IDS Camera Manager / IDS peak Cockpit)?"
            )

        sensor_info = ueye.SENSORINFO()
        ueye.is_GetSensorInfo(self.h_cam, sensor_info)
        sensor_name = sensor_info.strSensorName.decode(errors="replace").rstrip("\x00")
        self.model_name = f"IDS uEye ({sensor_name})" if sensor_name else "IDS uEye camera"
        self._native_w = int(sensor_info.nMaxWidth)
        self._native_h = int(sensor_info.nMaxHeight)

        rect_aoi = ueye.IS_RECT()
        rect_aoi.s32X = ueye.int(0)
        rect_aoi.s32Y = ueye.int(0)
        rect_aoi.s32Width = ueye.int(self._native_w)
        rect_aoi.s32Height = ueye.int(self._native_h)
        ueye.is_AOI(self.h_cam, ueye.IS_AOI_IMAGE_SET_AOI, rect_aoi, ueye.sizeof(rect_aoi))

    def native_size(self) -> tuple[int, int]:
        return self._native_w, self._native_h

    def apply_pixel_format(self, requested: str, log: Callable[[str], None]) -> str:
        ueye = self._ueye
        # IS_CM_MONO10/12 pack samples into a 16-bit container, the same way
        # the SDK's RAW10/RAW12 modes do - inferred from that pattern (see
        # class docstring), not independently confirmed on real hardware.
        color_modes = {
            "Mono8": (ueye.IS_CM_MONO8, 8),
            "Mono10": (ueye.IS_CM_MONO10, 16),
            "Mono12": (ueye.IS_CM_MONO12, 16),
        }
        mode, bits = color_modes.get(requested, color_modes["Mono8"])
        applied = requested if requested in color_modes else "Mono8"
        ret = ueye.is_SetColorMode(self.h_cam, mode)
        if ret != ueye.IS_SUCCESS:
            if applied != "Mono8":
                log(f"{self.model_name} does not support pixel format {requested} (error {ret}) - using Mono8 instead.")
            mode, bits = color_modes["Mono8"]
            ueye.is_SetColorMode(self.h_cam, mode)
            applied = "Mono8"
        self._color_mode = mode
        self._bits_per_pixel = bits
        return applied

    def apply_binning(self, n: int, log: Callable[[str], None]) -> None:
        ueye = self._ueye
        if n == 1:
            ueye.is_SetBinning(self.h_cam, ueye.IS_BINNING_DISABLE)
            return
        mode_by_factor = {
            2: ueye.IS_BINNING_2X_HORIZONTAL | ueye.IS_BINNING_2X_VERTICAL,
            4: ueye.IS_BINNING_4X_HORIZONTAL | ueye.IS_BINNING_4X_VERTICAL,
        }
        mode = mode_by_factor.get(n)
        if mode is None:
            log(f"{self.model_name}: {n}x binning not offered by this tool - continuing at 1x1.")
            return
        supported = ueye.is_SetBinning(self.h_cam, ueye.IS_GET_SUPPORTED_BINNING)
        if not (supported & mode):
            log(f"{self.model_name} does not support {n}x binning - continuing at 1x1.")
            return
        ret = ueye.is_SetBinning(self.h_cam, mode)
        if ret != ueye.IS_SUCCESS:
            log(f"{self.model_name}: setting {n}x binning failed (error {ret}) - continuing at 1x1.")

    def apply_exposure_us(self, requested_us: float, log: Callable[[str], None]) -> None:
        # uEye exposure is metric (milliseconds), unlike pypylon's
        # microseconds - converted here so CameraGrabThread stays
        # vendor-neutral (always microseconds).
        ueye = self._ueye
        lo_ms, hi_ms = ueye.c_double(), ueye.c_double()
        ueye.is_Exposure(self.h_cam, ueye.IS_EXPOSURE_CMD_GET_EXPOSURE_RANGE_MIN, lo_ms, ueye.sizeof(lo_ms))
        ueye.is_Exposure(self.h_cam, ueye.IS_EXPOSURE_CMD_GET_EXPOSURE_RANGE_MAX, hi_ms, ueye.sizeof(hi_ms))
        requested_ms = requested_us / 1000.0
        lo, hi = lo_ms.value, hi_ms.value
        if hi > 0 and not (lo <= requested_ms <= hi):
            clamped_ms = min(max(requested_ms, lo), hi)
            log(
                f"Requested exposure {requested_us:.0f}us is outside {self.model_name}'s range "
                f"({lo * 1000:.0f}-{hi * 1000:.0f}us) - clamped to {clamped_ms * 1000:.0f}us."
            )
            requested_ms = clamped_ms
        exposure_param = ueye.c_double(requested_ms)
        ueye.is_Exposure(self.h_cam, ueye.IS_EXPOSURE_CMD_SET_EXPOSURE, exposure_param, ueye.sizeof(exposure_param))

    def maximize_throughput(self, log: Callable[[str], None]) -> None:
        # Freshly initialized uEye cameras default to a conservative pixel
        # clock and an even more conservative frame-rate cap - measured on
        # this camera as 200MHz (of a 120-400MHz range) and ~25fps
        # regardless of exposure/binning, neither of which is a real sensor/
        # USB bandwidth limit. Confirmed on the bench: raising both took
        # this camera from 25fps to ~117fps at Mono8/native/1x1. Must run
        # after apply_binning (frame size affects the achievable ceiling)
        # and before apply_exposure_us (a long exposure can legitimately cap
        # frame rate below this ceiling - that's fine, this just removes the
        # *artificial* cap).
        ueye = self._ueye
        try:
            pc_range = (ueye.UINT * 3)()
            ret = ueye.is_PixelClock(self.h_cam, ueye.IS_PIXELCLOCK_CMD_GET_RANGE, pc_range, ueye.sizeof(pc_range))
            if ret == ueye.IS_SUCCESS and pc_range[1].value > 0:
                max_pc = pc_range[1].value
                ueye.is_PixelClock(self.h_cam, ueye.IS_PIXELCLOCK_CMD_SET, ueye.UINT(max_pc), ueye.sizeof(ueye.UINT(max_pc)))
        except Exception as exc:
            log(f"{self.model_name}: could not raise pixel clock ({exc}) - achievable fps may be lower than possible.")

        try:
            min_s, max_s, inc_s = ueye.c_double(), ueye.c_double(), ueye.c_double()
            ret = ueye.is_GetFrameTimeRange(self.h_cam, min_s, max_s, inc_s)
            if ret == ueye.IS_SUCCESS and min_s.value > 0:
                applied_fps = ueye.c_double()
                ueye.is_SetFrameRate(self.h_cam, ueye.c_double(1.0 / min_s.value), applied_fps)
                log(f"{self.model_name}: pixel clock/frame rate maximized for the current format/binning "
                    f"(~{applied_fps.value:.1f} fps ceiling before exposure time is applied).")
        except Exception as exc:
            log(f"{self.model_name}: could not raise frame rate ({exc}) - it may default to ~25fps.")

    def start_grabbing(self) -> None:
        ueye = self._ueye
        for mem_ptr, mem_id in self._img_buffers:
            ueye.is_FreeImageMem(self.h_cam, mem_ptr, mem_id)
        self._img_buffers = []

        rect_aoi = ueye.IS_RECT()
        ueye.is_AOI(self.h_cam, ueye.IS_AOI_IMAGE_GET_AOI, rect_aoi, ueye.sizeof(rect_aoi))
        self._frame_w = rect_aoi.s32Width.value
        self._frame_h = rect_aoi.s32Height.value

        # 3 sequence buffers is IDS's own example default - enough to absorb
        # normal scheduling jitter between capture and is_WaitForNextImage().
        for _ in range(3):
            mem_ptr = ueye.c_mem_p()
            mem_id = ueye.int()
            ueye.is_AllocImageMem(self.h_cam, self._frame_w, self._frame_h, self._bits_per_pixel, mem_ptr, mem_id)
            ueye.is_AddToSequence(self.h_cam, mem_ptr, mem_id)
            self._img_buffers.append((mem_ptr, mem_id))
        ueye.is_InitImageQueue(self.h_cam, 0)
        ueye.is_CaptureVideo(self.h_cam, ueye.IS_DONT_WAIT)

    def retrieve_frame(self, timeout_ms: int) -> np.ndarray | None:
        ueye = self._ueye
        mem_ptr = ueye.c_mem_p()
        mem_id = ueye.int()
        ret = ueye.is_WaitForNextImage(self.h_cam, timeout_ms, mem_ptr, mem_id)
        if ret != ueye.IS_SUCCESS:
            return None
        x, y, bits, pitch = ueye.int(), ueye.int(), ueye.int(), ueye.int()
        ueye.is_InquireImageMem(self.h_cam, mem_ptr, mem_id, x, y, bits, pitch)
        raw = ueye.get_data(mem_ptr, self._frame_w, self._frame_h, bits, pitch, True)
        if self._bits_per_pixel == 8:
            frame = raw.reshape(self._frame_h, self._frame_w).copy()
        else:
            frame = raw.view(np.uint16).reshape(self._frame_h, self._frame_w).copy()
        ueye.is_UnlockSeqBuf(self.h_cam, mem_id, mem_ptr)
        return frame

    def stop_grabbing(self) -> None:
        if self._ueye is None or self.h_cam is None:
            return
        self._ueye.is_StopLiveVideo(self.h_cam, self._ueye.IS_FORCE_VIDEO_STOP)
        for mem_ptr, mem_id in self._img_buffers:
            self._ueye.is_FreeImageMem(self.h_cam, mem_ptr, mem_id)
        self._img_buffers = []

    def close(self) -> None:
        if self._ueye is not None and self.h_cam is not None:
            self._ueye.is_ExitCamera(self.h_cam)
            self.h_cam = None


BACKENDS: dict[str, type[CameraBackend]] = {
    "pylon": PylonBackend,
    "ueye": UeyeBackend,
}


# ── Camera capture thread ───────────────────────────────────────────────────
#
# Runs the backend's blocking retrieve_frame() loop off the GUI thread - the
# same separation the real app's acquisition pipeline will need (§8 of the
# architecture plan). frameReady carries the raw ndarray and the capture
# timestamp; ROI extraction happens on the GUI thread for this spike (single-
# threaded on purpose first - see whether that alone sustains the target
# before building a separate processing thread, per the architecture plan's
# "don't build the more complex version speculatively" note).

class CameraGrabThread(QThread):
    frameReady = pyqtSignal(object, float)
    logMessage = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        backend_name: str,
        pixel_format: str = "Mono8",
        binning: int = 1,
        exposure_us: float | None = None,
    ) -> None:
        super().__init__()
        self._backend_name = backend_name
        self._pixel_format = pixel_format
        self._binning = binning
        self._exposure_us = exposure_us
        self._running = False
        self.backend: CameraBackend | None = None
        self.camera_model = "unknown"

    def run(self) -> None:
        try:
            backend_cls = BACKENDS.get(self._backend_name)
            if backend_cls is None:
                self.error.emit(f"Unknown camera backend '{self._backend_name}'.")
                return
            self.backend = backend_cls()
            self.backend.open()
            self.camera_model = self.backend.model_name

            applied_format = self.backend.apply_pixel_format(self._pixel_format, self.logMessage.emit)
            self.backend.apply_binning(self._binning, self.logMessage.emit)
            self.backend.maximize_throughput(self.logMessage.emit)
            width, height = self.backend.native_size()
            if self._exposure_us is not None:
                self.backend.apply_exposure_us(self._exposure_us, self.logMessage.emit)

            self.logMessage.emit(
                f"Connected: {self.camera_model}, native {width}x{height}, "
                f"pixel format {applied_format}, binning {self._binning}x{self._binning}."
            )

            self.backend.start_grabbing()
            self._running = True
            while self._running:
                frame = self.backend.retrieve_frame(timeout_ms=2000)
                if frame is not None:
                    self.frameReady.emit(frame, time.perf_counter())
        except Exception as exc:  # noqa: BLE001 - surface any backend error to the UI
            self.error.emit(str(exc))
        finally:
            try:
                if self.backend is not None:
                    self.backend.stop_grabbing()
                    self.backend.close()
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
        controls.addWidget(QLabel("Camera:"))
        self.camera_combo = QComboBox()
        self.camera_combo.addItems(["Auto-detect", "Basler (pypylon)", "IDS (pyueye)"])
        controls.addWidget(self.camera_combo)

        controls.addWidget(QLabel("Pixel format:"))
        self.pixel_format_combo = QComboBox()
        self.pixel_format_combo.addItems(["Mono8", "Mono10", "Mono12"])
        controls.addWidget(self.pixel_format_combo)

        controls.addWidget(QLabel("Binning:"))
        self.binning_combo = QComboBox()
        # Labels describe the binning factor only, not a fixed resolution - the
        # actual resulting size depends on whatever camera is connected and is
        # shown live in the stats line below (frame: WxH) once capturing.
        self.binning_combo.addItems(["1x1 (native resolution)", "2x2 (1/4 resolution)", "4x4 (1/16 resolution)"])
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

    def _resolve_backend_name(self) -> str | None:
        choice = self.camera_combo.currentText()
        if choice.startswith("Basler"):
            return "pylon"
        if choice.startswith("IDS"):
            return "ueye"
        # Auto-detect: probe both without opening either, prefer Basler if
        # both are present (matches this tool's original single-camera
        # assumption), and tell the user so they can force the other one.
        pylon_n = PylonBackend.count_available()
        ueye_n = UeyeBackend.count_available()
        if pylon_n and ueye_n:
            self._log(
                f"Auto-detect found both a Basler camera ({pylon_n}) and an IDS camera ({ueye_n}) - "
                "using Basler. Pick a specific camera above to choose the other one."
            )
            return "pylon"
        if pylon_n:
            return "pylon"
        if ueye_n:
            return "ueye"
        self._log("Auto-detect found no camera from either backend (Basler/pypylon or IDS/pyueye).")
        return None

    def _start_capture(self) -> None:
        backend_name = self._resolve_backend_name()
        if backend_name is None:
            return
        self._first_frame_shown = False
        self._roi_masks = RoiMasks(boxes=[])  # force rebuild - image size may have changed (binning/camera)
        pf = self.pixel_format_combo.currentText()
        binning = int(self.binning_combo.currentText().split("x")[0])
        exposure_us = float(self.exposure_spin.value())
        self._grab_thread = CameraGrabThread(
            backend_name=backend_name, pixel_format=pf, binning=binning, exposure_us=exposure_us
        )
        self._grab_thread.frameReady.connect(self._on_frame)
        self._grab_thread.error.connect(self._on_error)
        self._grab_thread.logMessage.connect(self._log)
        self._grab_thread.start()
        self.start_button.setText("Stop capture")
        if self.disk_write_check.isChecked():
            self._save_writer = SaveWriterThread()
            self._save_writer.start()
            self._log(f"Disk-write load enabled - writing into {_SCRATCH_DIR} "
                      f"(rotating {_SCRATCH_FILE_COUNT} files, bounded disk use).")
        else:
            self._save_writer = None
        self._log(f"Starting: camera={backend_name}, pixel_format={pf}, binning={binning}x{binning}, "
                   f"exposure={exposure_us:.0f}us "
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
        camera_model = self._grab_thread.camera_model if self._grab_thread is not None else "unknown"
        summary = (
            f"### Phase 0 results — {time.strftime('%Y-%m-%d %H:%M')}\n"
            f"- Camera: {camera_model}, pixel format {pf}, binning {binning}, "
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
