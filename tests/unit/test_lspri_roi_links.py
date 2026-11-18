"""Sample / reference roles and links between ROIs (phase 1.1 of docs/roi_shapes_and_linking_plan_2026-11-18.md).

Covers the pure rules (`domain/roi_links.py`), the `RoiToolbox` commands (one undo step each, refusals change nothing), links
following renumbering and deletion, and the session file round trip (schema 1.11, defaults not written).
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_links import (
        SOURCE_BUILTIN,
        SOURCE_IS_REFERENCE,
        SOURCE_LINKED,
        SOURCE_NONE,
        cleaned_links,
        reference_source,
    )
    from lspr_imaging_app.domain.roi_model import AreaRoi
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.storage.session import _decode_area_rois, _encode_area_roi
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _roi(roi_id: int, **kw) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=10.0 * roi_id, center_y=5.0, sample_diameter_px=6.0, **kw)


class ReferenceSourceTest(unittest.TestCase):
    def test_each_source(self) -> None:
        self.assertEqual(reference_source(_roi(1)), SOURCE_BUILTIN)
        self.assertEqual(reference_source(_roi(1, reference_geometry_type="none")), SOURCE_NONE)
        self.assertEqual(reference_source(_roi(1, reference_roi_ids=(2,))), SOURCE_LINKED)
        self.assertEqual(reference_source(_roi(1, role="reference")), SOURCE_IS_REFERENCE)

    def test_links_win_over_the_ring(self) -> None:
        self.assertEqual(reference_source(_roi(1, reference_roi_ids=(2,), reference_outer_diameter_px=30.0)), SOURCE_LINKED)


class CleanedLinksTest(unittest.TestCase):
    def test_a_consistent_set_is_untouched(self) -> None:
        rois = [_roi(1, reference_roi_ids=(2,)), _roi(2, role="reference")]
        self.assertEqual(cleaned_links(rois), [])
        self.assertEqual(rois[0].reference_roi_ids, (2,))

    def test_bad_links_are_dropped_and_reported(self) -> None:
        rois = [
            _roi(1, reference_roi_ids=(3, 2, 2, 9)),  # 3 is a sample, 9 does not exist
            _roi(2, role="reference", reference_roi_ids=(1,)),  # a reference with links
            _roi(3, role="weird"),
        ]
        problems = cleaned_links(rois)
        self.assertEqual(len(problems), 3)
        self.assertEqual(rois[0].reference_roi_ids, (2,))
        self.assertEqual(rois[1].reference_roi_ids, ())
        self.assertEqual(rois[2].role, "sample")


class ToolboxLinkCommandsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        for x in (10.0, 20.0, 30.0, 40.0):
            self.toolbox.add_roi(x, 5.0)
        self.link_events = []
        self.geometry_events = []
        self.toolbox.links_changed.connect(self.link_events.append)
        self.toolbox.geometry_changed.connect(self.geometry_events.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _make_refs(self, *ids: int) -> None:
        self.toolbox.set_roi_role(ids, "reference")

    def test_role_change_is_one_undo_step_and_computational(self) -> None:
        self._make_refs(3, 4)
        self.assertEqual(self.toolbox.reference_source(3), SOURCE_IS_REFERENCE)
        self.assertEqual(self.geometry_events[-1].reason, "role_changed")
        self.assertEqual(self.geometry_events[-1].roi_ids, (3, 4))
        undo_manager.undo()
        self.assertEqual(self.toolbox.reference_source(3), SOURCE_BUILTIN)

    def test_sample_ids_drops_reference_rois_keeps_the_order_and_unknown_ids(self) -> None:
        """The one analysis-unit rule (a reference ROI is not measured on its own): used by Spectra, Sensorgram, the step table and the Signal map."""
        self._make_refs(2, 4)
        self.assertEqual(self.toolbox.sample_ids((4, 3, 2, 1)), (3, 1))  # the order given
        self.assertEqual(self.toolbox.sample_ids((2, 4)), ())
        self.assertEqual(sorted(self.toolbox.sample_ids({1, 3})), [1, 3])  # any iterable, a set too
        self.assertEqual(self.toolbox.sample_ids((1, 99)), (1, 99))  # an id the toolbox does not know is left to the caller's own "not in the scan" path
        self.assertEqual(self.toolbox.sample_ids(()), ())

    def test_link_one_reference_to_many_samples(self) -> None:
        self._make_refs(4)
        self.toolbox.link_references((1, 2), (4,))
        self.assertEqual(self.toolbox.references_of(1), (4,))
        self.assertEqual(self.toolbox.samples_of(4), (1, 2))
        self.assertEqual(self.link_events[-1].sample_ids, (1, 2))
        undo_manager.undo()
        self.assertEqual(self.toolbox.samples_of(4), ())
        undo_manager.redo()
        self.assertEqual(self.toolbox.samples_of(4), (1, 2))

    def test_links_add_up(self) -> None:
        self._make_refs(3, 4)
        self.toolbox.link_references((1,), (4,))
        self.toolbox.link_references((1,), (3,))
        self.assertEqual(self.toolbox.references_of(1), (3, 4))

    def test_refusals_change_nothing(self) -> None:
        self._make_refs(4)
        for samples, refs in (((1,), (2,)), ((4,), (4,)), ((1,), ()), ((1,), (99,))):
            with self.assertRaises(ValueError):
                self.toolbox.link_references(samples, refs)
        self.assertEqual(self.toolbox.references_of(1), ())
        self.assertEqual(undo_manager.undo_label,"Make ROI reference")  # no step was pushed by a refusal

    def test_unlink_some_or_all(self) -> None:
        self._make_refs(3, 4)
        self.toolbox.link_references((1, 2), (3, 4))
        self.toolbox.unlink_references((1,), (3,))
        self.assertEqual(self.toolbox.references_of(1), (4,))
        self.toolbox.unlink_references((2,))
        self.assertEqual(self.toolbox.references_of(2), ())
        undo_manager.undo()
        self.assertEqual(self.toolbox.references_of(2), (3, 4))

    def test_making_a_reference_a_sample_unlinks_it_everywhere(self) -> None:
        self._make_refs(4)
        self.toolbox.link_references((1, 2), (4,))
        self.toolbox.set_roi_role((4,), "sample")
        self.assertEqual(self.toolbox.references_of(1), ())
        self.assertEqual(self.toolbox.reference_source(1), SOURCE_BUILTIN)
        undo_manager.undo()
        self.assertEqual(self.toolbox.samples_of(4), (1, 2))

    def test_making_a_linked_sample_a_reference_drops_its_links(self) -> None:
        self._make_refs(4)
        self.toolbox.link_references((1,), (4,))
        self.toolbox.set_roi_role((1,), "reference")
        self.assertEqual(self.toolbox.references_of(1), ())
        undo_manager.undo()
        self.assertEqual(self.toolbox.references_of(1), (4,))

    def test_links_follow_a_reorder(self) -> None:
        self._make_refs(4)
        self.toolbox.link_references((1,), (4,))
        self.toolbox.reorder_rois([4, 1, 2, 3])  # the reference becomes ROI 1, the sample ROI 2
        self.assertEqual(self.toolbox.roi_by_id(1).role, "reference")
        self.assertEqual(self.toolbox.references_of(2), (1,))
        undo_manager.undo()
        self.assertEqual(self.toolbox.references_of(1), (4,))

    def test_deleting_a_reference_drops_its_links_and_undo_restores_them(self) -> None:
        self._make_refs(2)
        self.toolbox.link_references((1, 3), (2,))
        self.toolbox.delete_rois((2,))
        self.assertEqual(self.toolbox.references_of(1), ())
        self.assertEqual(self.toolbox.references_of(2), ())  # old ROI 3
        self.assertEqual(self.link_events[-1].sample_ids, (1, 2))
        undo_manager.undo()
        self.assertEqual(self.toolbox.samples_of(2), (1, 3))

    def test_deleting_a_sample_renumbers_the_links_of_the_others(self) -> None:
        self._make_refs(4)
        self.toolbox.link_references((2,), (4,))
        self.toolbox.delete_rois((1,))
        self.assertEqual(self.toolbox.references_of(1), (3,))
        undo_manager.undo()
        self.assertEqual(self.toolbox.references_of(2), (4,))

    def test_restore_repairs_a_bad_file(self) -> None:
        rois = (_roi(1, reference_roi_ids=(2, 5)), _roi(2, role="reference"))
        with self.assertLogs("lspr_imaging_app.roi.toolbox", level="WARNING"):
            self.toolbox.restore_state(self.toolbox.detection_settings(), rois, (), ())
        self.assertEqual(self.toolbox.references_of(1), (2,))


class SessionRoundTripTest(unittest.TestCase):
    def test_defaults_are_not_written(self) -> None:
        payload = _encode_area_roi(_roi(1))
        self.assertNotIn("role", payload)
        self.assertNotIn("reference_roi_ids", payload)

    def test_role_and_links_round_trip(self) -> None:
        sample, reference = _roi(1, reference_roi_ids=(2,)), _roi(2, role="reference")
        decoded = _decode_area_rois([_encode_area_roi(sample), _encode_area_roi(reference)])
        self.assertEqual(decoded[0].reference_roi_ids, (2,))
        self.assertEqual(decoded[0].role, "sample")
        self.assertEqual(decoded[1].role, "reference")

    def test_an_older_entry_loads_as_a_sample_without_links(self) -> None:
        entry = _encode_area_roi(_roi(1))
        decoded = _decode_area_rois([entry])[0]
        self.assertEqual((decoded.role, decoded.reference_roi_ids), ("sample", ()))


if __name__ == "__main__":
    unittest.main()
