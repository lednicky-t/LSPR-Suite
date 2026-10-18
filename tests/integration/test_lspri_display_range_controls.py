"""The View tab's "Display range" controls (`panels/image/display_range_controls.py`): a pin toggle, the black and
white point fields, and a fit-to-frame button. Real `QToolButton` / `QLineEdit` widgets driven the way a user
would (`click()`, typing then Enter), no `.exec()`.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtGui import QFontMetrics

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.display_range_controls import FULL_16BIT_RANGE, VALUE_MAX, DisplayRangeControls
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

from tests._qt_fonts import load_system_fonts  # noqa: E402

load_system_fonts()


def type_into(edit: QtWidgets.QLineEdit, text: str) -> None:
    """What a user does: replace the text and press Enter (`editingFinished`)."""
    edit.setText(text)
    edit.editingFinished.emit()


class DisplayRangeControlsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controls = DisplayRangeControls()
        self.changes: list[tuple[bool, float, float]] = []
        self.controls.changed.connect(lambda *args: self.changes.append(args))

    def _fields(self) -> tuple[str, str]:
        return self.controls._low_edit.text(), self.controls._high_edit.text()

    def test_it_starts_automatic_with_the_fields_greyed(self) -> None:
        self.assertEqual(self.controls.state(), (False, *FULL_16BIT_RANGE))
        self.assertFalse(self.controls._toggle.isChecked())
        self.assertTrue(self.controls._low_edit.isReadOnly())
        self.assertTrue(self.controls._high_edit.isReadOnly())
        self.assertFalse(self.controls._fit.isEnabled())
        self.assertFalse(self.controls._full.isEnabled())

    def test_while_automatic_the_fields_show_the_current_frames_own_range(self) -> None:
        self.controls.show_frame_levels((120.0, 3900.0))
        self.assertEqual(self._fields(), ("120", "3900"))
        self.controls.show_frame_levels((150.4, 4100.6))  # the next frame: shown as whole numbers
        self.assertEqual(self._fields(), ("150", "4101"))
        self.assertEqual(self.changes, [], "showing a readout is not a user change")

    def test_switching_on_starts_at_the_full_16_bit_range_not_at_the_frame(self) -> None:
        """Maintainer's choice (2026-10-14): the fixed range starts at 0 to 65535, whatever frame is shown."""
        self.controls.show_frame_levels((120.0, 3900.0))
        self.controls._toggle.click()
        self.assertEqual(self.changes, [(True, 0.0, 65535.0)])
        self.assertFalse(self.controls._low_edit.isReadOnly())
        self.assertEqual(self.controls.state(), (True, 0.0, 65535.0))
        self.assertEqual(self._fields(), ("0", "65535"))

    def test_with_no_frame_yet_it_starts_at_the_same_full_range(self) -> None:
        self.controls._toggle.click()
        self.assertEqual(self.changes, [(True, *FULL_16BIT_RANGE)])

    def test_once_fixed_the_next_frames_do_not_move_the_fields(self) -> None:
        self.controls.show_frame_levels((120.0, 3900.0))
        self.controls._toggle.click()
        self.controls.show_frame_levels((500.0, 9000.0))
        self.assertEqual(self._fields(), ("0", "65535"))
        self.assertEqual(self.controls.state(), (True, 0.0, 65535.0))

    def test_typing_a_value_reports_the_new_pair(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0))
        self.controls._toggle.click()
        self.changes.clear()
        type_into(self.controls._high_edit, "2500")
        self.assertEqual(self.changes, [(True, 0.0, 2500.0)])
        type_into(self.controls._low_edit, "50")
        self.assertEqual(self.changes[-1], (True, 50.0, 2500.0))

    def test_pressing_enter_without_a_change_reports_nothing(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0))
        self.controls._toggle.click()
        self.changes.clear()
        self.controls._high_edit.editingFinished.emit()
        self.assertEqual(self.changes, [])

    def test_an_empty_or_unreadable_field_goes_back_to_the_last_number(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0))
        self.controls._toggle.click()
        self.changes.clear()
        for text in ("", "-", "abc"):
            type_into(self.controls._high_edit, text)
            self.assertEqual(self._fields(), ("0", "65535"), repr(text))
        self.assertEqual(self.changes, [])

    def test_a_value_beyond_five_digits_is_kept_in_range(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0))
        self.controls._toggle.click()
        type_into(self.controls._high_edit, "123456")  # setText bypasses the validator, as a paste could
        self.assertEqual(self.changes[-1], (True, 0.0, float(VALUE_MAX)))
        self.assertEqual(self._fields()[1], str(VALUE_MAX))

    def test_switching_off_keeps_the_fixed_range_and_switching_on_again_brings_it_back(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0))
        self.controls._toggle.click()
        type_into(self.controls._high_edit, "2500")
        self.controls._toggle.click()  # off
        self.assertEqual(self.changes[-1], (False, 0.0, 2500.0))
        self.assertEqual(self._fields(), ("100", "4000"), "the fields show the frame's own range again")
        self.controls.show_frame_levels((300.0, 5000.0))
        self.controls._toggle.click()  # on again: the range chosen before, not this frame's
        self.assertEqual(self.changes[-1], (True, 0.0, 2500.0))
        self.assertEqual(self._fields(), ("0", "2500"))

    def test_fit_takes_the_current_frames_range_and_works_only_while_fixed(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0))
        self.assertFalse(self.controls._fit.isEnabled())
        self.controls._toggle.click()
        type_into(self.controls._high_edit, "900")
        self.controls.show_frame_levels((80.0, 5000.0))
        self.assertTrue(self.controls._fit.isEnabled())
        self.changes.clear()
        self.controls._fit.click()
        self.assertEqual(self.changes, [(True, 80.0, 5000.0)])
        self.assertEqual(self._fields(), ("80", "5000"))

    def test_the_full_16_bit_button_resets_the_range_and_works_only_while_fixed(self) -> None:
        self.assertFalse(self.controls._full.isEnabled())
        self.controls.show_frame_levels((100.0, 4000.0))
        self.controls._toggle.click()
        self.assertTrue(self.controls._full.isEnabled())
        type_into(self.controls._low_edit, "100")
        type_into(self.controls._high_edit, "2000")
        self.changes.clear()
        self.controls._full.click()
        self.assertEqual(self.changes, [(True, 0.0, 65535.0)])
        self.assertEqual(self._fields(), ("0", "65535"))
        self.controls._toggle.click()  # off again: nothing to apply it to
        self.assertFalse(self.controls._full.isEnabled())

    def test_fit_is_unavailable_without_a_frame(self) -> None:
        self.controls._toggle.click()
        self.controls.show_frame_levels(None)
        self.assertFalse(self.controls._fit.isEnabled())

    def test_set_state_changes_the_controls_without_reporting(self) -> None:
        self.controls.set_state(True, 200.0, 3000.0)
        self.assertEqual(self.changes, [])
        self.assertTrue(self.controls._toggle.isChecked())
        self.assertFalse(self.controls._low_edit.isReadOnly())
        self.assertEqual(self._fields(), ("200", "3000"))
        self.controls.set_state(False, 200.0, 3000.0)
        self.assertEqual(self.changes, [])
        self.assertTrue(self.controls._low_edit.isReadOnly())
        # a restored range counts as chosen: turning the pin on later must not overwrite it with the frame's
        self.controls.show_frame_levels((10.0, 20.0))
        self.controls._toggle.click()
        self.assertEqual(self.changes[-1], (True, 200.0, 3000.0))

    def test_typing_into_a_greyed_field_does_nothing(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0))
        type_into(self.controls._high_edit, "1234")  # as if an edit reached the read-only field
        self.assertEqual(self.changes, [])
        self.assertFalse(self.controls.state()[0])
        self.assertEqual(self._fields(), ("100", "4000"), "the readout is not overwritten")

    def test_the_fields_have_room_for_five_digits(self) -> None:
        """The rendered margin, not `.width()` alone (CLAUDE.md): the width the field was given minus the widest
        text it can show ("99999", the same convention as the Histogram's highlight range fields)."""
        for edit in (self.controls._low_edit, self.controls._high_edit):
            margin = edit.width() - QFontMetrics(edit.font()).boundingRect("99999").width()
            self.assertGreaterEqual(margin, 6, f"only {margin} px to spare")
            edit.setText("99999")
            self.assertEqual(edit.text(), "99999")

    def test_the_group_in_the_ribbon_is_just_two_buttons(self) -> None:
        """The View tab only has ~88 px of room before it sets the Image panel's minimum width (measured
        2026-10-14, real fonts: number fields in the row made the panel 80 px wider, spin boxes 217 px), so the
        row holds two 28 px buttons and the numbers live in a popup."""
        self.assertLessEqual(self.controls.minimumSizeHint().width(), 64)
        self.assertIs(self.controls._more.menu(), self.controls._menu)

    def test_the_numbers_and_fit_button_are_in_the_popup_panel(self) -> None:
        actions = self.controls._menu.actions()
        self.assertEqual(len(actions), 1)
        panel = actions[0].defaultWidget()
        for widget in (self.controls._low_edit, self.controls._high_edit, self.controls._fit, self.controls._full):
            self.assertTrue(panel.isAncestorOf(widget))

    def test_both_buttons_are_real_buttons_with_tooltips_and_icons(self) -> None:
        for button in (self.controls._toggle, self.controls._more, self.controls._fit, self.controls._full):
            self.assertIsInstance(button, QtWidgets.QToolButton)
            self.assertTrue(button.toolTip())
            self.assertFalse(button.icon().isNull())


if __name__ == "__main__":
    unittest.main()
