"""Tests for the shared experiment-control theme (Tier 3b, 2026-08-10) -
`lspr_acq_shell.experiment_control_theme`. Tests the real owner directly,
no window construction needed - both apps' `_theme_palette()`/`_apply_style()`
are now thin wrappers over this module.
"""

from __future__ import annotations

import unittest

from PyQt6.QtWidgets import QApplication, QWidget

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_acq_shell.experiment_control_theme import (
    apply_experiment_control_style,
    experiment_control_theme_palette,
)

_APP = QApplication.instance() or QApplication([])

_REQUIRED_KEYS = {
    "bg", "fg", "muted", "field", "button", "button_hover", "button_pressed",
    "accent_button", "accent_hover", "title", "danger_button", "danger_hover",
    "border", "border_hover", "pressed", "scroll", "scroll_hover", "splitter",
    "timeline_bg", "header", "selection", "mode_toggle_accent",
}


class ExperimentControlThemePaletteTests(unittest.TestCase):
    def test_dark_palette_has_every_required_key(self) -> None:
        palette = experiment_control_theme_palette("dark")
        self.assertEqual(set(palette), _REQUIRED_KEYS)

    def test_light_palette_has_every_required_key(self) -> None:
        palette = experiment_control_theme_palette("light")
        self.assertEqual(set(palette), _REQUIRED_KEYS)

    def test_dark_and_light_are_different_palettes(self) -> None:
        self.assertNotEqual(experiment_control_theme_palette("dark"), experiment_control_theme_palette("light"))

    def test_unknown_mode_falls_back_to_light(self) -> None:
        self.assertEqual(experiment_control_theme_palette("nonsense"), experiment_control_theme_palette("light"))

    def test_returns_a_fresh_copy_each_call(self) -> None:
        first = experiment_control_theme_palette("dark")
        first["bg"] = "mutated"
        self.assertNotEqual(experiment_control_theme_palette("dark")["bg"], "mutated")


class ApplyExperimentControlStyleTests(unittest.TestCase):
    def test_applies_a_non_empty_stylesheet_without_raising(self) -> None:
        widget = QWidget()
        apply_experiment_control_style(widget, experiment_control_theme_palette("dark"))
        self.assertTrue(widget.styleSheet())

    def test_no_unsubstituted_placeholders_remain(self) -> None:
        widget = QWidget()
        apply_experiment_control_style(widget, experiment_control_theme_palette("dark"))
        self.assertNotIn("%(", widget.styleSheet())

    def test_checked_flow_icon_button_rule_is_present(self) -> None:
        """LSPRi acq's own real addition (used by its checkable hold/pause/
        edit-mode toggle buttons) - merged into the shared template rather
        than dropped when this module was created from sLSPR acq's version."""
        widget = QWidget()
        apply_experiment_control_style(widget, experiment_control_theme_palette("dark"))
        self.assertIn("QToolButton#flowIconButton:checked", widget.styleSheet())

    def test_missing_palette_key_raises(self) -> None:
        widget = QWidget()
        with self.assertRaises(KeyError):
            apply_experiment_control_style(widget, {"bg": "#000000"})


if __name__ == "__main__":
    unittest.main()
