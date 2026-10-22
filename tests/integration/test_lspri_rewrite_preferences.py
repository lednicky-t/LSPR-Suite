"""Edit -> Preferences... of the LSPRi rewrite (2026-10-19), which replaced the Options menu.

The dialog on its own (values, choices, Restore Defaults) and inside the real window (the menu entry, OK applies only
what changed, Cancel applies nothing, the settings are saved). A real `QApplication` built in-process, never `.exec()`;
the dialog is opened by the action and driven through its buttons (`.click()`), never by screen coordinate.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_core.perf import PerfPreferences, PerfRuntime, get_runtime, set_runtime
    from lspr_imaging_app.app_rewrite import build_main_window
    from lspr_imaging_app.panels.dock_container import PanelContainer
    from lspr_imaging_app.panels.histogram import HistogramPanel
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.overlay_cache import BUDGET_CHOICES_MB, DEFAULT_BUDGET_MB
    from lspr_imaging_app.panels.preferences_dialog import DEFAULT_PREFERENCES, Preferences, PreferencesDialog
    from lspr_imaging_app.panels.workflow import WorkflowPanel, WorkflowStage
    from lspr_imaging_app.storage.app_settings import AppSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

Button = QtWidgets.QDialogButtonBox.StandardButton


class DialogTests(unittest.TestCase):
    def test_the_defaults_are_the_apps_own(self) -> None:
        fresh = AppSettings()
        self.assertEqual(
            DEFAULT_PREFERENCES,
            Preferences(
                reopen_last_dataset=fresh.auto_reopen_last_dataset,
                auto_apply_layout_preset=fresh.auto_apply_preset_on_stage_change,
                overlay_cache_mb=DEFAULT_BUDGET_MB,
            ),
        )

    def test_it_shows_the_current_values_and_returns_them_unchanged(self) -> None:
        current = Preferences(reopen_last_dataset=False, auto_apply_layout_preset=True, overlay_cache_mb=512)
        dialog = PreferencesDialog(current)
        self.assertFalse(dialog.reopen_check.isChecked())
        self.assertTrue(dialog.auto_apply_check.isChecked())
        self.assertEqual(dialog.values(), current)

    def test_the_cache_choices_are_off_and_the_sizes_with_the_default_marked(self) -> None:
        dialog = PreferencesDialog(DEFAULT_PREFERENCES)
        labels = [dialog.cache_combo.itemText(i) for i in range(dialog.cache_combo.count())]
        self.assertEqual(labels, ["Off", "128 MB", "256 MB (default)", "512 MB", "1024 MB"])
        self.assertEqual(dialog.cache_combo.currentData(), DEFAULT_BUDGET_MB)

    def test_a_size_that_is_not_a_choice_is_listed_and_selected_not_replaced(self) -> None:
        """A size set by `LSPR_OVERLAY_CACHE_MB` (here 64): OK must not silently change it to a listed size."""
        self.assertNotIn(64, BUDGET_CHOICES_MB)
        dialog = PreferencesDialog(Preferences(overlay_cache_mb=64))
        self.assertEqual(dialog.cache_combo.currentText(), "64 MB")
        self.assertEqual(dialog.values().overlay_cache_mb, 64)

    def test_restore_defaults_puts_the_defaults_on_screen(self) -> None:
        dialog = PreferencesDialog(Preferences(reopen_last_dataset=False, auto_apply_layout_preset=True, overlay_cache_mb=0))
        dialog.button_box.button(Button.RestoreDefaults).click()
        self.assertEqual(dialog.values(), DEFAULT_PREFERENCES)

    def test_the_status_and_environment_notes_are_shown_only_when_there_is_something_to_say(self) -> None:
        plain = PreferencesDialog(DEFAULT_PREFERENCES)
        self.assertTrue(plain.cache_status_label.isHidden())
        self.assertTrue(plain.cache_environment_label.isHidden())
        noted = PreferencesDialog(DEFAULT_PREFERENCES, overlay_cache_status="3 of 27 cached", cache_set_by_environment=True)
        self.assertFalse(noted.cache_status_label.isHidden())
        self.assertEqual(noted.cache_status_label.text(), "3 of 27 cached")
        self.assertFalse(noted.cache_environment_label.isHidden())
        self.assertIn("LSPR_OVERLAY_CACHE_MB", noted.cache_environment_label.text())

    def test_the_parallel_work_choices_are_shown_and_returned_unchanged(self) -> None:
        current = Preferences(perf=PerfPreferences(mode="manual", manual_workers=3, reserved_cores=2, low_priority=False))
        dialog = PreferencesDialog(current, core_count=8)
        self.assertEqual(dialog.perf_mode_combo.currentData(), "manual")
        self.assertEqual((dialog.perf_workers_spin.value(), dialog.perf_reserved_spin.value()), (3, 2))
        self.assertFalse(dialog.perf_low_priority_check.isChecked())
        self.assertEqual(dialog.values(), current)

    def test_a_fixed_number_enables_the_number_and_automatic_enables_the_reserve(self) -> None:
        dialog = PreferencesDialog(DEFAULT_PREFERENCES, core_count=8)
        self.assertEqual(dialog.perf_mode_combo.currentData(), "standard", "Standard is the default; tuning is opt-in")
        self.assertFalse(dialog.perf_workers_spin.isEnabled())
        self.assertTrue(dialog.perf_reserved_spin.isEnabled())
        dialog.perf_mode_combo.setCurrentIndex(dialog.perf_mode_combo.findData("auto"))
        self.assertFalse(dialog.perf_workers_spin.isEnabled())
        self.assertTrue(dialog.perf_reserved_spin.isEnabled())
        self.assertEqual(dialog.values().perf, PerfPreferences(mode="auto"))
        dialog.perf_mode_combo.setCurrentIndex(dialog.perf_mode_combo.findData("manual"))
        self.assertTrue(dialog.perf_workers_spin.isEnabled())
        self.assertFalse(dialog.perf_reserved_spin.isEnabled())
        dialog.perf_workers_spin.setValue(4)
        self.assertEqual(dialog.values().perf, PerfPreferences(mode="manual", manual_workers=4))

    def test_the_number_is_not_a_choice_until_fixed_is_selected(self) -> None:
        """The spin box always shows a number (a suggestion), but automatic mode must not turn it into a saved choice."""
        fresh = PreferencesDialog(DEFAULT_PREFERENCES, core_count=8)
        self.assertEqual(fresh.values().perf.manual_workers, 0)
        remembered = PreferencesDialog(Preferences(perf=PerfPreferences(mode="standard", manual_workers=4)), core_count=8)
        self.assertEqual(remembered.perf_workers_spin.value(), 4)
        self.assertEqual(remembered.values().perf.manual_workers, 4, "the earlier fixed number is kept while automatic is on")

    def test_the_three_modes_are_listed_standard_first(self) -> None:
        dialog = PreferencesDialog(DEFAULT_PREFERENCES)
        self.assertEqual([dialog.perf_mode_combo.itemData(i) for i in range(dialog.perf_mode_combo.count())], ["standard", "auto", "manual"])

    def test_forget_saved_counts_is_only_offered_when_something_is_saved_and_is_a_request_until_ok(self) -> None:
        none_saved = PreferencesDialog(DEFAULT_PREFERENCES, perf_saved_count=0)
        self.assertFalse(none_saved.perf_forget_button.isEnabled())
        some = PreferencesDialog(DEFAULT_PREFERENCES, perf_saved_count=2)
        self.assertTrue(some.perf_forget_button.isEnabled())
        self.assertFalse(some.forget_saved_requested)
        some.perf_forget_button.click()
        self.assertTrue(some.forget_saved_requested)
        self.assertFalse(some.perf_forget_button.isEnabled())
        self.assertEqual(some.values(), DEFAULT_PREFERENCES, "forgetting is not a preference value")

    def test_the_limits_follow_the_core_count(self) -> None:
        dialog = PreferencesDialog(DEFAULT_PREFERENCES, core_count=4)
        self.assertEqual((dialog.perf_workers_spin.minimum(), dialog.perf_workers_spin.maximum()), (1, 8))
        self.assertEqual((dialog.perf_reserved_spin.minimum(), dialog.perf_reserved_spin.maximum()), (0, 3))

    def test_the_machine_status_is_shown_only_when_given(self) -> None:
        plain = PreferencesDialog(DEFAULT_PREFERENCES)
        self.assertTrue(plain.perf_status_label.isHidden())
        shown = PreferencesDialog(DEFAULT_PREFERENCES, perf_status="This computer: 4 cores.")
        self.assertFalse(shown.perf_status_label.isHidden())
        self.assertEqual(shown.perf_status_label.text(), "This computer: 4 cores.")

    def test_ok_accepts_and_cancel_rejects(self) -> None:
        dialog = PreferencesDialog(DEFAULT_PREFERENCES)
        results: list[str] = []
        dialog.accepted.connect(lambda: results.append("accepted"))
        dialog.rejected.connect(lambda: results.append("rejected"))
        dialog.button_box.button(Button.Ok).click()
        dialog.button_box.button(Button.Cancel).click()
        self.assertEqual(results, ["accepted", "rejected"])


class WindowTests(unittest.TestCase):
    def setUp(self) -> None:
        previous_runtime = set_runtime(PerfRuntime())  # a window applies its saved choices to the runtime: keep that out of other tests
        self.addCleanup(set_runtime, previous_runtime)
        self.saved: list[AppSettings] = []
        # The callback gets the live settings object, so keep copies of what it holds at each call.
        self.window = build_main_window(AppSettings(), lambda settings: self.saved.append(AppSettings(**vars(settings))))
        self.panel = self.window.findChild(ImagePanel)
        self.preferences_action = next(
            a for a in next(m for m in self._menus() if m.title() == "&Edit").actions() if a.text() == "Preferences..."
        )

    def tearDown(self) -> None:
        for dialog in self.window.findChildren(PreferencesDialog):
            dialog.close()
        self.window.close()

    def _menus(self):
        return [a.menu() for a in self.window.menuBar().actions()]

    def _open(self) -> PreferencesDialog:
        self.preferences_action.trigger()
        # An accepted dialog is deleted later (WA_DeleteOnClose, needs the event loop): pick the one on screen.
        shown = [d for d in self.window.findChildren(PreferencesDialog) if d.isVisible()]
        self.assertEqual(len(shown), 1, "Edit -> Preferences... opens exactly one dialog")
        return shown[0]

    def _histogram_dock(self) -> PanelContainer:
        widget = self.window.findChild(HistogramPanel)
        while widget is not None and not isinstance(widget, PanelContainer):
            widget = widget.parent()
        return widget

    def test_the_options_menu_is_gone_and_preferences_sits_in_edit(self) -> None:
        self.assertEqual([m.title() for m in self._menus()], ["&File", "&Edit", "&View", "&Help"])
        self.assertEqual(self.preferences_action.shortcut().toString(), "Ctrl+,")

    def test_the_dialog_starts_with_the_apps_current_values(self) -> None:
        dialog = self._open()
        self.assertEqual(dialog.values(), DEFAULT_PREFERENCES)
        self.assertEqual(dialog.values().overlay_cache_mb, self.panel.overlay_cache_mb)

    def test_opening_it_twice_brings_the_same_dialog_forward(self) -> None:
        first = self._open()
        self.preferences_action.trigger()
        self.assertEqual(len(self.window.findChildren(PreferencesDialog)), 1)
        self.assertIs(self._open(), first)

    def test_cancel_applies_and_saves_nothing(self) -> None:
        dialog = self._open()
        dialog.reopen_check.setChecked(False)
        dialog.auto_apply_check.setChecked(True)
        dialog.cache_combo.setCurrentIndex(dialog.cache_combo.findData(0))
        dialog.button_box.button(Button.Cancel).click()
        self.assertEqual(self.panel.overlay_cache_mb, DEFAULT_BUDGET_MB)
        self.assertEqual(self.saved, [])

    def test_ok_without_a_change_applies_and_saves_nothing(self) -> None:
        self._open().button_box.button(Button.Ok).click()
        self.assertEqual(self.saved, [])
        self.assertEqual(self.panel.overlay_cache_mb, DEFAULT_BUDGET_MB)

    def test_ok_applies_the_cache_size_at_once(self) -> None:
        dialog = self._open()
        dialog.cache_combo.setCurrentIndex(dialog.cache_combo.findData(0))
        dialog.button_box.button(Button.Ok).click()
        self.assertEqual(self.panel.overlay_cache_mb, 0)
        self.assertFalse(self.panel._roi_overlay.layers.enabled)
        self.assertEqual(self._open().values().overlay_cache_mb, 0, "reopening shows what was applied")

    def test_ok_applies_the_parallel_work_choices_to_the_runtime_for_the_next_job(self) -> None:
        dialog = self._open()
        dialog.perf_mode_combo.setCurrentIndex(dialog.perf_mode_combo.findData("manual"))
        dialog.perf_workers_spin.setValue(3)
        dialog.perf_low_priority_check.setChecked(False)
        dialog.button_box.button(Button.Ok).click()
        self.assertEqual(get_runtime().preferences, PerfPreferences(mode="manual", manual_workers=3, low_priority=False))
        self.assertEqual(self._open().values().perf, get_runtime().preferences, "reopening shows what was applied")

    def test_ok_after_forget_deletes_the_saved_counts_and_cancel_does_not(self) -> None:
        from lspr_core.perf import ProfileStore
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = ProfileStore(Path(tmp.name) / "perf.json")
        runtime = get_runtime()
        runtime.store = store
        store.record(runtime.probe().fingerprint(), "export.ome_zarr", "", workers=4, rate=30.0)
        cancelled = self._open()
        self.assertTrue(cancelled.perf_forget_button.isEnabled())
        cancelled.perf_forget_button.click()
        cancelled.button_box.button(Button.Cancel).click()
        self.assertEqual(len(runtime.learned_all()), 1, "Cancel forgets nothing")
        accepted = self._open()
        accepted.perf_forget_button.click()
        accepted.button_box.button(Button.Ok).click()
        self.assertEqual(runtime.learned_all(), [])

    def test_cancel_leaves_the_parallel_work_choices_alone(self) -> None:
        dialog = self._open()
        dialog.perf_mode_combo.setCurrentIndex(dialog.perf_mode_combo.findData("manual"))
        dialog.button_box.button(Button.Cancel).click()
        self.assertEqual(get_runtime().preferences, PerfPreferences())

    def test_ok_saves_the_reopen_choice(self) -> None:
        dialog = self._open()
        dialog.reopen_check.setChecked(False)
        dialog.button_box.button(Button.Ok).click()
        self.assertFalse(self.saved[-1].auto_reopen_last_dataset)

    def test_auto_apply_off_leaves_the_panels_alone_when_the_stage_changes(self) -> None:
        self.window.findChild(WorkflowPanel).stage_changed.emit(WorkflowStage.ANALYSIS)
        self.assertFalse(self._histogram_dock().isHidden())

    def test_auto_apply_on_arranges_the_panels_when_the_stage_changes_and_is_saved(self) -> None:
        dialog = self._open()
        dialog.auto_apply_check.setChecked(True)
        dialog.button_box.button(Button.Ok).click()
        self.assertTrue(self.saved[-1].auto_apply_preset_on_stage_change)
        self.window.findChild(WorkflowPanel).stage_changed.emit(WorkflowStage.ANALYSIS)
        self.assertTrue(self._histogram_dock().isHidden(), "the Analysis preset does not show the Histogram")


if __name__ == "__main__":
    unittest.main()
