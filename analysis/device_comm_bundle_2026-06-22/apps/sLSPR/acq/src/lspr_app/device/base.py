from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from lspr_app.domain.models import AcquisitionSettings, Spectrum


class SpectrometerError(RuntimeError):
    """Raised when acquisition fails."""


@dataclass(slots=True)
class SpectrometerCapabilities:
    hardware_boxcar: bool = False
    simulated_boxcar: bool = False
    supports_dark_correction: bool = False
    supports_nonlinearity_correction: bool = False
    supports_trigger: bool = False


class Spectrometer(ABC):
    @abstractmethod
    def device_name(self) -> str:
        """Return a user-facing device identifier."""

    def capabilities(self) -> SpectrometerCapabilities:
        return SpectrometerCapabilities()

    def auto_integration_time_ms(self, settings: AcquisitionSettings) -> float:
        raise SpectrometerError("Automatic integration time is not available for this backend.")

    @abstractmethod
    def acquire_spectrum(self, settings: AcquisitionSettings) -> Spectrum:
        """Acquire one spectrum using the provided settings."""
