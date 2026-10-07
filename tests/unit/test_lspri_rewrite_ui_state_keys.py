"""The UI-state key table (`storage/ui_state_keys.py`) and the migration of the
settings fields it replaced (2026-10-07).

`AppSettings` had 37 hand-wired fields; 26 of them were per-panel values and
now live in `AppSettings.ui_state`. The risk of that move is losing what a
user already saved, so the migration is pinned here end to end: a settings file
written by the previous build loads with every value in its new place, a value
already in the new place wins, bad values are dropped instead of reaching a
panel, and the migrated file saves without the old fields.

Qt-free on purpose (pure logic plus JSON files).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.storage import ui_state_keys as keys
from lspr_imaging_app.storage.app_settings import (
    APP_SETTINGS_SCHEMA_NAME,
    AppSettings,
    load_app_settings,
    save_app_settings,
)

# A settings file exactly as the previous build wrote it: every retired field set to a non-default value.
_LEGACY_FILE = {
    "schema_name": APP_SETTINGS_SCHEMA_NAME,
    "schema_version": "1.0",
    "theme": "bright",
    "last_dataset_folder": "C:/data/run7",
    "histogram_y_mode": "counts",
    "histogram_log_y": True,
    "histogram_bin_width_px": 128.0,
    "histogram_line_width_px": 2.5,
    "image_view_x_min": 1.0,
    "image_view_x_max": 79.0,
    "image_view_y_min": 2.0,
    "image_view_y_max": 63.0,
    "chromatic_landmark_count": 25,
    "chromatic_stride": 5,
    "chromatic_border_percent": 7.5,
    "chromatic_max_step_px": 9.0,
    "chromatic_feature_diameter_px": 14.0,
    "chromatic_show_landmarks": False,
    "chromatic_landmarks_all_wavelengths": True,
    "image_ribbon_category": "ROIs",
    "mask_overlay_visible": False,
    "mask_overlay_color": "#112233",
    "mask_overlay_alpha": 0.3,
    "highlight_overlay_visible": False,
    "highlight_overlay_color": "#445566",
    "highlight_overlay_alpha": 0.7,
    "show_background": True,
    "highlight_range_min": 100.0,
    "highlight_range_max": 900.0,
    "highlight_range_dataset": "C:/data/run7",
}


class KeyTableTest(unittest.TestCase):
    def test_keys_and_legacy_names_are_unique(self) -> None:
        names = [k.key for k in keys.ALL_KEYS]
        self.assertEqual(len(names), len(set(names)))
        legacy = [k.legacy for k in keys.ALL_KEYS]
        self.assertEqual(len(legacy), len(set(legacy)))

    def test_defaults_are_the_ones_the_retired_fields_had(self) -> None:
        """Opening the app with no saved state must look exactly as before."""
        expected = {
            "histogram/y_mode": "percent", "histogram/log_y": False, "histogram/bin_width_px": 512.0,
            "histogram/line_width_px": 1.5, "chromatic/landmark_count": 15, "chromatic/stride": 3,
            "chromatic/border_percent": 5.0, "chromatic/max_step_px": 5.0, "chromatic/feature_diameter_px": None,
            "chromatic/show_landmarks": True, "chromatic/all_wavelengths": False, "image/ribbon_category": None,
            "image/show_background": False, "image/mask_overlay_visible": True, "image/mask_overlay_color": None,
            "image/mask_overlay_alpha": 0.5, "image/highlight_overlay_visible": True,
            "image/highlight_overlay_color": None, "image/highlight_overlay_alpha": 0.42,
        }
        self.assertEqual({k.key: k.default for k in keys.ALL_KEYS}, expected)

    def test_read_returns_a_valid_saved_value(self) -> None:
        self.assertEqual(keys.read({"histogram/log_y": True}, keys.HISTOGRAM_LOG_Y), True)
        self.assertEqual(keys.read({"chromatic/stride": 7}, keys.CHROMATIC_STRIDE), 7)
        self.assertEqual(keys.read({"image/mask_overlay_color": "#aabbcc"}, keys.MASK_OVERLAY_COLOR), "#aabbcc")

    def test_read_falls_back_to_the_default_for_missing_or_wrongly_typed_values(self) -> None:
        for store, key in (
            ({}, keys.HISTOGRAM_BIN_WIDTH),
            ({"histogram/bin_width_px": "wide"}, keys.HISTOGRAM_BIN_WIDTH),
            ({"histogram/log_y": 1}, keys.HISTOGRAM_LOG_Y),  # an int is not a bool
            ({"chromatic/stride": True}, keys.CHROMATIC_STRIDE),  # a bool is not an int
            ({"chromatic/stride": 2.5}, keys.CHROMATIC_STRIDE),
            ({"histogram/y_mode": None}, keys.HISTOGRAM_Y_MODE),  # null is only legal where the default is null
            ({"image/mask_overlay_alpha": [0.5]}, keys.MASK_OVERLAY_ALPHA),
        ):
            self.assertEqual(keys.read(store, key), key.default, (store, key.key))

    def test_an_integer_is_accepted_for_a_float_key_and_returned_as_a_float(self) -> None:
        value = keys.read({"histogram/bin_width_px": 256}, keys.HISTOGRAM_BIN_WIDTH)
        self.assertEqual(value, 256.0)
        self.assertIsInstance(value, float)

    def test_a_nullable_key_accepts_a_value_and_null(self) -> None:
        self.assertEqual(keys.read({"chromatic/feature_diameter_px": 12}, keys.CHROMATIC_FEATURE_DIAMETER), 12.0)
        self.assertIsNone(keys.read({"chromatic/feature_diameter_px": None}, keys.CHROMATIC_FEATURE_DIAMETER))

    def test_view_range_needs_all_four_numbers(self) -> None:
        self.assertEqual(
            keys.read_view_range({keys.IMAGE_VIEW_RANGE: [[1, 79], [2.5, 63]]}), ((1.0, 79.0), (2.5, 63.0))
        )
        for bad in (None, [[1, 79]], [[1, 79], [2]], [[1, 79], ["a", 3]], [[1, 79], [True, 3]], "x", [[1, 79], [2, 3], [4, 5]]):
            self.assertIsNone(keys.read_view_range({keys.IMAGE_VIEW_RANGE: bad}), bad)

    def test_highlight_range_needs_a_dataset_and_two_numbers(self) -> None:
        good = {"dataset": "C:/d", "min": 1, "max": 9.5}
        self.assertEqual(keys.read_highlight_range({keys.HIGHLIGHT_RANGE: good}), ("C:/d", 1.0, 9.5))
        for bad in (None, {"dataset": "C:/d", "min": 1}, {"min": 1, "max": 2}, {"dataset": 3, "min": 1, "max": 2}, [1, 2]):
            self.assertIsNone(keys.read_highlight_range({keys.HIGHLIGHT_RANGE: bad}), bad)


class HandWiredKeysTest(unittest.TestCase):
    def test_every_key_name_in_the_table_is_unique(self) -> None:
        names = [k.key for k in keys.ALL_KEYS] + [k.key for k in keys.HAND_WIRED_KEYS]
        names += [keys.IMAGE_VIEW_RANGE, keys.HIGHLIGHT_RANGE, keys.REFERENCE_FRAME,
                  keys.ROI_TABLE_SORT, keys.ROI_TABLE_COLLAPSED, keys.ROI_TABLE_COLUMN_WIDTHS]
        self.assertEqual(len(names), len(set(names)), sorted(n for n in names if names.count(n) > 1))
        for name in names:
            self.assertRegex(name, r"^[a-z_]+/[a-z_]+$|^reference_frame$")

    def test_keys_without_a_retired_field_have_none_to_migrate(self) -> None:
        self.assertTrue(all(k.legacy is None for k in keys.HAND_WIRED_KEYS))

    def test_no_panel_or_the_app_shell_spells_a_ui_state_key_as_a_string_literal(self) -> None:
        """A key lives in `ui_state_keys.py`, so it cannot be spelled two ways
        in two files. The one exception is a widget's own `bind(...)` key, which
        stays next to the widget by design (one line per control)."""
        import re

        root = APP_SRC / "lspr_imaging_app"
        pattern = re.compile(r"""\.(?:get|set)\(\s*f?["'][a-z_]+/[^"']+["']""")
        offenders = []
        for path in [*(root / "panels").rglob("*.py"), root / "app_rewrite.py"]:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()}")
        self.assertEqual(offenders, [])


class MigrationTest(unittest.TestCase):
    def test_every_retired_field_lands_in_its_new_key(self) -> None:
        ui: dict[str, object] = {}
        self.assertTrue(keys.migrate_legacy_fields(_LEGACY_FILE, ui))
        for ui_key in keys.ALL_KEYS:
            self.assertEqual(ui[ui_key.key], _LEGACY_FILE[ui_key.legacy], ui_key.key)
        self.assertEqual(ui[keys.IMAGE_VIEW_RANGE], [[1.0, 79.0], [2.0, 63.0]])
        self.assertEqual(ui[keys.HIGHLIGHT_RANGE], {"dataset": "C:/data/run7", "min": 100.0, "max": 900.0})

    def test_a_value_already_in_the_new_place_wins(self) -> None:
        ui: dict[str, object] = {"histogram/y_mode": "normalized", keys.IMAGE_VIEW_RANGE: [[0, 1], [0, 1]]}
        keys.migrate_legacy_fields(_LEGACY_FILE, ui)
        self.assertEqual(ui["histogram/y_mode"], "normalized")
        self.assertEqual(ui[keys.IMAGE_VIEW_RANGE], [[0, 1], [0, 1]])
        self.assertEqual(ui["histogram/log_y"], True)  # the rest still migrates

    def test_null_and_wrongly_typed_legacy_values_are_dropped(self) -> None:
        payload = {
            "image_ribbon_category": None, "chromatic_stride": "three", "histogram_log_y": 1,
            "mask_overlay_alpha": [0.5], "histogram_y_mode": "counts",
        }
        ui: dict[str, object] = {}
        keys.migrate_legacy_fields(payload, ui)
        self.assertEqual(ui, {"histogram/y_mode": "counts"})

    def test_a_partial_composite_is_not_migrated(self) -> None:
        ui: dict[str, object] = {}
        changed = keys.migrate_legacy_fields(
            {"image_view_x_min": 1.0, "image_view_x_max": 79.0, "highlight_range_min": 1.0, "highlight_range_max": 2.0},
            ui,
        )
        self.assertFalse(changed)
        self.assertEqual(ui, {})

    def test_nothing_to_migrate_changes_nothing(self) -> None:
        ui: dict[str, object] = {"export/chunk_size": 512}
        self.assertFalse(keys.migrate_legacy_fields({"theme": "dark"}, ui))
        self.assertEqual(ui, {"export/chunk_size": 512})


class LoadAndSaveTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / "settings.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, payload: dict) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_a_file_from_the_previous_build_loads_with_every_value_in_ui_state(self) -> None:
        self._write(_LEGACY_FILE)
        settings = load_app_settings(self.path)
        self.assertEqual(settings.theme, "bright")  # app-level fields are untouched
        self.assertEqual(settings.last_dataset_folder, "C:/data/run7")
        self.assertEqual(settings.ui_state["histogram/y_mode"], "counts")
        self.assertEqual(settings.ui_state["chromatic/landmark_count"], 25)
        self.assertEqual(settings.ui_state[keys.IMAGE_VIEW_RANGE], [[1.0, 79.0], [2.0, 63.0]])
        self.assertEqual(settings.ui_state[keys.HIGHLIGHT_RANGE]["dataset"], "C:/data/run7")

    def test_saving_a_migrated_file_drops_the_retired_fields_and_keeps_the_values(self) -> None:
        self._write(_LEGACY_FILE)
        save_app_settings(load_app_settings(self.path), self.path)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        for ui_key in keys.ALL_KEYS:
            self.assertNotIn(ui_key.legacy, saved)
        for name in ("image_view_x_min", "highlight_range_min", "highlight_range_dataset"):
            self.assertNotIn(name, saved)
        self.assertEqual(saved["schema_version"], "1.1")
        self.assertEqual(saved["ui_state"]["histogram/log_y"], True)

    def test_migration_is_stable_across_repeated_load_and_save(self) -> None:
        self._write(_LEGACY_FILE)
        first = load_app_settings(self.path)
        save_app_settings(first, self.path)
        second = load_app_settings(self.path)
        self.assertEqual(second, first)
        save_app_settings(second, self.path)
        self.assertEqual(load_app_settings(self.path), first)

    def test_a_new_style_file_is_loaded_as_it_is(self) -> None:
        settings = AppSettings(theme="bright", ui_state={"histogram/log_y": True, keys.IMAGE_VIEW_RANGE: [[0, 5], [0, 6]]})
        save_app_settings(settings, self.path)
        self.assertEqual(load_app_settings(self.path), settings)

    def test_the_app_level_fields_that_remain(self) -> None:
        from dataclasses import fields

        self.assertEqual(
            {f.name for f in fields(AppSettings)},
            {
                "last_dataset_folder", "auto_reopen_last_dataset", "theme", "main_window_geometry",
                "main_window_state", "active_layout_preset", "layout_presets",
                "auto_apply_preset_on_stage_change", "active_workflow_stage", "expanded_subsections", "ui_state",
            },
        )


if __name__ == "__main__":
    unittest.main()
