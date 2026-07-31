"""Coverage for MeasurementSession.set_absorbance_direct - Simulation mode's
direct-to-Absorbance wiring (see spectral_processing_pipeline_architecture.md
and domain/session.py). Bypasses compute_absorbance/_recompute_absorbance
entirely, so it must not depend on (or be clobbered by) dark/reference/sample.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

import sys

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import Spectrum
from lspr_app.domain.session import MeasurementSession


def _make_spectrum(*, y_label: str = "Intensity (a.u.)", kind: str | None = "sample") -> Spectrum:
    metadata = {} if kind is None else {"kind": kind}
    return Spectrum(
        wavelengths_nm=np.asarray([400.0, 500.0, 600.0], dtype=np.float64),
        values=np.asarray([0.1, 0.3, 0.15], dtype=np.float64),
        y_label=y_label,
        acquired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metadata=metadata,
    )


class SetAbsorbanceDirectTests(unittest.TestCase):
    def test_requires_no_dark_or_reference(self) -> None:
        session = MeasurementSession()
        self.assertIsNone(session.state.dark)
        self.assertIsNone(session.state.reference)

        session.set_absorbance_direct(_make_spectrum())

        self.assertIsNotNone(session.state.absorbance)
        self.assertIsNone(session.state.dark)
        self.assertIsNone(session.state.reference)

    def test_tags_kind_and_normalizes_y_label(self) -> None:
        session = MeasurementSession()
        session.set_absorbance_direct(_make_spectrum(y_label="Intensity (a.u.)", kind=None))

        absorbance = session.state.absorbance
        assert absorbance is not None
        self.assertEqual(absorbance.metadata["kind"], "absorbance")
        self.assertEqual(absorbance.y_label, "Absorbance (a.u.)")

    def test_values_pass_through_unchanged(self) -> None:
        session = MeasurementSession()
        spectrum = _make_spectrum()
        session.set_absorbance_direct(spectrum)

        absorbance = session.state.absorbance
        assert absorbance is not None
        np.testing.assert_allclose(absorbance.values, spectrum.values)
        np.testing.assert_allclose(absorbance.wavelengths_nm, spectrum.wavelengths_nm)

    def test_does_not_touch_dark_reference_sample(self) -> None:
        session = MeasurementSession()
        session.set_dark(_make_spectrum(kind="dark"))
        session.set_reference(_make_spectrum(kind="reference"))

        session.set_absorbance_direct(_make_spectrum())

        self.assertIsNotNone(session.state.dark)
        self.assertIsNotNone(session.state.reference)
        self.assertIsNone(session.state.sample)

    def test_set_dark_after_the_fact_does_clobber_a_direct_absorbance(self) -> None:
        # set_dark/set_reference/set_sample all call _recompute_absorbance
        # unconditionally, which nulls state.absorbance unless dark+
        # reference+sample are ALL present - set_absorbance_direct doesn't
        # protect against that. This is exactly why Simulation mode's live
        # loop (flush_live_processed_results) and its dark/reference capture
        # buttons (hidden in Simulation mode - see
        # MainWindow._update_absorbance_only_mode) must never call set_dark/
        # set_reference/set_sample: doing so would silently null out a
        # directly-wired absorbance value.
        session = MeasurementSession()
        session.set_absorbance_direct(_make_spectrum())
        self.assertIsNotNone(session.state.absorbance)

        session.set_dark(_make_spectrum(kind="dark"))

        self.assertIsNone(session.state.absorbance)


if __name__ == "__main__":
    unittest.main()
