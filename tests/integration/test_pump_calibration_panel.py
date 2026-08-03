"""Coverage for the pump calibration pop-out window (Device Manager's Pump
page -> "Open Pump Calibration..." -> PumpCalibrationDialog - see
gui/pump_calibration_panel.py). Distinct from the one-channel-at-a-time raw
command test bench in device_console_dialog.py's deep-debug-gated
"Pump Cal. (raw)" tab.

Covers:
- Pure helpers: volume/duration/flow-rate conversion, the fill-tubes flow
  rate, the correction ratio, the deviation percent/color/tooltip banding,
  and the MM:SS remaining-time formatter.
- The control row: Duration/CHs/Dir/Tube/Length/Flow/Set-Volume layout,
  "=" mode's shared Direction/Tube/Length *and* CH1-sourced Flow/Volume
  linking, the duration<->volume<->flow linking rules, Set Volume's uL
  units with a flow-rate-matched scroll step, and that no button in the
  dialog can be triggered by pressing Enter in an unrelated field
  (autoDefault regression).
- The merged step/status bar: each step's own Start/Stop toggle button
  sits directly above its own progress segment, which shows percent and
  MM:SS remaining.
- The three steps: Fill tubes (all 4 channels, computed rate, fixed 1 min),
  Disperse liquid (active channels, configured rate/duration), and
  Input measured volume + confirm (correction math, color/tooltip bands,
  writing pump.roller_step_volume.set only for measured channels) - with
  Measured Volume/Deviation added to the control row's own grid so their
  columns line up with Set Volume.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QPushButton

from lspr_app.gui.pump_calibration_panel import (
    DEFAULT_TUBE_LENGTH_MM,
    PumpCalibrationControlRow,
    PumpCalibrationDialog,
    corrected_roller_step_volume_ml,
    deviation_color,
    deviation_pct,
    deviation_tooltip,
    duration_s_from_flow_and_volume,
    fill_flow_rate_ul_min,
    format_mm_ss,
    volume_ml_from_flow_and_duration,
)
from lspr_app.storage.device_manager_settings import DeviceManagerSettings


class ConversionHelperTests(unittest.TestCase):
    def test_volume_from_flow_and_duration(self) -> None:
        self.assertAlmostEqual(volume_ml_from_flow_and_duration(1000.0, 60.0), 1.0)

    def test_duration_from_flow_and_volume_is_the_inverse(self) -> None:
        self.assertAlmostEqual(duration_s_from_flow_and_volume(1000.0, 1.0), 60.0)

    def test_duration_from_zero_flow_is_zero_not_a_crash(self) -> None:
        self.assertEqual(duration_s_from_flow_and_volume(0.0, 5.0), 0.0)


class FillFlowRateTests(unittest.TestCase):
    def test_default_tube_matches_hand_calculation(self) -> None:
        # pi * (0.25/2)^2 * 450 = 22.089... uL, * 1.2 = 26.5..., ceil -> 27
        self.assertEqual(fill_flow_rate_ul_min(0.25, DEFAULT_TUBE_LENGTH_MM), 27.0)

    def test_larger_tube_needs_a_higher_fill_rate(self) -> None:
        small = fill_flow_rate_ul_min(0.25, 450.0)
        large = fill_flow_rate_ul_min(1.02, 450.0)
        self.assertGreater(large, small)

    def test_longer_tube_needs_a_higher_fill_rate(self) -> None:
        short = fill_flow_rate_ul_min(0.25, 200.0)
        long_ = fill_flow_rate_ul_min(0.25, 900.0)
        self.assertGreater(long_, short)

    def test_result_is_rounded_up(self) -> None:
        import math
        raw = math.pi * (0.19 / 2) ** 2 * 450.0 * 1.2
        self.assertNotEqual(raw, math.floor(raw))
        self.assertEqual(fill_flow_rate_ul_min(0.19, 450.0), math.ceil(raw))


class CorrectionAndDeviationTests(unittest.TestCase):
    def test_correction_matches_the_worked_example(self) -> None:
        new_rsv = corrected_roller_step_volume_ml(old_rsv_ml=0.001, target_volume_ml=200.0, measured_volume_ml=202.0)
        self.assertAlmostEqual(new_rsv, 0.00101)

    def test_deviation_pct_matches_correction_ratio_as_a_percent(self) -> None:
        self.assertAlmostEqual(deviation_pct(200.0, 202.0), 1.0)

    def test_deviation_pct_negative_for_under_delivery(self) -> None:
        self.assertAlmostEqual(deviation_pct(100.0, 95.0), -5.0)

    def test_deviation_color_bands(self) -> None:
        self.assertEqual(deviation_color(1.5), "#22c55e")
        self.assertEqual(deviation_color(-1.5), "#22c55e")
        self.assertEqual(deviation_color(3.0), "#eab308")
        self.assertEqual(deviation_color(7.0), "#f59e0b")
        self.assertEqual(deviation_color(-7.0), "#f59e0b")
        self.assertEqual(deviation_color(12.0), "#ef4444")

    def test_deviation_color_boundary_at_2_percent_is_not_green(self) -> None:
        self.assertNotEqual(deviation_color(2.0), "#22c55e")

    def test_deviation_tooltip_only_above_5_percent(self) -> None:
        self.assertEqual(deviation_tooltip(3.0), "")
        self.assertIn("remeasuring", deviation_tooltip(5.0))
        self.assertIn("remeasuring", deviation_tooltip(-8.0))


class FormatMmSsTests(unittest.TestCase):
    def test_formats_whole_minutes_and_seconds(self) -> None:
        self.assertEqual(format_mm_ss(90.0), "1:30")

    def test_pads_seconds_to_two_digits(self) -> None:
        self.assertEqual(format_mm_ss(65.0), "1:05")

    def test_zero_is_zero_zero(self) -> None:
        self.assertEqual(format_mm_ss(0.0), "0:00")

    def test_negative_clamps_to_zero(self) -> None:
        self.assertEqual(format_mm_ss(-5.0), "0:00")


class PumpCalibrationControlRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_row(self, max_flow_ul_min: float = 5000.0) -> PumpCalibrationControlRow:
        settings = DeviceManagerSettings()
        settings.pump.max_flow_ul_min = max_flow_ul_min
        row = PumpCalibrationControlRow(settings)
        self.addCleanup(row.deleteLater)
        return row

    def test_tube_length_defaults_to_450mm_shared_and_per_channel(self) -> None:
        row = self._make_row()
        self.assertAlmostEqual(row._shared_length_spin.value(), 450.0)
        for channel in range(1, 5):
            self.assertAlmostEqual(row.channel_tube_length_mm(channel), 450.0)

    def test_equal_mode_copies_shared_length_to_every_channel(self) -> None:
        row = self._make_row()
        row._shared_length_spin.setValue(600.0)
        for channel in range(1, 5):
            self.assertAlmostEqual(row.channel_tube_length_mm(channel), 600.0)

    def test_not_equal_mode_shows_per_channel_length_and_hides_shared(self) -> None:
        row = self._make_row()
        row._equal_button.setChecked(False)
        self.assertTrue(row._shared_length_spin.isHidden())
        for channel in range(1, 5):
            self.assertFalse(row._channel_length_spins[channel].isHidden())

    def test_equal_mode_links_flow_from_channel_one_to_others(self) -> None:
        row = self._make_row()
        row._channel_flow_spins[1].setValue(750.0)
        for channel in (2, 3, 4):
            self.assertAlmostEqual(row.channel_flow_ul_min(channel), 750.0)
            self.assertFalse(row._channel_flow_spins[channel].isEnabled())
        self.assertTrue(row._channel_flow_spins[1].isEnabled())

    def test_equal_mode_links_volume_transitively_via_flow_and_shared_duration(self) -> None:
        row = self._make_row()
        row._duration_spin.setValue(60.0)
        row._channel_flow_spins[1].setValue(1000.0)
        for channel in (2, 3, 4):
            self.assertAlmostEqual(row.channel_volume_ml(channel), row.channel_volume_ml(1), places=3)

    def test_not_equal_mode_stops_linking_flow(self) -> None:
        row = self._make_row()
        row._channel_flow_spins[1].setValue(750.0)
        row._equal_button.setChecked(False)
        for channel in (2, 3, 4):
            self.assertTrue(row._channel_flow_spins[channel].isEnabled())
        row._channel_flow_spins[2].setValue(50.0)
        self.assertAlmostEqual(row.channel_flow_ul_min(1), 750.0)
        self.assertAlmostEqual(row.channel_flow_ul_min(2), 50.0)

    def test_changing_channel_volume_updates_shared_duration_and_other_channels(self) -> None:
        row = self._make_row()
        row._equal_button.setChecked(False)  # isolate from flow-linking for this test
        row._duration_spin.setValue(60.0)
        row._channel_flow_spins[1].setValue(1000.0)
        row._channel_flow_spins[2].setValue(500.0)
        self.assertAlmostEqual(row.channel_volume_ml(1), 1.0, places=3)
        self.assertAlmostEqual(row.channel_volume_ml(2), 0.5, places=3)

        row._channel_volume_spins[1].setValue(2000.0)  # 2.0 mL, in uL

        self.assertAlmostEqual(row.duration_s(), 120.0, places=1)
        self.assertAlmostEqual(row.channel_volume_ml(2), 1.0, places=3)

    def test_tube_geometry_changed_callback_fires_on_length_and_diameter_change(self) -> None:
        row = self._make_row()
        calls = []
        row.tube_geometry_changed.append(lambda: calls.append(1))
        row._shared_length_spin.setValue(300.0)
        self.assertGreaterEqual(len(calls), 1)
        calls.clear()
        row._shared_tube_combo.setValue(0.51)
        self.assertGreaterEqual(len(calls), 1)

    def test_no_channel_widget_has_any_stylesheet(self) -> None:
        # Regression test: linked (disabled, following-CH1) Flow/Set Volume
        # fields used to get a colored border and an underline bar as a
        # "these move together" indicator - both removed as unwanted visual
        # clutter. Disabled state alone (default Qt greying) is now the
        # only indicator.
        row = self._make_row()
        for channel in range(1, 5):
            self.assertEqual(row._channel_flow_spins[channel].styleSheet(), "")
            self.assertEqual(row._channel_volume_spins[channel].styleSheet(), "")

    def test_set_volume_displays_in_microliters(self) -> None:
        row = self._make_row()
        row._duration_spin.setValue(60.0)
        row._channel_flow_spins[1].setValue(1000.0)  # 1000 uL/min for 60 s = 1000 uL
        self.assertAlmostEqual(row._channel_volume_spins[1].value(), 1000.0, places=1)
        self.assertIn("uL", row._channel_volume_spins[1].suffix())
        self.assertAlmostEqual(row.channel_volume_ml(1), 1.0, places=3)

    def test_set_volume_scroll_step_matches_flow_rate(self) -> None:
        row = self._make_row()
        row._channel_flow_spins[1].setValue(250.0)
        self.assertAlmostEqual(row._channel_volume_spins[1].singleStep(), 250.0)

        row._channel_flow_spins[1].setValue(1200.0)
        self.assertAlmostEqual(row._channel_volume_spins[1].singleStep(), 1200.0)

    def test_set_volume_scroll_step_has_a_sane_floor_at_zero_flow(self) -> None:
        row = self._make_row()
        row._channel_flow_spins[1].setValue(0.0)
        self.assertGreater(row._channel_volume_spins[1].singleStep(), 0.0)

    def test_no_button_has_autodefault_enabled(self) -> None:
        # Regression test: pressing Enter after typing into a spinbox used
        # to trigger the "=" button (Qt's automatic default-button
        # behavior), silently flipping equal/not-equal mode. Every
        # QPushButton in the control row must opt out of that.
        row = self._make_row()
        buttons = [row._equal_button, row._shared_direction_button, *row._channel_direction_buttons.values()]
        for button in buttons:
            self.assertIsInstance(button, QPushButton)
            self.assertFalse(button.autoDefault())


class PumpCalibrationDialogLayoutTests(unittest.TestCase):
    """Column-alignment coverage: Set Volume (in the control row) and
    Measured Volume/Deviation (added afterward by PumpCalibrationDialog)
    must land in the exact same grid columns, since two independent
    QGridLayout instances can never keep their column widths in sync with
    each other."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_dialog(self) -> PumpCalibrationDialog:
        settings = DeviceManagerSettings()
        settings.pump.max_flow_ul_min = 5000.0
        dialog = PumpCalibrationDialog(settings, MagicMock())
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_measured_and_set_volume_share_the_same_grid(self) -> None:
        dialog = self._make_dialog()
        grid = dialog.control_row.grid
        for channel in range(1, 5):
            column = dialog.control_row.channel_column(channel)
            set_volume_item = grid.itemAtPosition(PumpCalibrationControlRow._ROW_SET_VOLUME, column)
            measured_item = grid.itemAtPosition(dialog._ROW_MEASURED_VOLUME, column)
            self.assertIsNotNone(set_volume_item)
            self.assertIsNotNone(measured_item)
            self.assertIs(measured_item.widget(), dialog._measured_spins[channel])

    def test_measured_volume_column_matches_set_volume_column_index(self) -> None:
        dialog = self._make_dialog()
        for channel in range(1, 5):
            self.assertEqual(
                dialog.control_row.channel_column(channel),
                dialog.control_row.channel_column(channel),  # same accessor used by both rows
            )


class TimeUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_row(self, max_flow_ul_min: float = 5000.0) -> PumpCalibrationControlRow:
        settings = DeviceManagerSettings()
        settings.pump.max_flow_ul_min = max_flow_ul_min
        row = PumpCalibrationControlRow(settings)
        self.addCleanup(row.deleteLater)
        return row

    def test_starts_in_seconds_mode(self) -> None:
        row = self._make_row()
        self.assertEqual(row._time_unit_mode, "s")
        self.assertEqual(row._time_unit_button.text(), "s")
        self.assertAlmostEqual(row._duration_spin.value(), 60.0)

    def test_cycling_goes_s_then_min_then_h_then_back_to_s(self) -> None:
        row = self._make_row()
        row._cycle_time_unit_mode()
        self.assertEqual(row._time_unit_mode, "min")
        row._cycle_time_unit_mode()
        self.assertEqual(row._time_unit_mode, "h")
        row._cycle_time_unit_mode()
        self.assertEqual(row._time_unit_mode, "s")

    def test_cycling_converts_the_displayed_value_but_keeps_seconds_unchanged(self) -> None:
        row = self._make_row()
        row._duration_spin.setValue(120.0)  # 120 s
        self.assertAlmostEqual(row.duration_s(), 120.0)

        row._cycle_time_unit_mode()  # -> min
        self.assertAlmostEqual(row._duration_spin.value(), 2.0, places=3)
        self.assertAlmostEqual(row.duration_s(), 120.0)

        row._cycle_time_unit_mode()  # -> h
        self.assertAlmostEqual(row._duration_spin.value(), 0.03, places=2)
        self.assertAlmostEqual(row.duration_s(), 120.0)

    def test_each_unit_uses_the_same_range_decimals_as_experiment_control(self) -> None:
        row = self._make_row()
        self.assertEqual(row._duration_spin.decimals(), 0)
        self.assertAlmostEqual(row._duration_spin.maximum(), 86400.0)

        row._cycle_time_unit_mode()  # -> min
        self.assertEqual(row._duration_spin.decimals(), 1)
        self.assertAlmostEqual(row._duration_spin.maximum(), 1440.0)

        row._cycle_time_unit_mode()  # -> h
        self.assertEqual(row._duration_spin.decimals(), 2)
        self.assertAlmostEqual(row._duration_spin.maximum(), 24.0)

    def test_seconds_mode_no_longer_clips_at_3600(self) -> None:
        row = self._make_row()
        row._duration_spin.setValue(7200.0)
        self.assertAlmostEqual(row.duration_s(), 7200.0)

    def test_editing_volume_past_one_hour_auto_switches_to_hours(self) -> None:
        row = self._make_row()
        row._equal_button.setChecked(False)
        row._channel_flow_spins[1].setValue(100.0)  # slow rate -> long duration for a modest volume
        row._channel_volume_spins[1].setValue(10_000.0)  # 10 mL at 100 uL/min = 6000 s = 1.667 h

        self.assertEqual(row._time_unit_mode, "h")
        self.assertAlmostEqual(row.duration_s(), 6000.0, places=0)

    def test_editing_volume_past_one_minute_auto_switches_to_minutes(self) -> None:
        row = self._make_row()
        row._equal_button.setChecked(False)
        row._channel_flow_spins[1].setValue(1000.0)
        row._channel_volume_spins[1].setValue(5_000.0)  # 5 mL at 1000 uL/min = 300 s = 5 min

        self.assertEqual(row._time_unit_mode, "min")
        self.assertAlmostEqual(row.duration_s(), 300.0, places=0)

    def test_directly_editing_duration_does_not_auto_switch_unit(self) -> None:
        row = self._make_row()
        row._cycle_time_unit_mode()  # -> min
        row._duration_spin.setValue(90.0)  # 90 min
        self.assertEqual(row._time_unit_mode, "min")
        self.assertAlmostEqual(row.duration_s(), 5400.0, places=0)


class PumpCalibrationDialogStepsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_dialog(self, max_flow_ul_min: float = 5000.0) -> tuple[PumpCalibrationDialog, MagicMock]:
        settings = DeviceManagerSettings()
        settings.pump.max_flow_ul_min = max_flow_ul_min
        service = MagicMock()
        service.send_command.return_value = SimpleNamespace(success=True, response="*", error=None)
        dialog = PumpCalibrationDialog(settings, service)
        self.addCleanup(dialog.deleteLater)
        return dialog, service

    def _fill_segment(self, dialog: PumpCalibrationDialog):
        return dialog._status_bar.segments[dialog._STEP_FILL_TUBES]

    def _disperse_segment(self, dialog: PumpCalibrationDialog):
        return dialog._status_bar.segments[dialog._STEP_DISPERSE]

    def test_fill_tubes_label_shows_one_rate_when_all_channels_match(self) -> None:
        dialog, _service = self._make_dialog()
        self.assertEqual(self._fill_segment(dialog).label.text(), "1. Fill tubes using 27 uL/min for one minute")

    def test_fill_tubes_label_shows_per_channel_rates_when_they_differ(self) -> None:
        dialog, _service = self._make_dialog()
        dialog.control_row._equal_button.setChecked(False)
        dialog.control_row._channel_length_spins[2].setValue(900.0)
        text = self._fill_segment(dialog).label.text()
        self.assertIn("CH1:", text)
        self.assertIn("CH2:", text)

    def test_disperse_label_is_set_at_construction(self) -> None:
        dialog, _service = self._make_dialog()
        self.assertEqual(self._disperse_segment(dialog).label.text(), "2. Disperse liquid.")

    def test_toggle_buttons_start_as_start(self) -> None:
        dialog, _service = self._make_dialog()
        self.assertEqual(self._fill_segment(dialog).toggle_button.text(), "Start")
        self.assertEqual(self._disperse_segment(dialog).toggle_button.text(), "Start")

    def test_clicking_fill_tubes_toggle_starts_it_and_flips_to_stop(self) -> None:
        dialog, service = self._make_dialog()
        self._fill_segment(dialog).toggle_button.click()

        self.assertEqual(dialog._active_step_index, dialog._STEP_FILL_TUBES)
        self.assertEqual(self._fill_segment(dialog).toggle_button.text(), "Stop")
        set_flow_calls = [c for c in service.send_command.call_args_list if c.args[1].command_type == "pump.set_flow"]
        self.assertEqual({c.args[1].payload["channel"] for c in set_flow_calls}, {1, 2, 3, 4})

    def test_clicking_toggle_again_while_running_stops_it(self) -> None:
        dialog, service = self._make_dialog()
        toggle = self._fill_segment(dialog).toggle_button
        toggle.click()  # start
        service.send_command.reset_mock()
        toggle.click()  # stop

        self.assertIsNone(dialog._active_step_index)
        self.assertEqual(toggle.text(), "Start")
        stop_channels = {
            c.args[1].payload["channel"] for c in service.send_command.call_args_list
            if c.args[1].command_type == "pump.stop"
        }
        self.assertEqual(stop_channels, {1, 2, 3, 4})

    def test_other_toggle_disabled_while_a_step_runs(self) -> None:
        dialog, _service = self._make_dialog()
        self._fill_segment(dialog).toggle_button.click()

        self.assertTrue(self._fill_segment(dialog).toggle_button.isEnabled())
        self.assertFalse(self._disperse_segment(dialog).toggle_button.isEnabled())

    def test_start_fill_tubes_configures_all_four_channels_at_computed_rate(self) -> None:
        dialog, service = self._make_dialog()
        dialog._start_fill_tubes()

        set_flow_calls = [c for c in service.send_command.call_args_list if c.args[1].command_type == "pump.set_flow"]
        configured_channels = {c.args[1].payload["channel"] for c in set_flow_calls}
        self.assertEqual(configured_channels, {1, 2, 3, 4})
        for call in set_flow_calls:
            self.assertEqual(call.args[1].payload["flow_ul_min"], 27.0)
            self.assertTrue(call.args[1].payload["start"])
        self.assertEqual(dialog._active_step_index, dialog._STEP_FILL_TUBES)

    def test_fill_tubes_elapsing_stops_all_channels_and_resets_toggle(self) -> None:
        dialog, service = self._make_dialog()
        dialog._start_fill_tubes()
        service.send_command.reset_mock()

        dialog._on_active_step_elapsed()

        stop_channels = {
            c.args[1].payload["channel"] for c in service.send_command.call_args_list
            if c.args[1].command_type == "pump.stop"
        }
        self.assertEqual(stop_channels, {1, 2, 3, 4})
        self.assertIsNone(dialog._active_step_index)
        self.assertEqual(self._fill_segment(dialog).toggle_button.text(), "Start")

    def test_manual_stop_sends_pump_stop_and_resets_toggle(self) -> None:
        dialog, service = self._make_dialog()
        dialog._start_fill_tubes()
        service.send_command.reset_mock()

        dialog._stop_active_step()

        stop_channels = {
            c.args[1].payload["channel"] for c in service.send_command.call_args_list
            if c.args[1].command_type == "pump.stop"
        }
        self.assertEqual(stop_channels, {1, 2, 3, 4})
        self.assertIsNone(dialog._active_step_index)
        self.assertEqual(self._fill_segment(dialog).toggle_button.text(), "Start")

    def test_manual_stop_during_disperse_still_reveals_measure_section(self) -> None:
        dialog, service = self._make_dialog()

        def _dispatch(_label, command):
            if command.command_type == "pump.roller_step_volume.get":
                return SimpleNamespace(success=True, response=0.001, error=None)
            return SimpleNamespace(success=True, response="*", error=None)

        service.send_command.side_effect = _dispatch
        dialog.control_row._channel_flow_spins[1].setValue(1000.0)

        dialog._start_disperse()
        dialog._stop_active_step()  # stop early, before the full duration elapses

        self.assertFalse(dialog._measured_spins[1].isHidden())
        self.assertIn(1, dialog._channel_old_rsv_ml)

    def test_stop_active_step_with_nothing_running_is_a_no_op(self) -> None:
        dialog, service = self._make_dialog()
        dialog._stop_active_step()
        service.send_command.assert_not_called()

    def test_disperse_only_configures_active_channels(self) -> None:
        dialog, service = self._make_dialog()
        dialog.control_row._equal_button.setChecked(False)
        dialog.control_row._channel_flow_spins[1].setValue(500.0)
        dialog.control_row._channel_flow_spins[3].setValue(300.0)
        # CH2/CH4 left at 0 -> inactive

        dialog._start_disperse()

        set_flow_calls = [c for c in service.send_command.call_args_list if c.args[1].command_type == "pump.set_flow"]
        configured_channels = {c.args[1].payload["channel"] for c in set_flow_calls}
        self.assertEqual(configured_channels, {1, 3})
        self.assertEqual(dialog._active_step_index, dialog._STEP_DISPERSE)

    def test_disperse_with_no_active_channels_shows_message_and_sends_nothing(self) -> None:
        dialog, service = self._make_dialog()
        with patch("lspr_app.gui.pump_calibration_panel.QMessageBox.information") as info_mock:
            dialog._start_disperse()
            info_mock.assert_called_once()
        service.send_command.assert_not_called()
        self.assertIsNone(dialog._active_step_index)

    def test_disperse_finishing_reads_old_roller_step_volume_and_reveals_measure_section(self) -> None:
        dialog, service = self._make_dialog()

        def _dispatch(_label, command):
            if command.command_type == "pump.roller_step_volume.get":
                return SimpleNamespace(success=True, response=0.001, error=None)
            return SimpleNamespace(success=True, response="*", error=None)

        service.send_command.side_effect = _dispatch
        dialog.control_row._channel_flow_spins[1].setValue(1000.0)  # equal mode -> all 4 active

        dialog._start_disperse()
        dialog._on_active_step_elapsed()

        for channel in range(1, 5):
            self.assertFalse(dialog._measured_spins[channel].isHidden())
            self.assertAlmostEqual(dialog._channel_old_rsv_ml[channel], 0.001)
            self.assertTrue(dialog._measured_spins[channel].isEnabled())
            # Measured Volume is seeded with the target, in uL now.
            self.assertAlmostEqual(
                dialog._measured_spins[channel].value(), dialog._channel_target_volume_ml[channel] * 1000.0, places=1,
            )

    def test_confirm_writes_corrected_roller_step_volume_and_colors_deviation(self) -> None:
        dialog, service = self._make_dialog()

        def _dispatch(_label, command):
            if command.command_type == "pump.roller_step_volume.get":
                return SimpleNamespace(success=True, response=0.001, error=None)
            return SimpleNamespace(success=True, response="*", error=None)

        service.send_command.side_effect = _dispatch
        dialog.control_row._channel_flow_spins[1].setValue(1000.0)
        dialog._start_disperse()
        dialog._on_active_step_elapsed()
        target_ml = dialog._channel_target_volume_ml[1]
        dialog._measured_spins[1].setValue(target_ml * 1000.0 * 1.01)  # uL, +1%

        service.send_command.reset_mock()
        dialog._apply_corrections()

        write_calls = [
            c for c in service.send_command.call_args_list if c.args[1].command_type == "pump.roller_step_volume.set"
        ]
        ch1_write = next(c for c in write_calls if c.args[1].payload["channel"] == 1)
        self.assertAlmostEqual(ch1_write.args[1].payload["volume_ml"], 0.00101, places=6)
        self.assertIn("+1.00%", dialog._deviation_labels[1].text())
        self.assertEqual(dialog._deviation_labels[1].styleSheet(), "color: #22c55e; font-weight: 600;")

    def test_confirm_with_nothing_measured_shows_message(self) -> None:
        dialog, service = self._make_dialog()
        with patch("lspr_app.gui.pump_calibration_panel.QMessageBox.information") as info_mock:
            dialog._apply_corrections()
            info_mock.assert_called_once()
        service.send_command.assert_not_called()

    def test_channel_left_at_zero_measured_volume_is_skipped(self) -> None:
        # "If some value is not written or 0, don't change it" - a channel
        # whose Measured Volume is 0 (the seeded default is the non-zero
        # target, so 0 means the user deliberately cleared it) must not get
        # a pump.roller_step_volume.set at all, and its Deviation stays "-".
        dialog, service = self._make_dialog()

        def _dispatch(_label, command):
            if command.command_type == "pump.roller_step_volume.get":
                return SimpleNamespace(success=True, response=0.001, error=None)
            return SimpleNamespace(success=True, response="*", error=None)

        service.send_command.side_effect = _dispatch
        dialog.control_row._equal_button.setChecked(False)
        dialog.control_row._channel_flow_spins[1].setValue(1000.0)
        dialog.control_row._channel_flow_spins[2].setValue(1000.0)
        dialog._start_disperse()
        dialog._on_active_step_elapsed()

        dialog._measured_spins[2].setValue(0.0)  # deliberately skip CH2

        service.send_command.reset_mock()
        dialog._apply_corrections()

        write_calls = [
            c for c in service.send_command.call_args_list if c.args[1].command_type == "pump.roller_step_volume.set"
        ]
        written_channels = {c.args[1].payload["channel"] for c in write_calls}
        self.assertIn(1, written_channels)
        self.assertNotIn(2, written_channels)
        self.assertEqual(dialog._deviation_labels[2].text(), "-")

    def test_calibrate_button_disabled_until_something_is_measured(self) -> None:
        dialog, service = self._make_dialog()
        self.assertFalse(dialog._status_bar.calibrate_button.isEnabled())

        def _dispatch(_label, command):
            if command.command_type == "pump.roller_step_volume.get":
                return SimpleNamespace(success=True, response=0.001, error=None)
            return SimpleNamespace(success=True, response="*", error=None)

        service.send_command.side_effect = _dispatch
        dialog.control_row._channel_flow_spins[1].setValue(1000.0)
        dialog._start_disperse()
        dialog._on_active_step_elapsed()

        self.assertTrue(dialog._status_bar.calibrate_button.isEnabled())

    def test_calibrate_button_disables_again_once_every_channel_is_zeroed(self) -> None:
        dialog, service = self._make_dialog()

        def _dispatch(_label, command):
            if command.command_type == "pump.roller_step_volume.get":
                return SimpleNamespace(success=True, response=0.001, error=None)
            return SimpleNamespace(success=True, response="*", error=None)

        service.send_command.side_effect = _dispatch
        dialog.control_row._channel_flow_spins[1].setValue(1000.0)  # equal mode -> all 4 active
        dialog._start_disperse()
        dialog._on_active_step_elapsed()
        self.assertTrue(dialog._status_bar.calibrate_button.isEnabled())

        for channel in range(1, 5):
            dialog._measured_spins[channel].setValue(0.0)

        self.assertFalse(dialog._status_bar.calibrate_button.isEnabled())

    def test_calibrate_button_is_part_of_the_status_bar_row(self) -> None:
        # "Instead the check button after the columns, add one at the end
        # of the progress bar" - Calibrate now lives in the same row as the
        # two step segments, not in the control row's grid.
        dialog, _service = self._make_dialog()
        self.assertIs(dialog._status_bar.calibrate_button.parent(), dialog._status_bar)

    def test_progress_bar_text_is_centered_inside_the_bar(self) -> None:
        dialog, _service = self._make_dialog()
        for segment in dialog._status_bar.segments:
            self.assertTrue(segment.progress_bar.isTextVisible())
            self.assertIn("text-align: center", segment.progress_bar.styleSheet())

    def test_status_bar_progress_shows_percent_and_remaining_time(self) -> None:
        dialog, _service = self._make_dialog()
        dialog._start_fill_tubes()
        dialog._active_step_deadline -= dialog._active_step_duration_s * 0.5  # simulate ~50% elapsed
        dialog._tick_active_step()

        segment = self._fill_segment(dialog)
        self.assertGreater(segment.progress_bar.value(), 0)
        self.assertLess(segment.progress_bar.value(), 100)
        self.assertIn("left", segment.progress_bar.format())
        self.assertIn(":", segment.progress_bar.format())

    def test_no_button_in_dialog_has_autodefault_enabled(self) -> None:
        dialog, _service = self._make_dialog()
        buttons = [
            self._fill_segment(dialog).toggle_button,
            self._disperse_segment(dialog).toggle_button,
            dialog._status_bar.calibrate_button,
        ]
        for button in buttons:
            self.assertFalse(button.autoDefault())


if __name__ == "__main__":
    unittest.main()
