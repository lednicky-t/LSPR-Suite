from __future__ import annotations

import unittest

import numpy as np

from lspr_app.device.simulated import SimulatedSpectrometer, SimulationParameters
from lspr_app.domain.models import AcquisitionSettings, ProcessingSettings
from lspr_app.domain.processing import process_spectrum


class SimulatedSpectrometerTests(unittest.TestCase):
    def test_acquisition_output_is_independent_of_acquisition_settings(self) -> None:
        spectrometer = SimulatedSpectrometer(
            SimulationParameters(
                peak_height=1300.0,
                baseline=900.0,
                slope=0.12,
                noise=0.0,
            )
        )
        first = spectrometer.acquire_kind_spectrum(
            "sample",
            AcquisitionSettings(
                integration_time_ms=1.0,
                averages=1,
                correct_dark_counts=True,
                correct_nonlinearity=True,
                trigger_mode=0,
            ),
        )
        second = spectrometer.acquire_kind_spectrum(
            "sample",
            AcquisitionSettings(
                integration_time_ms=50.0,
                averages=100,
                correct_dark_counts=False,
                correct_nonlinearity=False,
                trigger_mode=0,
            ),
        )

        self.assertEqual(first.wavelengths_nm.shape, second.wavelengths_nm.shape)
        np.testing.assert_allclose(first.wavelengths_nm, second.wavelengths_nm)
        np.testing.assert_allclose(first.values, second.values)
        self.assertGreater(float(first.values.max()), 1000.0)

    def test_peak_height_changes_the_simulated_signal(self) -> None:
        low = SimulatedSpectrometer(
            SimulationParameters(
                peak_height=400.0,
                baseline=900.0,
                slope=0.12,
                noise=0.0,
            )
        ).acquire_kind_spectrum(
            "sample",
            AcquisitionSettings(
                integration_time_ms=4.0,
                averages=10,
                correct_dark_counts=True,
                correct_nonlinearity=True,
                trigger_mode=0,
            ),
        )
        high = SimulatedSpectrometer(
            SimulationParameters(
                peak_height=1300.0,
                baseline=900.0,
                slope=0.12,
                noise=0.0,
            )
        ).acquire_kind_spectrum(
            "sample",
            AcquisitionSettings(
                integration_time_ms=4.0,
                averages=10,
                correct_dark_counts=True,
                correct_nonlinearity=True,
                trigger_mode=0,
            ),
        )

        self.assertGreater(float(high.values.max()), float(low.values.max()))

    def test_second_peak_parameters_control_the_secondary_feature(self) -> None:
        narrow = SimulatedSpectrometer(
            SimulationParameters(
                peak_center_nm=600.0,
                peak_width_nm=20.0,
                peak_height=1000.0,
                secondary_peak_offset_nm=80.0,
                secondary_peak_height_percent=200.0,
                secondary_peak_width_percent=50.0,
                baseline=0.0,
                slope=0.0,
                noise=0.0,
            )
        ).acquire_kind_spectrum("sample", AcquisitionSettings())
        wide = SimulatedSpectrometer(
            SimulationParameters(
                peak_center_nm=600.0,
                peak_width_nm=20.0,
                peak_height=1000.0,
                secondary_peak_offset_nm=80.0,
                secondary_peak_height_percent=200.0,
                secondary_peak_width_percent=200.0,
                baseline=0.0,
                slope=0.0,
                noise=0.0,
            )
        ).acquire_kind_spectrum("sample", AcquisitionSettings())

        secondary_center_nm = 680.0
        center_index = int(np.argmin(np.abs(wide.wavelengths_nm - secondary_center_nm)))
        offset_index = int(np.argmin(np.abs(wide.wavelengths_nm - (secondary_center_nm + 40.0))))
        self.assertAlmostEqual(float(wide.wavelengths_nm[center_index]), secondary_center_nm, places=6)
        self.assertAlmostEqual(float(wide.values[center_index]), 2000.0, delta=1.0)
        self.assertGreater(float(wide.values[offset_index]), float(narrow.values[offset_index]))

    def test_linear_slope_is_visible_in_reference_spectrum(self) -> None:
        spectrometer = SimulatedSpectrometer(
            SimulationParameters(
                peak_height=0.0,
                baseline=900.0,
                slope=0.20,
                noise=0.0,
            )
        )
        spectrum = spectrometer.acquire_kind_spectrum(
            "reference",
            AcquisitionSettings(
                integration_time_ms=4.0,
                averages=1,
                correct_dark_counts=False,
                correct_nonlinearity=False,
                trigger_mode=0,
            ),
        )
        self.assertGreater(float(spectrum.values[-1] - spectrum.values[0]), 90.0)

    def test_slope_scales_with_signal_level(self) -> None:
        low_peak = SimulatedSpectrometer(
            SimulationParameters(
                peak_height=0.0,
                baseline=900.0,
                slope=0.20,
                noise=0.0,
            )
        ).acquire_kind_spectrum(
            "reference",
            AcquisitionSettings(),
        )
        high_peak = SimulatedSpectrometer(
            SimulationParameters(
                peak_height=1800.0,
                baseline=900.0,
                slope=0.20,
                noise=0.0,
            )
        ).acquire_kind_spectrum(
            "reference",
            AcquisitionSettings(),
        )
        self.assertGreater(
            float(high_peak.values[-1] - high_peak.values[0]),
            float(low_peak.values[-1] - low_peak.values[0]),
        )

    def test_linear_baseline_processing_removes_endpoint_trend(self) -> None:
        """_linear_baseline anchors each end on the *mean of a window* of
        points (2% of the spectrum, clamped to 5-50 - see its docstring in
        domain/processing.py), not a single raw sample, to avoid baking one
        noisy sample's value into the slope/intercept subtracted from the
        whole spectrum. For this noise-free linear-slope signal, that means
        the corrected endpoints land close to zero but not exactly zero -
        averaging a window on a sloped line gives the line's value at the
        window's midpoint, not its true edge. Compute that expected offset
        analytically from the same slope, rather than assuming zero.
        """
        spectrometer = SimulatedSpectrometer(
            SimulationParameters(
                peak_height=0.0,
                baseline=900.0,
                slope=0.20,
                noise=0.0,
            )
        )
        spectrum = spectrometer.acquire_kind_spectrum(
            "reference",
            AcquisitionSettings(
                integration_time_ms=4.0,
                averages=1,
                correct_dark_counts=False,
                correct_nonlinearity=False,
                trigger_mode=0,
            ),
        )
        # Baseline removal only ever applies to the Absorbance spectrum (see
        # spectral_processing_pipeline_architecture.md) - tag this synthetic
        # "reference"-shaped curve as absorbance so this test can isolate
        # _linear_baseline's math from that kind gate.
        processed, _ = process_spectrum(
            spectrum.with_metadata(kind="absorbance"),
            ProcessingSettings(
                baseline_method="linear",
                smoothing_method="none",
                smoothing_window=1,
                wavelength_min_nm=float(spectrum.wavelengths_nm[0]),
                wavelength_max_nm=float(spectrum.wavelengths_nm[-1]),
            ),
        )
        self.assertIsNotNone(processed)
        assert processed is not None

        n = len(spectrum.values)
        window = min(max(round(n * 0.02), 5), 50)
        window = min(window, max(n // 2, 1))
        slope_per_index = (float(spectrum.values[-1]) - float(spectrum.values[0])) / (n - 1)
        # Mean of indices [0, window-1] sits at the line's value at the
        # window's midpoint index (window-1)/2, vs. the true edge at index 0.
        expected_left_offset = -slope_per_index * (window - 1) / 2.0
        expected_right_offset = slope_per_index * (window - 1) / 2.0
        self.assertAlmostEqual(float(processed.values[0]), expected_left_offset, places=6)
        self.assertAlmostEqual(float(processed.values[-1]), expected_right_offset, places=6)


if __name__ == "__main__":
    unittest.main()
