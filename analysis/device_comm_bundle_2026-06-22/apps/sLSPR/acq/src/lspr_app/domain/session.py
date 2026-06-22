from __future__ import annotations

from dataclasses import replace

import numpy as np

from lspr_app.domain.models import SessionState, Spectrum


class MeasurementError(RuntimeError):
    """Raised when spectra cannot be evaluated safely."""


class MeasurementSession:
    def __init__(self) -> None:
        self.state = SessionState()

    def set_dark(self, spectrum: Spectrum) -> None:
        self.state = replace(self.state, dark=spectrum.with_metadata(kind="dark"))
        self._recompute_absorbance()

    def set_reference(self, spectrum: Spectrum) -> None:
        reference = self._coerce_to_dark_axis(spectrum)
        self.state = replace(self.state, reference=reference.with_metadata(kind="reference"))
        self._recompute_absorbance()

    def set_sample(self, spectrum: Spectrum) -> None:
        sample = self._coerce_to_dark_axis(spectrum).with_metadata(kind="sample")
        self.state = replace(self.state, sample=sample)
        self._recompute_absorbance()

    def get_plot_data(self, plot_mode: str) -> Spectrum | None:
        if plot_mode == "dark":
            return self.state.dark
        if plot_mode == "reference":
            return self.state.reference
        if plot_mode == "sample":
            return self.state.sample
        if plot_mode == "absorbance":
            return self.state.absorbance
        raise ValueError(f"Unsupported plot mode: {plot_mode}")

    def compute_absorbance(self, sample: Spectrum) -> Spectrum | None:
        if self.state.dark is None or self.state.reference is None:
            return None

        dark = self.state.dark
        reference = self.state.reference
        resampled_to_dark_axis = bool(
            reference.wavelengths_nm.shape != dark.wavelengths_nm.shape
            or not np.allclose(reference.wavelengths_nm, dark.wavelengths_nm)
            or sample.wavelengths_nm.shape != dark.wavelengths_nm.shape
            or not np.allclose(sample.wavelengths_nm, dark.wavelengths_nm)
        )
        reference = self._coerce_to_axis(reference, dark.wavelengths_nm)
        sample = self._coerce_to_axis(sample, dark.wavelengths_nm)

        corrected_reference = reference.values - dark.values
        corrected_sample = sample.values - dark.values

        valid = (corrected_reference > 0.0) & (corrected_sample > 0.0)
        absorbance = np.full_like(corrected_reference, np.nan, dtype=np.float64)
        absorbance[valid] = -np.log10(corrected_sample[valid] / corrected_reference[valid])

        return Spectrum(
            wavelengths_nm=dark.wavelengths_nm.copy(),
            values=absorbance,
            y_label="Absorbance (a.u.)",
            acquired_at=sample.acquired_at,
            metadata={
                "kind": "absorbance",
                "derived_from": [
                    dark.acquired_at.isoformat(),
                    reference.acquired_at.isoformat(),
                    sample.acquired_at.isoformat(),
                ],
                "invalid_points": int((~valid).sum()),
                "resampled_to_dark_axis": resampled_to_dark_axis,
            },
        )

    def _recompute_absorbance(self) -> None:
        if not (self.state.dark and self.state.reference and self.state.sample):
            self.state = replace(self.state, absorbance=None)
            return

        dark = self.state.dark
        reference = self.state.reference
        sample = self.state.sample

        self.state = replace(
            self.state,
            absorbance=self.compute_absorbance(sample),
        )

    @staticmethod
    def _coerce_to_dark_axis(spectrum: Spectrum) -> Spectrum:
        return spectrum

    @staticmethod
    def _coerce_to_axis(spectrum: Spectrum, target_wavelengths: np.ndarray) -> Spectrum:
        source_wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
        target_wavelengths = np.asarray(target_wavelengths, dtype=np.float64)
        if source_wavelengths.shape == target_wavelengths.shape and np.allclose(source_wavelengths, target_wavelengths):
            return spectrum
        if len(source_wavelengths) == 0 or len(target_wavelengths) == 0:
            return Spectrum(
                wavelengths_nm=target_wavelengths.copy(),
                values=np.full_like(target_wavelengths, np.nan, dtype=np.float64),
                y_label=spectrum.y_label,
                acquired_at=spectrum.acquired_at,
                metadata={**spectrum.metadata, "resampled_to_axis": True},
            )
        values = np.asarray(spectrum.values, dtype=np.float64)
        finite = np.isfinite(source_wavelengths) & np.isfinite(values)
        if np.count_nonzero(finite) < 2:
            return Spectrum(
                wavelengths_nm=target_wavelengths.copy(),
                values=np.full_like(target_wavelengths, np.nan, dtype=np.float64),
                y_label=spectrum.y_label,
                acquired_at=spectrum.acquired_at,
                metadata={**spectrum.metadata, "resampled_to_axis": True},
            )
        source_wavelengths = source_wavelengths[finite]
        values = values[finite]
        order = np.argsort(source_wavelengths)
        source_wavelengths = source_wavelengths[order]
        values = values[order]
        resampled = np.interp(target_wavelengths, source_wavelengths, values, left=np.nan, right=np.nan)
        return Spectrum(
            wavelengths_nm=target_wavelengths.copy(),
            values=resampled,
            y_label=spectrum.y_label,
            acquired_at=spectrum.acquired_at,
            metadata={**spectrum.metadata, "resampled_to_axis": True},
        )
