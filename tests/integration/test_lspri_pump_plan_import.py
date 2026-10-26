"""LSPRi: experiment steps in the acquisition metadata - import of the pump-plan CSV (alone or with the measuring
times), discovery next to a dataset, the saved sidecar, and the format-version guard.

Synthetic data in the real formats: 3 cubes x 2 wavelengths, an idle stretch before and after two steps, the second
step starting in the middle of cube 2 (as in a real run, where the pump-plan note changes mid-cube)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_core import ImagingAcquisitionMetadata, ImagingCommentEvent, classify_cubes
    from lspr_imaging_app.dataset.io import load_acquisition_metadata
    from lspr_imaging_app.format_versions import ACQUISITION_METADATA_VERSION
    from lspr_imaging_app.io.legacy_metadata import import_legacy_imaging_metadata
    from lspr_imaging_app.io.metadata_import import (
        CLASSIFICATION_LEGACY_CSV,
        CLASSIFICATION_PUMP_PLAN_CSV,
        classify_metadata_file,
        import_metadata_files,
    )
    from lspr_imaging_app.io.pump_plan import attach_pump_plan, find_pump_plan_csv
    from lspr_imaging_app.storage.workspace import (
        acquisition_metadata_from_payload,
        build_acquisition_metadata_payload,
        load_acquisition_metadata_sidecar,
        save_acquisition_metadata_sidecar,
    )
except ImportError as exc:  # pragma: no cover - rewrite code absent
    skip_if_lspri_rewrite_absent(exc)
    raise

PLAN_HEADER = "FR1 [ml/min];Direction1;FR2 [ml/min];Direction2;FR3 [ml/min];Direction3;FR4 [ml/min];Direction4;Time;Valves;Notes"
PLAN_CSV = "\r\n".join(
    [
        PLAN_HEADER,
        "4.00E-02;aclckw;2.00E-02;aclckw;;;;;00:00:01;V1cV2cV3cV4c;A_--_ch1F_ch2P",
        ";;4.00E-02;aclckw;2.00E-02;aclckw;;;00:00:01;V1oV2cV3cV4c;B_--_ch2F_ch3P",
    ]
)
MEASURING_TIMES_CSV = (
    "Image;Measuring time [ms];Measuring time [s];Note pump plan\n"
    "imLCTFatWL470Frame0;100;0;Pump is not running\n"
    "imLCTFatWL480Frame0;400;0;Pump is not running\n"
    "imLCTFatWL470Frame1;1000;1;A_--_ch1F_ch2P\n"
    "imLCTFatWL480Frame1;1300;1;A_--_ch1F_ch2P\n"
    "imLCTFatWL470Frame2;2000;2;A_--_ch1F_ch2P\n"
    "imLCTFatWL480Frame2;2300;2;B_--_ch2F_ch3P\n"
    "imLCTFatWL470Frame3;3000;3;B_--_ch2F_ch3P\n"
    "imLCTFatWL480Frame3;3300;3;Pump is not running\n"
)
META_DATA_TXT = """User: testuser
Date and time: 12.09.2025 12:59:11
Camera
BinningH=4
Measurement
FilterSetTime=100
Wavelength and exposure time camera
defined wl:real wl:exposure time
470:470:210
480:480:220
eof
"""
RUN_NAME = "03_my_run"


def _make_run_folder(root: Path, *, with_plan: bool = True, with_times: bool = True) -> Path:
    folder = root / RUN_NAME
    folder.mkdir()
    if with_times:
        (folder / "measureing_times.csv").write_text(MEASURING_TIMES_CSV, encoding="utf-8")
        (folder / "metaData.txt").write_text(META_DATA_TXT, encoding="utf-8")  # the legacy search needs both files
    if with_plan:
        (folder / f"{RUN_NAME}.csv").write_bytes(PLAN_CSV.encode("utf-8"))
    return folder


def _times(metadata: ImagingAcquisitionMetadata) -> dict[int, list[int]]:
    by_cube: dict[int, list[int]] = {}
    for timing in metadata.image_timings:
        by_cube.setdefault(timing.spectral_cube_index, []).append(timing.acquired_at_unix_ms)
    return by_cube


class ImportMetadataFilesTests(unittest.TestCase):
    def test_pump_plan_csv_is_recognised_by_content_and_not_mistaken_for_the_measuring_times(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            self.assertEqual(classify_metadata_file(folder / f"{RUN_NAME}.csv"), CLASSIFICATION_PUMP_PLAN_CSV)
            self.assertEqual(classify_metadata_file(folder / "measureing_times.csv"), CLASSIFICATION_LEGACY_CSV)

    def test_measuring_times_and_plan_together_give_steps_with_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            result = import_metadata_files([folder / "measureing_times.csv", folder / f"{RUN_NAME}.csv"])
            plan = result.metadata.plan
            assert plan is not None
            self.assertEqual(plan.source_file, f"{RUN_NAME}.csv")
            self.assertEqual([(s.step, s.label) for s in plan.steps], [(1, "A_--_ch1F_ch2P"), (2, "B_--_ch2F_ch3P")])
            self.assertEqual((plan.steps[0].start_unix_ms, plan.steps[0].end_unix_ms), (1000, 2300))
            self.assertEqual(plan.steps[1].devices["valve"], "Open")
            self.assertTrue(any("device settings from" in note for note in result.notes))
            self.assertEqual(plan.warnings, [])

    def test_measuring_times_alone_still_give_steps_from_the_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp), with_plan=False)
            result = import_metadata_files([folder / "measureing_times.csv"])
            plan = result.metadata.plan
            assert plan is not None
            self.assertEqual(len(plan.steps), 2)
            self.assertIsNone(plan.source_file)
            self.assertEqual(plan.steps[0].devices, {})

    def test_plan_alone_needs_loaded_metadata_to_be_placed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp), with_times=False)
            with self.assertRaises(ValueError) as caught:
                import_metadata_files([folder / f"{RUN_NAME}.csv"])
            self.assertIn("measuring times", str(caught.exception))

    def test_plan_alone_is_attached_to_the_loaded_metadata_and_changes_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            base = import_legacy_imaging_metadata(folder / "measureing_times.csv", None)
            result = import_metadata_files([folder / f"{RUN_NAME}.csv"], base_metadata=base)
            self.assertEqual(result.metadata.comment_events, base.comment_events)
            self.assertEqual(result.metadata.image_timings, base.image_timings)
            assert result.metadata.plan is not None
            self.assertEqual(result.metadata.plan.source_file, f"{RUN_NAME}.csv")

    def test_unreadable_plan_file_stops_the_import_with_its_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            (folder / f"{RUN_NAME}.csv").write_bytes((PLAN_HEADER + "\r\n4.00E-02;aclckw;;;;;;;;V1cV2cV3cV4c;A\r\n").encode("utf-8"))
            with self.assertRaises(ValueError) as caught:
                import_metadata_files([folder / "measureing_times.csv", folder / f"{RUN_NAME}.csv"])
            self.assertIn("line 2", str(caught.exception))

    def test_cubes_across_the_step_change_are_marked_mixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            metadata = import_metadata_files([folder / "measureing_times.csv", folder / f"{RUN_NAME}.csv"]).metadata
            result = classify_cubes(metadata.plan, _times(metadata))
            # cube 0 lies wholly in the idle stretch, cube 1 wholly in step 1, cubes 2 and 3 each contain a boundary
            self.assertEqual({cube: (a.step, a.mixed) for cube, a in result.items()}, {0: (None, False), 1: (1, False), 2: (None, True), 3: (None, True)})
            self.assertEqual(result[2].counts, ((1, 1), (2, 1)))
            self.assertEqual(result[3].counts, ((2, 1), (None, 1)))


class FindPumpPlanCsvTests(unittest.TestCase):
    def test_finds_the_plan_named_like_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            self.assertEqual(find_pump_plan_csv(folder), folder / f"{RUN_NAME}.csv")

    def test_finds_it_a_few_folders_above_the_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            images = folder / "zarr" / "images.ome.zarr"
            images.mkdir(parents=True)
            self.assertEqual(find_pump_plan_csv(images), folder / f"{RUN_NAME}.csv")

    def test_a_single_plan_file_with_another_name_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp), with_plan=False)
            (folder / "plan_v2.csv").write_bytes(PLAN_CSV.encode("utf-8"))
            self.assertEqual(find_pump_plan_csv(folder), folder / "plan_v2.csv")

    def test_several_plan_files_and_none_named_like_the_folder_picks_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp), with_plan=False)
            (folder / "plan_a.csv").write_bytes(PLAN_CSV.encode("utf-8"))
            (folder / "plan_b.csv").write_bytes(PLAN_CSV.encode("utf-8"))
            with self.assertLogs("lspr_imaging_app.io.pump_plan", level="WARNING"):
                self.assertIsNone(find_pump_plan_csv(folder))

    def test_no_plan_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_pump_plan_csv(_make_run_folder(Path(tmp), with_plan=False)))


class LoadAcquisitionMetadataTests(unittest.TestCase):
    def test_load_joins_the_plan_file_found_next_to_the_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            metadata = load_acquisition_metadata(folder)
            assert metadata is not None and metadata.plan is not None
            self.assertEqual(metadata.plan.source_file, f"{RUN_NAME}.csv")
            self.assertEqual(len(metadata.plan.steps), 2)

    def test_load_without_a_plan_file_builds_the_steps_from_the_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metadata = load_acquisition_metadata(_make_run_folder(Path(tmp), with_plan=False))
            assert metadata is not None and metadata.plan is not None
            self.assertIsNone(metadata.plan.source_file)
            self.assertEqual(len(metadata.plan.steps), 2)

    def test_a_broken_plan_file_does_not_stop_the_metadata_from_loading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            (folder / f"{RUN_NAME}.csv").write_bytes((PLAN_HEADER + "\r\n4.00E-02;aclckw;;;;;;;soon;V1cV2cV3cV4c;A\r\n").encode("utf-8"))
            with self.assertLogs("lspr_imaging_app.workflow", level="WARNING"):
                metadata = load_acquisition_metadata(folder)
            assert metadata is not None and metadata.plan is not None
            self.assertIsNone(metadata.plan.source_file)  # steps from the notes, no settings
            self.assertEqual(len(metadata.plan.steps), 2)

    def test_a_sidecar_saved_before_plans_existed_gets_the_plan_file_joined_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            old = import_legacy_imaging_metadata(folder / "measureing_times.csv", None)
            payload = build_acquisition_metadata_payload(old)
            payload["schema_version"] = 1
            del payload["metadata"]["plan"]
            sidecar = folder / "analysis" / "acquisition_metadata.json"
            sidecar.parent.mkdir()
            sidecar.write_text(json.dumps(payload), encoding="utf-8")
            metadata = load_acquisition_metadata(folder)
            assert metadata is not None and metadata.plan is not None
            self.assertEqual(metadata.plan.source_file, f"{RUN_NAME}.csv")

    def test_a_saved_plan_with_settings_is_kept_and_the_plan_file_is_not_read_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            first = load_acquisition_metadata(folder)
            assert first is not None
            sidecar = folder / "analysis" / "acquisition_metadata.json"
            sidecar.parent.mkdir()
            save_acquisition_metadata_sidecar(sidecar, first)
            (folder / f"{RUN_NAME}.csv").write_text("not a plan any more", encoding="utf-8")
            again = load_acquisition_metadata(folder)
            self.assertEqual(again.plan, first.plan)


class SavedFormatTests(unittest.TestCase):
    def test_plan_round_trips_through_the_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            metadata = import_metadata_files([folder / "measureing_times.csv", folder / f"{RUN_NAME}.csv"]).metadata
            sidecar = Path(tmp) / "acquisition_metadata.json"
            save_acquisition_metadata_sidecar(sidecar, metadata)
            self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8"))["schema_version"], ACQUISITION_METADATA_VERSION)
            self.assertEqual(load_acquisition_metadata_sidecar(sidecar).plan, metadata.plan)

    def test_version_one_payload_without_a_plan_still_loads(self) -> None:
        payload = {
            "schema_name": "lspri_acquisition_metadata",
            "schema_version": 1,
            "metadata": {"source_format": "legacy_measuring_times_csv", "comment_events": [{"acquired_at_unix_ms": 5, "comment": "A"}]},
        }
        metadata = acquisition_metadata_from_payload(payload)
        self.assertIsNone(metadata.plan)
        self.assertEqual(metadata.comment_events, [ImagingCommentEvent(acquired_at_unix_ms=5, comment="A")])

    def test_a_file_from_a_newer_version_is_refused_not_guessed(self) -> None:
        payload = {"schema_name": "lspri_acquisition_metadata", "schema_version": ACQUISITION_METADATA_VERSION + 1, "metadata": {"source_format": "x"}}
        with self.assertRaises(ValueError) as caught:
            acquisition_metadata_from_payload(payload)
        self.assertIn("newer version", str(caught.exception))

    def test_attach_keeps_an_existing_plan_when_no_plan_file_is_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_run_folder(Path(tmp))
            metadata = import_metadata_files([folder / "measureing_times.csv", folder / f"{RUN_NAME}.csv"]).metadata
            again, notes = attach_pump_plan(metadata)
            self.assertIs(again, metadata)
            self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
