from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import numpy as np

import seabreeze
seabreeze.use("cseabreeze")
from seabreeze.spectrometers import Spectrometer as SeaBreezeSpectrometer
from seabreeze.spectrometers import list_devices

from lspr_app.device.base import Spectrometer, SpectrometerCapabilities, SpectrometerError
from lspr_app.domain.models import AcquisitionSettings, Spectrum


class OceanSpectrometer(Spectrometer):
    """Hardware backend for Ocean Insight / Ocean Optics devices via SeaBreeze."""

    _AUTO_TARGET_FRACTION = 0.85
    _AUTO_TOLERANCE_FRACTION = 0.08
    _AUTO_MAX_ITERATIONS = 6

    def __init__(self) -> None:
        devices = list_devices()
        if not devices:
            raise SpectrometerError(
                "No SeaBreeze-compatible spectrometer detected. "
                "Close OceanView and verify the USB driver configuration."
            )

        self._spectrometer = SeaBreezeSpectrometer(devices[0])
        self._wavelengths = np.asarray(self._spectrometer.wavelengths(), dtype=np.float64)
        self._integration_limits_us = self._spectrometer.integration_time_micros_limits
        self._max_intensity = float(self._spectrometer.max_intensity)
        self._electric_dark_pixel_indices = tuple(
            self._spectrometer.features["spectrometer"][0].get_electric_dark_pixel_indices()
        )

    def device_name(self) -> str:
        model = getattr(self._spectrometer, "model", "Ocean spectrometer")
        serial = getattr(self._spectrometer, "serial_number", "unknown-serial")
        return f"{model} ({serial})"

    def capabilities(self) -> SpectrometerCapabilities:
        return SpectrometerCapabilities(
            hardware_boxcar=False,
            simulated_boxcar=False,
            supports_dark_correction=True,
            supports_nonlinearity_correction=True,
            supports_trigger=True,
        )

    def auto_integration_time_ms(self, settings: AcquisitionSettings) -> float:
        tuned_us = self._resolve_integration_time_us(settings, auto_optimize=True)
        return tuned_us / 1000.0

    def acquire_spectrum(self, settings: AcquisitionSettings) -> Spectrum:
        settings = replace(settings)
        integration_time_us = self._resolve_integration_time_us(settings, auto_optimize=False)

        try:
            self._spectrometer.trigger_mode(settings.trigger_mode)
            self._spectrometer.integration_time_micros(integration_time_us)
            captures = [
                np.asarray(
                    self._spectrometer.intensities(
                        correct_dark_counts=settings.correct_dark_counts,
                        correct_nonlinearity=settings.correct_nonlinearity,
                    ),
                    dtype=np.float64,
                )
                for _ in range(max(settings.averages, 1))
            ]
        except Exception as exc:  # pragma: no cover - hardware runtime path
            raise SpectrometerError(f"Spectrometer acquisition failed: {exc}") from exc

        averaged = np.mean(captures, axis=0)
        return Spectrum(
            wavelengths_nm=self._wavelengths.copy(),
            values=averaged,
            y_label="Intensity (counts)",
            acquired_at=datetime.now(timezone.utc),
            metadata={
                "device": self.device_name(),
                "integration_time_ms": integration_time_us / 1000.0,
                "averages": settings.averages,
                "mode": "hardware",
                "correct_dark_counts": settings.correct_dark_counts,
                "correct_nonlinearity": settings.correct_nonlinearity,
                "trigger_mode": settings.trigger_mode,
                "electric_dark_pixel_correction": settings.correct_dark_counts,
                "electric_dark_pixel_count": len(self._electric_dark_pixel_indices),
            },
        )

    def close(self) -> None:
        self._spectrometer.close()

    def _resolve_integration_time_us(self, settings: AcquisitionSettings, auto_optimize: bool) -> int:
        requested_us = int(max(settings.integration_time_ms * 1000.0, 1_000.0))
        requested_us = int(np.clip(requested_us, *self._integration_limits_us))
        if not auto_optimize:
            return requested_us

        target = self._max_intensity * self._AUTO_TARGET_FRACTION
        tolerance = self._max_intensity * self._AUTO_TOLERANCE_FRACTION
        integration_us = requested_us

        try:
            self._spectrometer.trigger_mode(settings.trigger_mode)
        except Exception:
            pass

        for _ in range(self._AUTO_MAX_ITERATIONS):
            self._spectrometer.integration_time_micros(integration_us)
            intensities = np.asarray(
                self._spectrometer.intensities(
                    correct_dark_counts=settings.correct_dark_counts,
                    correct_nonlinearity=settings.correct_nonlinearity,
                ),
                dtype=np.float64,
            )
            peak = float(np.max(intensities))
            if peak <= 0.0:
                integration_us = min(integration_us * 4, self._integration_limits_us[1])
                continue
            if abs(peak - target) <= tolerance:
                return integration_us

            scaled = int(integration_us * target / peak)
            next_integration_us = int(np.clip(scaled, *self._integration_limits_us))
            if next_integration_us == integration_us:
                return integration_us
            integration_us = next_integration_us

        return integration_us
