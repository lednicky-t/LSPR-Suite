from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal


@dataclass(slots=True)
class HardwareInitStepResult:
    key: str
    label: str
    state: str
    message: str
    connected: bool = False
    probe: object | None = None
    payload: object | None = None
    error: str | None = None


@dataclass(slots=True)
class HardwareInitResult:
    steps: list[HardwareInitStepResult]
    pump_probe: object | None
    pump_error: str | None
    mswitch_devices: list[object]
    mswitch_error: str | None
    valve_probe: object | None = None
    valve_error: str | None = None
    spectrometer_name: str | None = None
    spectrometer_error: str | None = None


@dataclass(slots=True)
class HardwareInitStep:
    key: str
    label: str
    runner: Callable[[], HardwareInitStepResult]


class HardwareInitSignals(QObject):
    progress = pyqtSignal(int, str)
    step = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class HardwareInitTask(QRunnable):
    def __init__(self, steps: list[HardwareInitStep]) -> None:
        super().__init__()
        self._steps = list(steps)
        self.signals = HardwareInitSignals()

    def run(self) -> None:
        logger = logging.getLogger("lspr_app.hardware_init")
        steps: list[HardwareInitStepResult] = []
        pump_probe = None
        pump_error = None
        mswitch_devices: list[object] = []
        mswitch_error = None
        valve_probe = None
        valve_error = None
        spectrometer_name = None
        spectrometer_error = None

        logger.info("Hardware init worker started with %d step(s).", len(self._steps))
        total_steps = max(len(self._steps), 1)
        for index, step in enumerate(self._steps, start=1):
            progress_base = int(round(((index - 1) / total_steps) * 90.0))
            self.signals.progress.emit(progress_base, f"Initializing {step.label}...")
            try:
                result = step.runner()
            except Exception as exc:
                logger.warning("Hardware init step failed | key=%s error=%s", step.key, exc)
                result = HardwareInitStepResult(
                    key=step.key,
                    label=step.label,
                    state="error",
                    message=f"{step.label} init failed: {exc}",
                    connected=False,
                    error=str(exc),
                )
            steps.append(result)
            self.signals.step.emit(result)
            self.signals.progress.emit(progress_base + max(int(90 / total_steps), 1), result.message)
            if step.key == "pump":
                pump_probe = result.probe
                pump_error = result.error
            elif step.key == "mswitch":
                mswitch_devices = list(result.payload or [])
                mswitch_error = result.error
            elif step.key == "valve":
                valve_probe = result.probe
                valve_error = result.error
            elif step.key == "spectrometer":
                spectrometer_name = str(result.payload or "") or None
                spectrometer_error = result.error

        self.signals.progress.emit(98, "Finalizing hardware initialization...")
        self.signals.finished.emit(
            HardwareInitResult(
                steps=steps,
                pump_probe=pump_probe,
                pump_error=pump_error,
                mswitch_devices=mswitch_devices,
                mswitch_error=mswitch_error,
                valve_probe=valve_probe,
                valve_error=valve_error,
                spectrometer_name=spectrometer_name,
                spectrometer_error=spectrometer_error,
            )
        )
