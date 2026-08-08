from __future__ import annotations

import unittest

from tests._paths import ensure_repo_paths


ensure_repo_paths()

# Tests the real owner (lspr_acq_shell) directly, not the lspr_app shim -
# matches the convention established across every Phase 1 extraction (see
# docs/architecture/general/lspri_acq_build_log.md).
from lspr_acq_shell.experiment_control_runtime import (
    experiment_runtime_label,
    experiment_runtime_payload_state,
    experiment_runtime_state_name,
    experiment_runtime_tooltip,
)


class ExperimentControlRuntimeTests(unittest.TestCase):
    def test_runtime_state_names_are_canonical(self) -> None:
        self.assertEqual(experiment_runtime_state_name(running=True, holding=False, paused=False), "running")
        self.assertEqual(experiment_runtime_state_name(running=False, holding=True, paused=False), "hold")
        self.assertEqual(experiment_runtime_state_name(running=False, holding=False, paused=True), "paused")
        self.assertEqual(experiment_runtime_state_name(running=False, holding=False, paused=False), "stopped")

    def test_runtime_payload_states_are_canonical(self) -> None:
        self.assertEqual(experiment_runtime_payload_state(running=True, holding=False, paused=False), "RUN")
        self.assertEqual(experiment_runtime_payload_state(running=False, holding=True, paused=False), "HOLD")
        self.assertEqual(experiment_runtime_payload_state(running=False, holding=False, paused=True), "PAUSE")
        self.assertEqual(experiment_runtime_payload_state(running=False, holding=False, paused=False), "STOP")

    def test_runtime_label_appends_recording_suffix(self) -> None:
        self.assertEqual(
            experiment_runtime_label(running=False, holding=False, paused=False, recording=True),
            "Experiment: stopped & recording",
        )
        self.assertEqual(
            experiment_runtime_label(running=True, holding=False, paused=False, recording=True),
            "Experiment: running & recording",
        )

    def test_runtime_tooltip_mentions_recording(self) -> None:
        tooltip = experiment_runtime_tooltip(
            running=False,
            holding=False,
            paused=False,
            recording=True,
            source_name="Spectrometer",
        )
        self.assertIn("Recording is active.", tooltip)
        self.assertIn("spectrometer", tooltip.lower())


if __name__ == "__main__":
    unittest.main()
