from __future__ import annotations

import unittest
from pathlib import Path

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from suite_launcher.targets import TARGETS, _candidate_paths


class LauncherRegistryTests(unittest.TestCase):
    def test_candidate_paths_filters_none_values(self) -> None:
        path = Path("sample")
        self.assertEqual(_candidate_paths(None, path, None), (path,))

    def test_target_registry_contains_expected_apps(self) -> None:
        self.assertEqual(
            {target.key for target in TARGETS},
            {"slspr_acq", "slspr_eva", "lspri_acq", "lspri_eva"},
        )
        self.assertTrue(all(target.title for target in TARGETS))
        self.assertFalse(next(target for target in TARGETS if target.key == "lspri_acq").enabled)
