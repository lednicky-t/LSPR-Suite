"""Coverage for the two new widgets.py pieces added for the spectrum plot's
header row rework: ScientificAxis's scientific/SI-prefix tick-label toggle,
and MenuDropdownButton (the "[Absorbance]"-style popup-menu control that
replaced plot_selector's plain QComboBox). Needs a real QApplication -
pg.AxisItem is a QGraphicsWidget and crashes the interpreter outright if
constructed without one.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6.QtWidgets import QApplication

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.widgets import MenuDropdownButton, ScientificAxis


class _FakeAngleDelta:
    def __init__(self, y: int) -> None:
        self._y = y

    def y(self) -> int:
        return self._y


class _FakeWheelEvent:
    """Minimal stand-in for QWheelEvent - MenuDropdownButton.wheelEvent only
    ever calls angleDelta().y()/ignore()/accept(), so a real QWheelEvent
    (position, buttons, modifiers, phase...) would be unnecessary ceremony.
    """

    def __init__(self, delta_y: int) -> None:
        self._delta = _FakeAngleDelta(delta_y)
        self.ignored = False
        self.accepted = False

    def angleDelta(self) -> _FakeAngleDelta:
        return self._delta

    def ignore(self) -> None:
        self.ignored = True

    def accept(self) -> None:
        self.accepted = True


class ScientificAxisFormatModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.axis = ScientificAxis("left")

    def test_defaults_to_scientific(self) -> None:
        self.assertEqual(self.axis.tickStrings([65500.0], 1.0, 10.0), ["6.55e+04"])

    def test_si_mode_matches_the_requested_examples(self) -> None:
        self.axis.set_format_mode("si")
        self.assertEqual(self.axis.tickStrings([10000.0, 65500.0], 1.0, 10.0), ["10k", "65.5k"])

    def test_si_mode_handles_mega_and_giga(self) -> None:
        self.axis.set_format_mode("si")
        self.assertEqual(self.axis.tickStrings([1_230_000.0, 2_500_000_000.0], 1.0, 10.0), ["1.23M", "2.5G"])

    def test_si_mode_handles_small_values(self) -> None:
        self.axis.set_format_mode("si")
        self.assertEqual(self.axis.tickStrings([0.005, 0.0000012], 1.0, 10.0), ["5m", "1.2µ"])

    def test_mid_range_values_are_identical_between_modes(self) -> None:
        # Both modes share the same plain-decimal formatting for the "normal"
        # magnitude range (1e-2 <= |x| < 1e4) - only the compact notation
        # used outside that range differs.
        values = [0.5, 5.0, 50.0, 500.0]
        scientific = self.axis.tickStrings(values, 1.0, 10.0)
        self.axis.set_format_mode("si")
        si = self.axis.tickStrings(values, 1.0, 10.0)
        self.assertEqual(scientific, si)

    def test_zero_is_always_just_zero(self) -> None:
        self.axis.set_format_mode("si")
        self.assertEqual(self.axis.tickStrings([0.0], 1.0, 10.0), ["0"])

    def test_unknown_mode_falls_back_to_scientific(self) -> None:
        self.axis.set_format_mode("bogus")
        self.assertEqual(self.axis.tickStrings([65500.0], 1.0, 10.0), ["6.55e+04"])

    def test_setting_the_same_mode_again_is_a_cheap_no_op(self) -> None:
        self.axis.picture = "sentinel"
        self.axis.set_format_mode("scientific")
        self.assertEqual(self.axis.picture, "sentinel")

    def test_wide_tick_spacing_drops_unnecessary_decimals(self) -> None:
        # Sensorgram wavelength values (e.g. peak_nm around 500-600) used to
        # always show exactly 1 decimal (magnitude-bucketed formatting),
        # even when zoomed out enough that whole-nm ticks are plenty.
        self.assertEqual(
            self.axis.tickStrings([500.0, 550.0, 600.0], 1.0, 50.0),
            ["500", "550", "600"],
        )

    def test_tight_tick_spacing_adds_decimals_so_adjacent_ticks_differ(self) -> None:
        # Zoomed in enough that ticks are 0.02 nm apart - 1 decimal would
        # have rounded 525.30/525.32/525.34 down to identical "525.3" labels.
        self.assertEqual(
            self.axis.tickStrings([525.30, 525.32, 525.34], 1.0, 0.02),
            ["525.30", "525.32", "525.34"],
        )
        self.assertEqual(len(set(self.axis.tickStrings([525.30, 525.32, 525.34], 1.0, 0.02))), 3)

    def test_decimal_places_are_capped_for_pathologically_tiny_spacing(self) -> None:
        self.assertEqual(self.axis._decimal_places_for_spacing(1e-12), 6)

    def test_zero_or_invalid_spacing_falls_back_to_one_decimal(self) -> None:
        self.assertEqual(self.axis._decimal_places_for_spacing(0.0), 1)
        self.assertEqual(self.axis._decimal_places_for_spacing(-5.0), 1)
        self.assertEqual(self.axis._decimal_places_for_spacing(float("nan")), 1)


class MenuDropdownButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.sections = [("Live", ["Raw", "Absorbance"]), ("Stale", ["Reference", "Dark"])]

    def test_defaults_to_the_first_listed_option(self) -> None:
        button = MenuDropdownButton(self.sections)
        self.assertEqual(button.currentText(), "Raw")
        self.assertEqual(button.text(), "[Raw]")

    def test_header_rows_are_not_selectable_actions(self) -> None:
        button = MenuDropdownButton(self.sections)
        action_texts = [action.text() for action in button._menu.actions() if action.isCheckable()]
        self.assertEqual(action_texts, ["Raw", "Absorbance", "Reference", "Dark"])

    def test_set_current_text_updates_label_and_emits_signal(self) -> None:
        button = MenuDropdownButton(self.sections)
        seen: list[str] = []
        button.currentTextChanged.connect(seen.append)
        button.setCurrentText("Dark")
        self.assertEqual(button.currentText(), "Dark")
        self.assertEqual(button.text(), "[Dark]")
        self.assertEqual(seen, ["Dark"])

    def test_selecting_the_menu_action_updates_current_text(self) -> None:
        button = MenuDropdownButton(self.sections)
        button._actions["Absorbance"].trigger()
        self.assertEqual(button.currentText(), "Absorbance")

    def test_block_signals_suppresses_the_change_notification(self) -> None:
        button = MenuDropdownButton(self.sections)
        seen: list[str] = []
        button.currentTextChanged.connect(seen.append)
        button.blockSignals(True)
        button.setCurrentText("Reference")
        button.blockSignals(False)
        # State still changes even while blocked - only the notification is suppressed.
        self.assertEqual(button.currentText(), "Reference")
        self.assertEqual(button.text(), "[Reference]")
        self.assertEqual(seen, [])

    def test_setting_current_text_to_itself_is_a_no_op(self) -> None:
        button = MenuDropdownButton(self.sections)
        seen: list[str] = []
        button.currentTextChanged.connect(seen.append)
        button.setCurrentText("Raw")
        self.assertEqual(seen, [])

    def test_set_option_enabled_hides_and_disables_the_action(self) -> None:
        button = MenuDropdownButton(self.sections)
        button.set_option_enabled("Raw", False)
        self.assertFalse(button._actions["Raw"].isVisible())
        self.assertFalse(button._actions["Raw"].isEnabled())

        button.set_option_enabled("Raw", True)
        self.assertTrue(button._actions["Raw"].isVisible())
        self.assertTrue(button._actions["Raw"].isEnabled())

    def test_set_option_enabled_ignores_an_unknown_name(self) -> None:
        button = MenuDropdownButton(self.sections)
        button.set_option_enabled("Not a real option", False)  # must not raise

    def test_wheel_event_skips_hidden_options(self) -> None:
        # Order matches sections: Raw, Absorbance, Reference, Dark.
        button = MenuDropdownButton(self.sections)
        button.set_option_enabled("Absorbance", False)
        self.assertEqual(button.currentText(), "Raw")

        button.wheelEvent(_FakeWheelEvent(delta_y=120))

        self.assertEqual(button.currentText(), "Reference")

    def test_wheel_event_still_cycles_normally_when_nothing_is_hidden(self) -> None:
        button = MenuDropdownButton(self.sections)

        button.wheelEvent(_FakeWheelEvent(delta_y=120))

        self.assertEqual(button.currentText(), "Absorbance")

    def test_unknown_text_is_ignored(self) -> None:
        button = MenuDropdownButton(self.sections)
        button.setCurrentText("Not a real option")
        self.assertEqual(button.currentText(), "Raw")

    def test_flat_sections_without_a_header_are_supported(self) -> None:
        button = MenuDropdownButton([(None, ["A", "B"])])
        self.assertEqual(button.currentText(), "A")
        button.setCurrentText("B")
        self.assertEqual(button.currentText(), "B")


if __name__ == "__main__":
    unittest.main()
