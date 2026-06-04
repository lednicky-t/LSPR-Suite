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
        processed, _ = process_spectrum(
            spectrum,
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
        self.assertAlmostEqual(float(processed.values[0]), 0.0, places=6)
        self.assertAlmostEqual(float(processed.values[-1]), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
