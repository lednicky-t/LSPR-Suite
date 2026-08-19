from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.overlay_manager import OverlayManager


def _make_window(*, reference_points_visible: bool, all_visible: bool) -> SimpleNamespace:
    window = SimpleNamespace(
        _showing_background_profile_main=False,
        _reference_points_visible=reference_points_visible,
        _chromatic_reference_points_all_visible=all_visible,
        _landmark_overlay_items={},
        _chromatic_all_landmark_overlay_items={},
        _current_image_landmarks=lambda: [],
        _all_overlay_calls=0,
        _clear_all_overlay_calls=0,
    )

    def _update_chromatic_all_landmark_overlays() -> None:
        window._all_overlay_calls += 1

    def _clear_chromatic_all_landmark_overlays() -> None:
        window._clear_all_overlay_calls += 1

    window._update_chromatic_all_landmark_overlays = _update_chromatic_all_landmark_overlays
    window._clear_chromatic_all_landmark_overlays = _clear_chromatic_all_landmark_overlays
    return window


class TestLandmarkOverlayVisibilityLinking(unittest.TestCase):
    def test_master_toggle_off_hides_all_points_mode_too(self) -> None:
        # Regression: the master "show reference points" toggle (the one
        # under the image) must gate the Chromatic panel's "show all across
        # wavelengths" mode. It used to be checked *after* the all-mode
        # check, so turning all-mode on while the master toggle was off would
        # still draw landmarks -- the two toggles didn't actually agree.
        window = _make_window(reference_points_visible=False, all_visible=True)
        OverlayManager(window)._update_landmark_overlays()
        self.assertEqual(window._all_overlay_calls, 0)

    def test_master_toggle_on_with_all_mode_shows_all_points(self) -> None:
        window = _make_window(reference_points_visible=True, all_visible=True)
        OverlayManager(window)._update_landmark_overlays()
        self.assertEqual(window._all_overlay_calls, 1)

    def test_master_toggle_on_without_all_mode_uses_current_image_only(self) -> None:
        window = _make_window(reference_points_visible=True, all_visible=False)
        OverlayManager(window)._update_landmark_overlays()
        self.assertEqual(window._all_overlay_calls, 0)
        self.assertEqual(window._clear_all_overlay_calls, 1)


if __name__ == "__main__":
    unittest.main()
