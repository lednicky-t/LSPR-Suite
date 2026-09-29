"""Pure-logic tests for `GeometryModule`'s calibration/scale-bar commands
(`apply_measurement_calibration`, `set_measurement_anchors`,
`set_display_units`, `set_scale_bar_visible`, `can_display_micrometers`,
`microns_per_pixel_scalar`).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`test_lspri_rewrite_analysis_core.py`'s docstring.

These commands were ported from the stable app's
`gui/measurement_calibration_mixin.py` on 2026-09-21 and, per the rewrite
build log, only ever verified with ad-hoc scripted calls - there was no
pytest coverage at all before this file. What matters most here is exactly
what the module's own docstring calls out as easy to get wrong: the
symmetric/asymmetric-axis fallback in `apply_measurement_calibration`, its
three `ValueError` guards, and the "cosmetic vs. undo-tracked are
independent axes" split (`set_measurement_anchors`/`set_display_units`/
`set_scale_bar_visible` are cosmetic but NOT undo-tracked, while
`apply_measurement_calibration` is both). A third "mm" unit was briefly
added and reverted the same day (2026-09-29) - the Measure tool's unit
toggle was cut from scope before shipping, so `set_display_units` only
ever accepts "px"/"um" - `test_rejects_mm_no_longer_a_supported_unit`
pins that reversal explicitly rather than just silently dropping the case.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.geometry.module import GeometryModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


class MeasurementAnchorsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.geometry = GeometryModule()
        self.cosmetic_reasons: list[str] = []
        self.geometry.cosmetic_changed.connect(lambda change: self.cosmetic_reasons.append(change.reason))

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_sets_all_four_anchor_fields(self) -> None:
        self.geometry.set_measurement_anchors(1.0, 2.0, 3.0, 4.0)
        settings = self.geometry.settings()
        self.assertEqual(
            (
                settings.measurement_anchor1_x_px,
                settings.measurement_anchor1_y_px,
                settings.measurement_anchor2_x_px,
                settings.measurement_anchor2_y_px,
            ),
            (1.0, 2.0, 3.0, 4.0),
        )
        self.assertEqual(self.cosmetic_reasons, ["measurement_anchors"])

    def test_no_op_when_unchanged(self) -> None:
        self.geometry.set_measurement_anchors(1.0, 2.0, 3.0, 4.0)
        self.geometry.set_measurement_anchors(1.0, 2.0, 3.0, 4.0)
        self.assertEqual(self.cosmetic_reasons, ["measurement_anchors"])

    def test_not_undo_tracked(self) -> None:
        """A prior real edit is the only thing one undo() reverts - the
        anchor move itself never reaches the undo stack."""
        self.geometry.set_rotation(5.0)
        self.geometry.set_measurement_anchors(1.0, 2.0, 3.0, 4.0)
        undo_manager.undo()
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)
        self.assertEqual(self.geometry.settings().measurement_anchor2_x_px, 3.0)


class ApplyCalibrationTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.geometry = GeometryModule()
        self.geometry.set_measurement_anchors(0.0, 0.0, 100.0, 40.0)  # dx_px=100, dy_px=40

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_both_axes_given_are_independent(self) -> None:
        self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=100.0)
        settings = self.geometry.settings()
        self.assertAlmostEqual(settings.microns_per_pixel_x, 0.5)  # 50 um / 100 px
        self.assertAlmostEqual(settings.microns_per_pixel_y, 2.5)  # 100 um / 40 px
        self.assertTrue(settings.calibration_enabled)
        self.assertEqual(settings.display_units, "um")

    def test_only_dx_given_y_follows_x(self) -> None:
        self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=0.0)
        settings = self.geometry.settings()
        self.assertAlmostEqual(settings.microns_per_pixel_x, 0.5)
        self.assertAlmostEqual(settings.microns_per_pixel_y, 0.5)  # symmetric fallback

    def test_only_dy_given_x_follows_y(self) -> None:
        self.geometry.apply_measurement_calibration(dx_um=0.0, dy_um=100.0)
        settings = self.geometry.settings()
        self.assertAlmostEqual(settings.microns_per_pixel_y, 2.5)
        self.assertAlmostEqual(settings.microns_per_pixel_x, 2.5)  # symmetric fallback

    def test_neither_axis_given_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Enter a real dx"):
            self.geometry.apply_measurement_calibration(dx_um=0.0, dy_um=0.0)
        self.assertFalse(self.geometry.settings().calibration_enabled)

    def test_dx_requested_with_zero_pixel_delta_raises(self) -> None:
        self.geometry.set_measurement_anchors(0.0, 0.0, 0.0, 40.0)  # dx_px=0
        with self.assertRaisesRegex(ValueError, "dx between the ruler guides is zero"):
            self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=0.0)

    def test_dy_requested_with_zero_pixel_delta_raises(self) -> None:
        self.geometry.set_measurement_anchors(0.0, 0.0, 100.0, 0.0)  # dy_px=0
        with self.assertRaisesRegex(ValueError, "dy between the ruler guides is zero"):
            self.geometry.apply_measurement_calibration(dx_um=0.0, dy_um=100.0)

    def test_result_is_exactly_one_undo_step(self) -> None:
        self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=100.0)
        undo_manager.undo()
        settings = self.geometry.settings()
        self.assertFalse(settings.calibration_enabled)
        self.assertEqual(settings.display_units, "px")
        self.assertEqual(settings.microns_per_pixel_x, 1.0)
        self.assertEqual(settings.microns_per_pixel_y, 1.0)
        undo_manager.redo()
        self.assertTrue(self.geometry.settings().calibration_enabled)

    def test_emits_cosmetic_change_with_calibration_reason(self) -> None:
        reasons: list[str] = []
        self.geometry.cosmetic_changed.connect(lambda change: reasons.append(change.reason))
        self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=0.0)
        self.assertEqual(reasons, ["calibration"])


class DisplayUnitsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.geometry = GeometryModule()

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_rejects_an_unknown_unit(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be 'px' or 'um'"):
            self.geometry.set_display_units("inches")

    def test_rejects_mm_no_longer_a_supported_unit(self) -> None:
        """"mm" was added then reverted the same day (2026-09-29) - the
        Measure tool's unit toggle was cut from scope before shipping."""
        with self.assertRaisesRegex(ValueError, "must be 'px' or 'um'"):
            self.geometry.set_display_units("mm")

    def test_um_is_refused_before_calibration(self) -> None:
        with self.assertRaisesRegex(ValueError, "Calibrate the ruler first"):
            self.geometry.set_display_units("um")
        self.assertEqual(self.geometry.settings().display_units, "px")

    def test_um_is_allowed_after_calibration(self) -> None:
        self.geometry.set_measurement_anchors(0.0, 0.0, 100.0, 0.0)
        self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=0.0)
        self.geometry.set_display_units("um")
        self.assertEqual(self.geometry.settings().display_units, "um")

    def test_px_is_always_allowed(self) -> None:
        self.geometry.set_display_units("px")  # already px - a no-op, not an error
        self.assertEqual(self.geometry.settings().display_units, "px")

    def test_no_op_does_not_re_emit(self) -> None:
        reasons: list[str] = []
        self.geometry.cosmetic_changed.connect(lambda change: reasons.append(change.reason))
        self.geometry.set_display_units("px")
        self.assertEqual(reasons, [])

    def test_not_undo_tracked(self) -> None:
        self.geometry.set_measurement_anchors(0.0, 0.0, 100.0, 0.0)
        self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=0.0)  # one undo step, leaves display_units="um"
        self.geometry.set_display_units("px")  # not a second undo step
        undo_manager.undo()
        self.assertFalse(self.geometry.settings().calibration_enabled)


class ScaleBarAndQueriesTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.geometry = GeometryModule()

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_scale_bar_toggle_is_cosmetic_and_not_undo_tracked(self) -> None:
        reasons: list[str] = []
        self.geometry.cosmetic_changed.connect(lambda change: reasons.append(change.reason))
        self.geometry.set_rotation(5.0)
        self.geometry.set_scale_bar_visible(True)
        self.assertEqual(reasons, ["scale_bar_visible"])
        undo_manager.undo()
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)
        self.assertTrue(self.geometry.settings().scale_bar_visible)

    def test_can_display_micrometers_requires_both_axes_positive(self) -> None:
        self.assertFalse(self.geometry.can_display_micrometers())
        self.geometry.set_measurement_anchors(0.0, 0.0, 100.0, 0.0)
        self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=0.0)
        self.assertTrue(self.geometry.can_display_micrometers())

    def test_microns_per_pixel_scalar_is_the_axis_average(self) -> None:
        self.geometry.set_measurement_anchors(0.0, 0.0, 100.0, 40.0)
        self.geometry.apply_measurement_calibration(dx_um=50.0, dy_um=100.0)
        # x: 0.5 um/px, y: 2.5 um/px -> average 1.5
        self.assertAlmostEqual(self.geometry.microns_per_pixel_scalar(), 1.5)


if __name__ == "__main__":
    unittest.main()
