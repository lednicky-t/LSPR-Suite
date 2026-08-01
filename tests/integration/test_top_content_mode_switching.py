"""Regression tests for the Experiment Control panel restoration/visibility
bug (main_window_state.py's set_top_content_mode and
_restore_top_view_and_panel_visibility).

Bug reported by the maintainer: after restoring a session, the View menu's
"Experiment control" checkbox would sometimes show ticked while the panel
itself was not actually visible - or the panel simply failed to restore to
its previous form. Root causes traced and fixed:

1. window._top_view_mode (what ties the View menu's checkbox) used to be set
   unconditionally by set_top_content_mode(), even when the actual
   stack.setCurrentWidget() call was skipped because the experiment control
   panel's own session-restore bootstrap was still running
   (_experiment_control_bootstrap_in_progress). Fixed: _top_view_mode (and
   the menu sync) now only update once the widget switch has actually
   happened.
2. _restore_top_view_and_panel_visibility() used to set window._top_view_mode
   directly (bypassing set_top_content_mode entirely) while deferring the
   real switch - ticking the menu the moment a session with Experiment
   Control active was restored, long before the panel existed. Fixed: it now
   only records window._pending_top_view_mode.
3. main_window.py had `self._top_view_mode = "spectra"` positioned *after*
   self._restore_ui_state() in __init__, silently clobbering whatever
   restore had just set. Fixed by moving the default init before restore
   (not covered here - a MainWindow instance is too heavy to construct in a
   unit test; verified by direct code inspection and by the ordering tests
   below, which exercise the restore function in isolation).
"""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtWidgets import QApplication, QStackedWidget, QVBoxLayout, QWidget

from lspr_app.gui.main_window_state import _restore_top_view_and_panel_visibility, set_top_content_mode


class _FakeExperimentControlPanel(QWidget):
    def __init__(self, *, ui_startup_ready: bool, bootstrap_in_progress: bool) -> None:
        super().__init__()
        self._ui_startup_ready = ui_startup_ready
        self._experiment_control_bootstrap_in_progress = bootstrap_in_progress


def _make_window(*, ui_startup_ready: bool, bootstrap_in_progress: bool) -> SimpleNamespace:
    stack = QStackedWidget()
    spectra_block = QWidget()
    panel = _FakeExperimentControlPanel(ui_startup_ready=ui_startup_ready, bootstrap_in_progress=bootstrap_in_progress)
    stack.addWidget(spectra_block)
    stack.addWidget(panel)
    stack.setCurrentWidget(spectra_block)
    return SimpleNamespace(
        _top_content_stack=stack,
        _spectra_block=spectra_block,
        _experiment_control_window=panel,
        _top_view_mode="spectra",
        _pending_top_view_mode=None,
        _sync_view_actions=MagicMock(),
        _schedule_ui_state_persist=MagicMock(),
    )


class SetTopContentModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_switch_deferred_while_bootstrap_in_progress_does_not_tick_menu(self) -> None:
        window = _make_window(ui_startup_ready=True, bootstrap_in_progress=True)

        set_top_content_mode(window, "experimental_control")

        # The actual visible page never changed...
        self.assertIs(window._top_content_stack.currentWidget(), window._spectra_block)
        # ...so _top_view_mode (what the View menu ticks) must not claim it did.
        self.assertEqual(window._top_view_mode, "spectra")
        self.assertEqual(window._pending_top_view_mode, "experimental_control")
        window._sync_view_actions.assert_not_called()

    def test_switch_succeeds_once_ready(self) -> None:
        window = _make_window(ui_startup_ready=True, bootstrap_in_progress=False)

        set_top_content_mode(window, "experimental_control")

        self.assertIs(window._top_content_stack.currentWidget(), window._experiment_control_window)
        self.assertEqual(window._top_view_mode, "experimental_control")
        self.assertIsNone(window._pending_top_view_mode)
        window._sync_view_actions.assert_called_once()

    def test_pending_mode_resolves_and_clears_once_bootstrap_finishes(self) -> None:
        window = _make_window(ui_startup_ready=True, bootstrap_in_progress=True)
        set_top_content_mode(window, "experimental_control")
        self.assertEqual(window._pending_top_view_mode, "experimental_control")

        # Bootstrap finishes - a later call (as the real
        # _requeue_pending_top_view_switch/_apply_pending_experiment_control_view_mode
        # path does) should now actually switch and clean up the flag.
        window._experiment_control_window._experiment_control_bootstrap_in_progress = False
        set_top_content_mode(window, "experimental_control")

        self.assertIs(window._top_content_stack.currentWidget(), window._experiment_control_window)
        self.assertIsNone(window._pending_top_view_mode)
        window._sync_view_actions.assert_called_once()

    def test_switching_to_spectra_always_succeeds_and_ticks_menu(self) -> None:
        window = _make_window(ui_startup_ready=False, bootstrap_in_progress=True)

        set_top_content_mode(window, "spectra")

        self.assertIs(window._top_content_stack.currentWidget(), window._spectra_block)
        self.assertEqual(window._top_view_mode, "spectra")
        window._sync_view_actions.assert_called_once()


class RestoreTopViewAndPanelVisibilityTests(unittest.TestCase):
    def _window(self, *, ui_startup_ready: bool) -> SimpleNamespace:
        return SimpleNamespace(
            _top_view_mode="spectra",
            _pending_top_view_mode=None,
            _ui_startup_ready=ui_startup_ready,
            _activate_experiment_control_view=MagicMock(),
            _activate_spectra_view=MagicMock(),
        )

    def test_early_restore_only_records_intent_not_top_view_mode(self) -> None:
        window = self._window(ui_startup_ready=False)

        _restore_top_view_and_panel_visibility(window, {"top_view_mode": "experimental_control"})

        # This is the crux of the bug: at this point in real startup the
        # panel doesn't exist yet and the View menu already reflects
        # _top_view_mode - it must still say "spectra" here.
        self.assertEqual(window._top_view_mode, "spectra")
        self.assertEqual(window._pending_top_view_mode, "experimental_control")
        window._activate_experiment_control_view.assert_not_called()

    def test_restore_when_already_ready_uses_the_canonical_activate_method(self) -> None:
        window = self._window(ui_startup_ready=True)

        _restore_top_view_and_panel_visibility(window, {"top_view_mode": "experimental_control"})

        window._activate_experiment_control_view.assert_called_once()

    def test_restore_spectra_mode_activates_spectra(self) -> None:
        window = self._window(ui_startup_ready=False)

        _restore_top_view_and_panel_visibility(window, {"top_view_mode": "spectra"})

        window._activate_spectra_view.assert_called_once()
        self.assertIsNone(window._pending_top_view_mode)


class SetTopContentModeMeasurementVariantTests(unittest.TestCase):
    """Regression test for a second, separate bug found while investigating
    the maintainer's report: the "measurement" layout preset
    (_apply_measurement_layout_preset) permanently reparents the experiment
    control panel into its own fixed 3-pane arrangement, outside
    _top_content_stack entirely. A stray call to set_top_content_mode
    reaching this state afterwards - e.g. from a window._pending_top_view_mode
    left over from before the layout preset was known during startup - used
    to unconditionally re-parent the panel back into _top_content_stack via
    ensure_experiment_control_stack_page(), ripping it out of the measurement
    layout's (now hidden, per the "standard"-variant page's own visibility)
    container. This is what actually produced the maintainer's totally blank
    Experiment Control area that a manual View-menu toggle couldn't fix -
    confirmed by replaying the maintainer's real saved session state.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _measurement_window(self) -> tuple[SimpleNamespace, QWidget, QWidget]:
        stack = QStackedWidget()
        spectra_block = QWidget()
        stack.addWidget(spectra_block)

        # As _apply_measurement_layout_preset does: the panel lives in a
        # separate host widget (_measurement_top_host), not in
        # _top_content_stack at all.
        measurement_host = QWidget()
        measurement_host.setLayout(QVBoxLayout())
        panel = _FakeExperimentControlPanel(ui_startup_ready=True, bootstrap_in_progress=False)
        measurement_host.layout().addWidget(panel)

        window = SimpleNamespace(
            _top_content_stack=stack,
            _spectra_block=spectra_block,
            _experiment_control_window=panel,
            _measurement_top_host=measurement_host,
            _top_view_mode="experimental_control",
            # Stale, as if left over from _restore_top_view_and_panel_visibility
            # running before _restore_maximized_and_layout_presets discovered
            # the saved layout preset was "measurement".
            _pending_top_view_mode="experimental_control",
            _layout_preset_active_variant="measurement",
            _sync_view_actions=MagicMock(),
            _schedule_ui_state_persist=MagicMock(),
        )
        return window, panel, measurement_host

    def test_does_not_reparent_panel_out_of_the_measurement_layout(self) -> None:
        window, panel, measurement_host = self._measurement_window()

        set_top_content_mode(window, "experimental_control")

        self.assertIs(panel.parent(), measurement_host)
        self.assertEqual(window._top_content_stack.indexOf(panel), -1)
        self.assertEqual(window._top_view_mode, "experimental_control")
        self.assertIsNone(window._pending_top_view_mode)
        window._sync_view_actions.assert_called_once()

    def test_toggling_off_hides_the_top_host_not_a_no_op(self) -> None:
        """The maintainer's follow-up request: hiding/showing a panel in a
        layout preset must be a real, meaningful (and savable) choice, not
        silently ignored just because the fix above stops it from corrupting
        the layout."""
        window, panel, measurement_host = self._measurement_window()
        measurement_host.setVisible(True)

        set_top_content_mode(window, "spectra")

        self.assertTrue(measurement_host.isHidden())
        self.assertEqual(window._top_view_mode, "spectra")
        # Still in the measurement layout - the panel itself isn't touched,
        # only its host's visibility.
        self.assertIs(panel.parent(), measurement_host)

    def test_toggling_back_on_shows_the_top_host_again(self) -> None:
        window, panel, measurement_host = self._measurement_window()
        measurement_host.setVisible(False)

        set_top_content_mode(window, "experimental_control")

        self.assertFalse(measurement_host.isHidden())
        self.assertEqual(window._top_view_mode, "experimental_control")


class ApplyMeasurementLayoutPresetTopHostVisibilityTests(unittest.TestCase):
    """A hidden top host isn't just a live-toggle thing - it must round-trip
    through save_current_layout_to_preset / apply_layout_preset too, so a
    user's choice "stays as saved" (per the maintainer) while the built-in
    default preset (top pane visible) is still always available via Reset
    layout presets to defaults."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self) -> SimpleNamespace:
        measurement_top_host = QWidget()
        measurement_top_host.setLayout(QVBoxLayout())
        measurement_bottom_left_host = QWidget()
        measurement_bottom_left_host.setLayout(QVBoxLayout())
        measurement_bottom_right_host = QWidget()
        measurement_bottom_right_host.setLayout(QVBoxLayout())
        panel = _FakeExperimentControlPanel(ui_startup_ready=True, bootstrap_in_progress=False)
        return SimpleNamespace(
            _experiment_control_window=panel,
            _spectra_block=QWidget(),
            _sensorgram_block=QWidget(),
            _measurement_top_host=measurement_top_host,
            _measurement_bottom_left_host=measurement_bottom_left_host,
            _measurement_bottom_right_host=measurement_bottom_right_host,
            _measurement_vertical_splitter=None,
            _measurement_bottom_splitter=None,
        )

    def test_restoring_a_preset_saved_with_top_host_hidden_keeps_it_hidden(self) -> None:
        from lspr_app.gui.main_window_state import _apply_measurement_layout_preset

        window = self._window()

        _apply_measurement_layout_preset(window, {"top_view_mode": "spectra"})

        self.assertTrue(window._measurement_top_host.isHidden())
        self.assertEqual(window._top_view_mode, "spectra")

    def test_restoring_the_default_snapshot_shows_the_top_host(self) -> None:
        from lspr_app.gui.main_window_state import _apply_measurement_layout_preset, _default_layout_preset_snapshot

        window = self._window()
        window._measurement_top_host.setVisible(False)

        _apply_measurement_layout_preset(window, _default_layout_preset_snapshot("measurement"))

        self.assertFalse(window._measurement_top_host.isHidden())
        self.assertEqual(window._top_view_mode, "experimental_control")


if __name__ == "__main__":
    unittest.main()
