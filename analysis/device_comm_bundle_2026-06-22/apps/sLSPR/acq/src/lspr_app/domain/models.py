from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from numpy.typing import NDArray


ArrayF64 = NDArray[np.float64]


@dataclass(slots=True)
class AcquisitionSettings:
    integration_time_ms: float = 50.0
    averages: int = 1
    correct_dark_counts: bool = False
    correct_nonlinearity: bool = False
    trigger_mode: int = 0


@dataclass(slots=True)
class ProcessingSettings:
    wavelength_min_nm: float = 400.0
    wavelength_max_nm: float = 900.0
    baseline_method: str = "none"
    smoothing_method: str = "none"
    smoothing_window: int = 5
    temporal_smoothing: int = 1
    crop_method: str = "fixed_width"
    crop_fraction: float = 0.7
    fit_method: str = "none"
    polynomial_order: int = 2
    fit_window_width_nm: float = 120.0
    analysis_resolution_nm: float = 0.001
    spectrum_tracking_mode: str = "poly_max"
    trace_noise_window_s: float = 10.0
    trace_metrics: list[str] = field(
        default_factory=lambda: ["smoothed_max", "centroid"]
    )

    @property
    def peak_tracking_mode(self) -> str:
        return self.spectrum_tracking_mode

    @peak_tracking_mode.setter
    def peak_tracking_mode(self, value: str) -> None:
        self.spectrum_tracking_mode = value


@dataclass(slots=True)
class Spectrum:
    wavelengths_nm: ArrayF64
    values: ArrayF64
    y_label: str
    acquired_at: datetime
    metadata: dict[str, object] = field(default_factory=dict)

    def with_metadata(self, **extra: object) -> "Spectrum":
        merged = dict(self.metadata)
        merged.update(extra)
        return Spectrum(
            wavelengths_nm=self.wavelengths_nm.copy(),
            values=self.values.copy(),
            y_label=self.y_label,
            acquired_at=self.acquired_at,
            metadata=merged,
        )


@dataclass(slots=True)
class SessionState:
    dark: Spectrum | None = None
    reference: Spectrum | None = None
    sample: Spectrum | None = None
    absorbance: Spectrum | None = None
