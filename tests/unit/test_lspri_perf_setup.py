"""LSPRi's side of lspr_core.perf (`lspr_imaging_app/perf_setup.py`): the four remembered choices, the learned-settings file, the
status text of the Preferences dialog. Pure: a plain dict stands in for the UI-state store. Only runs on the `rewrite` branch."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_core.perf import PerfPreferences, PerfRuntime, ProfileStore, SystemProfile, get_runtime, set_runtime
    from lspr_imaging_app import perf_setup
    from lspr_imaging_app.storage import ui_state_keys as keys
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class _Store(dict):
    """The two methods of `UiStateStore` the code under test uses."""

    def set(self, key: str, value: object) -> None:
        self[key] = value


class PerfPreferencesPersistenceTests(unittest.TestCase):
    def test_an_empty_store_gives_the_defaults(self) -> None:
        self.assertEqual(perf_setup.read_perf_preferences(_Store()), PerfPreferences())

    def test_what_was_written_is_read_back(self) -> None:
        store = _Store()
        chosen = PerfPreferences(mode="manual", manual_workers=5, reserved_cores=2, low_priority=False)
        perf_setup.write_perf_preferences(store, chosen)
        self.assertEqual(perf_setup.read_perf_preferences(store), chosen)

    def test_only_the_changed_choices_are_written(self) -> None:
        store = _Store()
        self.assertEqual(perf_setup.write_perf_preferences(store, PerfPreferences()), [], "the defaults are not a change")
        self.assertEqual(store, {})
        changed = perf_setup.write_perf_preferences(store, PerfPreferences(low_priority=False, reserved_cores=3))
        self.assertEqual(sorted(changed), ["low_priority", "reserved_cores"])
        self.assertEqual(set(store), {keys.PERF_LOW_PRIORITY.key, keys.PERF_RESERVED_CORES.key})

    def test_a_hand_edited_file_with_wrong_types_falls_back_to_the_defaults(self) -> None:
        store = _Store({keys.PERF_MODE.key: 7, keys.PERF_MANUAL_WORKERS.key: "many", keys.PERF_LOW_PRIORITY.key: "yes"})
        self.assertEqual(perf_setup.read_perf_preferences(store), PerfPreferences())

    def test_an_unknown_mode_or_a_negative_number_is_forced_into_range(self) -> None:
        store = _Store({keys.PERF_MODE.key: "turbo", keys.PERF_MANUAL_WORKERS.key: -3, keys.PERF_RESERVED_CORES.key: -1})
        loaded = perf_setup.read_perf_preferences(store)
        self.assertEqual((loaded.mode, loaded.manual_workers, loaded.reserved_cores), ("standard", 0, 0))

    def test_standard_is_the_default_mode(self) -> None:
        self.assertEqual(perf_setup.read_perf_preferences(_Store()).mode, "standard")
        self.assertEqual(keys.PERF_MODE.default, "standard")

    def test_the_keys_are_hand_wired_and_unique(self) -> None:
        for key in (keys.PERF_MODE, keys.PERF_MANUAL_WORKERS, keys.PERF_RESERVED_CORES, keys.PERF_LOW_PRIORITY):
            self.assertIn(key, keys.HAND_WIRED_KEYS)
            self.assertTrue(key.key.startswith("perf/"))


class RuntimeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        previous = set_runtime(None)
        self.addCleanup(set_runtime, previous)

    def test_the_profile_lives_next_to_the_app_settings(self) -> None:
        self.assertEqual(perf_setup.perf_profile_path().name, "perf_profile.json")
        self.assertEqual(perf_setup.perf_profile_path().parent.name, "lspr-suite")

    def test_install_runtime_takes_the_saved_choices_and_a_profile_store(self) -> None:
        path = Path(self._tmp.name) / "perf_profile.json"
        store = _Store({keys.PERF_MODE.key: "manual", keys.PERF_MANUAL_WORKERS.key: 3})
        with mock.patch.object(perf_setup, "perf_profile_path", return_value=path):
            runtime = perf_setup.install_runtime(store)
        self.assertIs(get_runtime(), runtime)
        self.assertEqual(runtime.preferences.mode, "manual")
        self.assertIsInstance(runtime.store, ProfileStore)
        self.assertEqual(runtime.store.path, path)

    def test_a_window_start_applies_the_saved_choices_to_the_runtime_in_use(self) -> None:
        set_runtime(PerfRuntime())
        perf_setup.apply_saved_preferences(_Store({keys.PERF_RESERVED_CORES.key: 2}))
        self.assertEqual(get_runtime().preferences.reserved_cores, 2)

    def test_the_status_text_names_the_machine_and_the_counts_tuning_saved(self) -> None:
        machine = SystemProfile(8, 4, 16 * 1024**3, 9 * 1024**3, False, "test")
        store = ProfileStore(Path(self._tmp.name) / "p.json")
        set_runtime(PerfRuntime(store=store, probe=lambda: machine))
        self.assertEqual(perf_setup.status_text(), "This computer: 4 cores / 8 threads, 16.0 GB memory (9.0 GB free), on mains.")
        self.assertEqual(perf_setup.saved_count(), 0)
        store.record(machine.fingerprint(), perf_setup.EXPORT_TASK_KEY, "local", workers=6, rate=37.6)
        store.record(machine.fingerprint(), "mask.scan.screen", "", workers=5, rate=80.0)
        store.record("another-machine", perf_setup.EXPORT_TASK_KEY, "local", workers=2, rate=1.0)
        text = perf_setup.status_text()
        self.assertIn("export to a local disk: 6 workers (38/s)", text)
        self.assertIn("scan: 5 workers (80/s)", text)
        self.assertNotIn("2 workers", text, "another machine's counts are not shown")
        self.assertEqual(perf_setup.saved_count(), 2)


if __name__ == "__main__":
    unittest.main()
