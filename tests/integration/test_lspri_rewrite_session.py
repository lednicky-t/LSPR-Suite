"""Session save/load round trip for the LSPRimaging Evaluation rewrite.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

The format is new rather than a port of the old `processing_profile.json`
(the maintainer's 2026-09-23 decision - `PreprocessingSettings` no longer
exists, so there was nothing to keep the old shape for), which means there
is no legacy file to test against and the round trip *is* the contract.

What is pinned here is mostly the things that fail quietly:

- The two ROI fields that need real encoders rather than `asdict()`
  (`RoiMask`, `per_wavelength`'s tuple keys) - JSON has no tuples, so a
  naive encoder loses the per-wavelength nudges with no error at all.
- Id counters resuming past what was restored. If they reset, the first ROI
  or group created after opening a session silently *replaces* an existing
  one.
- The undo stack being cleared. Otherwise Ctrl+Z immediately after opening
  a dataset rewinds into a half-restored state that never existed.
- Re-saving unchanged state producing a byte-identical file, which is what
  lets an autosave's "did anything change" check be a plain comparison.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.app_rewrite import apply_session, capture_session
    from lspr_imaging_app.image_tools import (
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
    )
    from lspr_imaging_app.image_tools.chromatic.model import (
        ChromaticLandmarkObservation,
        ChromaticTransformModel,
    )
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import RoiMask
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.storage.session import (
        SESSION_SCHEMA_NAME,
        load_session,
        save_session,
        session_path,
    )
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402


def _modules():
    return (
        GeometryModule(), MaskModule(), ChromaticModule(),
        BackgroundModule(), RoiToolbox(), SelectionModule(),
    )


class RewriteSessionRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.naming = FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0, 600.0])
        (self.geometry, self.mask, self.chromatic,
         self.background, self.roi_toolbox, self.selection) = _modules()
        self.patch = np.zeros((5, 6), dtype=bool)
        self.patch[1:4, 1:5] = True
        self._populate()

    def tearDown(self) -> None:
        undo_manager.clear()
        self._tmp.cleanup()

    def _populate(self) -> None:
        """A deliberately non-default state touching every persisted block."""
        self.geometry.set_image_tools_enabled(True)
        self.geometry.set_rotation(11.5)
        self.geometry.set_crop(8, 6, 48, 40)
        self.geometry.set_flip(horizontal=True, vertical=False)
        self.background.set_flatten_background_settings(
            enabled=True, sigma_px=42.0, binning=3, exclude_area_rois=True,
            exclude_mask=False, exclusion_dilation_px=2,
            local_reference_normalization_enabled=True,
        )

        persistent = np.zeros((64, 80), dtype=bool)
        persistent[0:6, 0:6] = True
        individual = np.zeros((64, 80), dtype=bool)
        individual[10:14, 10:14] = True
        self.mask.set_mask_change((0, 500.0), "persistent", persistent)
        self.mask.set_mask_change((1, 600.0), "individual", individual)

        self.chromatic.restore_state(
            self.chromatic.settings(),
            (ChromaticTransformModel(
                spectral_cube_index=0, wavelength_nm=550.0,
                affine_matrix=[[1.0, 0.0, 2.5], [0.0, 1.0, -1.5]], rmse_px=0.31),),
            (ChromaticLandmarkObservation(
                landmark_id=7, spectral_cube_index=0, wavelength_nm=550.0, x_px=12.0, y_px=34.0),),
        )

        self.roi_a = self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        self.roi_b = self.roi_toolbox.add_roi(60.0, 45.0, sample_radius_px=7.0)
        self.group_id = self.roi_toolbox.create_group("Row A", sample_color_hex="#112233")
        self.roi_toolbox.add_to_group(self.roi_a, self.group_id)
        # Give one ROI mask geometry and a per-wavelength nudge - the two
        # fields a naive JSON encoder loses silently.
        self.roi_toolbox.restore_state(
            replace(self.roi_toolbox.detection_settings(),
                    reduction_method="median", reference_outer_radius_px=21.0),
            tuple(
                replace(roi, sample_geometry_type="mask",
                        sample_mask=RoiMask(x0=3, y0=4, mask=self.patch),
                        per_wavelength={(0, 550.0): (41.5, 31.5)})
                if roi.area_roi_id == self.roi_a else roi
                for roi in self.roi_toolbox.rois()
            ),
            self.roi_toolbox.groups(),
            self.roi_toolbox.array_groups(),
        )
        self.selection.set_cube(1)
        self.selection.set_wavelength(600.0)
        self.selection.set_roi_selection({self.roi_b})

    def _save(self) -> Path:
        return save_session(
            self.root,
            capture_session(self.geometry, self.mask, self.chromatic,
                            self.background, self.roi_toolbox, self.selection),
            self.naming,
        )

    def _reload(self):
        """Save, then apply into a completely fresh set of modules - what
        reopening a dataset in a new process actually does."""
        self._save()
        restored = _modules()
        state = load_session(self.root)
        self.assertIsNotNone(state)
        apply_session(state, *restored)
        return restored

    # -- on-disk shape --------------------------------------------------

    def test_file_layout_and_schema(self) -> None:
        path = self._save()
        self.assertEqual(path, session_path(self.root))
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_name"], SESSION_SCHEMA_NAME)
        self.assertLessEqual(
            {"geometry", "background", "mask", "chromatic", "roi", "selection"},
            set(payload),
        )

    def test_mask_pixels_go_to_pngs_not_into_the_json(self) -> None:
        path = self._save()
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(list((self.root / "session" / "masks").glob("*.png")))
        for entry in payload["mask"]["changes"]:
            self.assertEqual(set(entry), {"cube_index", "wavelength_nm", "scope", "version"})
        # MaskSettings' two ndarray fields are never persisted - see
        # image_tools/mask/module.py's finding 1.
        self.assertNotIn("histogram_mask", payload["mask"]["settings"])
        self.assertLess(path.stat().st_size, 20_000)

    def test_resaving_unchanged_state_is_byte_identical(self) -> None:
        first = json.loads(self._save().read_text(encoding="utf-8"))
        pngs = sorted(p.name for p in (self.root / "session" / "masks").glob("*.png"))
        second = json.loads(self._save().read_text(encoding="utf-8"))
        self.assertEqual(first, second)
        self.assertEqual(pngs, sorted(p.name for p in (self.root / "session" / "masks").glob("*.png")))

    # -- round trip -----------------------------------------------------

    def test_settings_round_trip(self) -> None:
        geometry, _mask, _chrom, background, _roi, _sel = self._reload()
        restored, original = geometry.settings(), self.geometry.settings()
        self.assertEqual(restored.rotation_angle_deg, original.rotation_angle_deg)
        self.assertEqual(restored.flip_horizontal, original.flip_horizontal)
        self.assertEqual(restored.crop.x, original.crop.x)
        self.assertEqual(restored.crop.width, original.crop.width)
        self.assertEqual(
            background.settings().flatten_background_sigma_px,
            self.background.settings().flatten_background_sigma_px,
        )

    def test_mask_timeline_round_trip(self) -> None:
        _geo, mask, _chrom, _bg, _roi, _sel = self._reload()
        self.assertEqual(len(mask.mask_changes()), 2)

        original = self.mask.resolve_mask_source((0, 500.0))
        restored = mask.resolve_mask_source((0, 500.0))
        self.assertIsNotNone(restored)
        self.assertTrue(np.array_equal(original[1], restored[1]))
        self.assertEqual(restored[2], "persistent")

        # An individual change must still win over the persistent one that
        # would otherwise carry forward into its cube.
        individual = mask.resolve_mask_source((1, 600.0))
        self.assertEqual(individual[2], "individual")

    def test_chromatic_round_trip(self) -> None:
        _geo, _mask, chromatic, _bg, _roi, _sel = self._reload()
        self.assertEqual(len(chromatic.models()), 1)
        self.assertTrue(np.allclose(
            chromatic.affine_for((0, 550.0)), np.array([[1.0, 0.0, 2.5], [0.0, 1.0, -1.5]])
        ))
        self.assertEqual([m.landmark_id for m in chromatic.landmarks()], [7])

    def test_roi_round_trip_including_mask_geometry_and_nudges(self) -> None:
        _geo, _mask, _chrom, _bg, roi_toolbox, _sel = self._reload()
        self.assertEqual(len(roi_toolbox.rois()), 2)
        self.assertEqual(roi_toolbox.detection_settings().reduction_method, "median")
        self.assertEqual(roi_toolbox.detection_settings().reference_outer_radius_px, 21.0)

        restored = roi_toolbox.roi_by_id(self.roi_a)
        self.assertEqual(restored.center_x, 40.0)
        self.assertIsNotNone(restored.sample_mask)
        self.assertEqual((restored.sample_mask.x0, restored.sample_mask.y0), (3, 4))
        self.assertTrue(np.array_equal(restored.sample_mask.mask, self.patch))
        # JSON has no tuple keys - this is the field a naive encoder loses.
        self.assertEqual(restored.per_wavelength, {(0, 550.0): (41.5, 31.5)})

        self.assertEqual(len(roi_toolbox.groups()), 1)
        self.assertEqual(roi_toolbox.groups()[0].sample_color_hex, "#112233")
        self.assertIsNotNone(roi_toolbox.group_for_roi(self.roi_a))

    def test_selection_round_trip(self) -> None:
        _geo, _mask, _chrom, _bg, _roi, selection = self._reload()
        self.assertEqual(selection.current_cube(), 1)
        self.assertEqual(selection.current_wavelength(), 600.0)
        self.assertEqual(selection.selected_roi_ids(), frozenset({self.roi_b}))

    # -- the traps ------------------------------------------------------

    def test_restore_clears_the_undo_stack(self) -> None:
        self._reload()
        self.assertFalse(undo_manager.can_undo)

    def test_id_counters_resume_past_what_was_restored(self) -> None:
        """If they reset, the next ROI or group created replaces an
        existing one instead of being added."""
        _geo, _mask, _chrom, _bg, roi_toolbox, _sel = self._reload()
        new_roi = roi_toolbox.add_roi(1.0, 2.0)
        self.assertEqual(new_roi, 3)
        self.assertEqual(len(roi_toolbox.rois()), 3)

        new_group = roi_toolbox.create_group("Row B")
        self.assertNotEqual(new_group, self.group_id)
        self.assertEqual(len(roi_toolbox.groups()), 2)

    # -- absent and unreadable files ------------------------------------

    def test_no_session_returns_none(self) -> None:
        self.assertIsNone(load_session(self.root / "never-used"))

    def test_foreign_schema_is_rejected(self) -> None:
        other = self.root / "other"
        (other / "session").mkdir(parents=True)
        (other / "session" / "session.json").write_text(
            json.dumps({"schema_name": "something_else", "schema_version": "1.0"}), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            load_session(other)

    def test_future_major_version_is_rejected(self) -> None:
        """Silently starting from defaults would look identical to "this
        dataset was never set up" - which is the wrong thing to show
        someone whose afternoon of ROI placement is in that file."""
        future = self.root / "future"
        (future / "session").mkdir(parents=True)
        (future / "session" / "session.json").write_text(
            json.dumps({"schema_name": SESSION_SCHEMA_NAME, "schema_version": "2.0"}), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            load_session(future)

    def test_a_missing_mask_png_drops_that_change_and_keeps_the_rest(self) -> None:
        payload = json.loads(self._save().read_text(encoding="utf-8"))
        orphaned = self.root / "orphaned"
        (orphaned / "session").mkdir(parents=True)
        (orphaned / "session" / "session.json").write_text(json.dumps(payload), encoding="utf-8")

        state = load_session(orphaned)  # its masks/ folder does not exist
        self.assertIsNotNone(state)
        self.assertEqual(state.mask_changes, ())
        self.assertEqual(len(state.rois), 2)


if __name__ == "__main__":
    unittest.main()
