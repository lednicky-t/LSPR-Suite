"""The Cube and wavelength sliders leave out the frames the slideshow skips (`panels/image/slider_skip.py`, 2026-11-21).

The rule alone (`snap_index`, pure) and the two callbacks that say which slider positions are allowed, with small fakes for the
dataset, the selection and the slideshow. The sliders themselves and the whole panel: `tests/integration/test_lspri_slider_skip_panel.py`.

Rewrite-only; skipped when the `apps/LSPRi/eva` submodule is not on its `rewrite` branch.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.slider_skip import cube_index_filter, snap_index, wavelength_index_filter
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def snap(requested: int, current: int, skipped: set[int], *, absolute: bool, first: int = 0, last: int = 9) -> int:
    return snap_index(requested, current, first, last, lambda i: i not in skipped, absolute=absolute)


class StepTests(unittest.TestCase):
    """The wheel, the arrow keys, Page Up / Down: a step goes on in its direction to the first frame that is not skipped."""

    def test_a_frame_that_is_not_skipped_is_taken_as_asked(self) -> None:
        self.assertEqual(snap(4, 3, {7}, absolute=False), 4)

    def test_a_step_jumps_over_a_block_in_either_direction(self) -> None:
        self.assertEqual(snap(3, 2, {3, 4, 5}, absolute=False), 6)
        self.assertEqual(snap(5, 6, {3, 4, 5}, absolute=False), 2)

    def test_a_step_that_lands_past_the_block_is_not_moved_again(self) -> None:
        self.assertEqual(snap(6, 2, {3, 4, 5}, absolute=False), 6)  # a page step

    def test_a_multi_notch_wheel_that_lands_in_a_block_goes_on_to_its_end(self) -> None:
        self.assertEqual(snap(5, 2, {5}, absolute=False), 6)

    def test_nothing_allowed_further_on_refuses_the_step(self) -> None:
        self.assertEqual(snap(8, 7, {8, 9}, absolute=False), 7)  # the last allowed frame
        self.assertEqual(snap(1, 2, {0, 1}, absolute=False), 2)

    def test_a_step_from_a_skipped_frame_with_nothing_ahead_stays_put(self) -> None:
        self.assertEqual(snap(9, 8, {8, 9}, absolute=False), 8)  # standing on a skipped frame: never moved backwards by a "next"

    def test_home_and_end_land_on_the_nearest_allowed_frame_between_the_end_and_the_handle(self) -> None:
        self.assertEqual(snap(0, 5, {0, 1}, absolute=False), 2)
        self.assertEqual(snap(9, 5, {8, 9}, absolute=False), 7)

    def test_home_and_end_never_land_behind_the_handle(self) -> None:
        self.assertEqual(snap(0, 1, {0, 1}, absolute=False), 1)  # Home from a skipped frame 1: 2 lies behind the handle's start, stay

    def test_the_request_is_clamped_to_the_slider(self) -> None:
        self.assertEqual(snap(12, 8, set(), absolute=False), 9)
        self.assertEqual(snap(-3, 1, {0}, absolute=False), 1)


class PositionTests(unittest.TestCase):
    """A press or a drag on the track: the nearest frame that is not skipped, looking only at where the pointer is."""

    def test_the_nearest_allowed_frame_wins(self) -> None:
        self.assertEqual(snap(3, 0, {3, 4, 5}, absolute=True), 2)
        self.assertEqual(snap(5, 0, {3, 4, 5}, absolute=True), 6)

    def test_a_tie_goes_the_way_the_handle_is_travelling(self) -> None:
        self.assertEqual(snap(4, 1, {3, 4, 5}, absolute=True), 6)  # coming from the left: on to the right
        self.assertEqual(snap(4, 8, {3, 4, 5}, absolute=True), 2)

    def test_at_the_ends_only_one_side_exists(self) -> None:
        self.assertEqual(snap(9, 0, {8, 9}, absolute=True), 7)
        self.assertEqual(snap(0, 9, {0, 1}, absolute=True), 2)

    def test_the_answer_does_not_depend_on_where_the_handle_was(self) -> None:
        # Dragging across a block must not jitter: the same pointer position always gives the same frame (except a tie).
        for requested in (3, 5):
            answers = {snap(requested, current, {3, 4, 5}, absolute=True) for current in (0, 1, 2, 6, 7, 9)}
            self.assertEqual(len(answers), 1, (requested, answers))

    def test_a_drag_across_a_block_crosses_it_once_at_the_middle(self) -> None:
        path = [snap(position, 0, {3, 4, 5}, absolute=True) for position in range(0, 10)]
        self.assertEqual(path, [0, 1, 2, 2, 6, 6, 6, 7, 8, 9])  # 3 -> 2, 5 -> 6, and the tie at 4 goes on (the handle started on the left)

    def test_a_press_on_an_allowed_frame_is_exact(self) -> None:
        self.assertEqual(snap(7, 0, {3, 4, 5}, absolute=True), 7)


class EverythingSkippedTests(unittest.TestCase):
    def test_when_every_frame_is_skipped_nothing_is_filtered(self) -> None:
        everything = set(range(10))
        self.assertEqual(snap(4, 3, everything, absolute=False), 4)
        self.assertEqual(snap(4, 3, everything, absolute=True), 4)

    def test_cheap_in_the_ordinary_case(self) -> None:
        calls: list[int] = []
        snap_index(5, 4, 0, 9, lambda i: calls.append(i) or True, absolute=False)
        self.assertEqual(calls, [5])  # one question, no search


@dataclass
class _Settings:
    skip_ignored: bool = True
    skip_dark: bool = False


class _Slideshow:
    """`is_skipped(cube, wavelength)` from a set of skipped frames; asked frames are recorded."""

    def __init__(self, skipped: set[tuple[int, float]], settings: _Settings | None = None) -> None:
        self.skipped, self._settings, self.asked = skipped, settings or _Settings(), []

    def settings(self) -> _Settings:
        return self._settings

    def is_skipped(self, cube: int, wavelength: float) -> bool:
        self.asked.append((cube, wavelength))
        return (cube, wavelength) in self.skipped


class _Dataset:
    def __init__(self, cubes: int = 6, wavelengths: tuple[float, ...] = (0.0, 500.0, 550.0, 600.0)) -> None:
        self._cubes, self._wavelengths = list(range(cubes)), wavelengths

    def spectral_cubes(self) -> list[int]:
        return self._cubes

    def wavelengths_for_cube(self, cube: int) -> tuple[float, ...]:
        return self._wavelengths


class _Selection:
    def __init__(self, cube: int = 0, wavelength: float = 500.0) -> None:
        self.cube, self.wavelength = cube, wavelength

    def current_cube(self) -> int:
        return self.cube

    def current_wavelength(self) -> float:
        return self.wavelength


class FilterTests(unittest.TestCase):
    def test_the_cube_filter_asks_about_the_frame_at_the_wavelength_shown(self) -> None:
        slideshow = _Slideshow({(2, 500.0), (3, 500.0), (4, 550.0)})  # cube 4 is only skipped at another wavelength
        allowed = cube_index_filter(_Dataset(), _Selection(1, 500.0), slideshow)
        self.assertEqual(allowed(2, 1, 0, 5, False), 4)  # steps over cubes 2 and 3
        self.assertEqual(allowed(3, 1, 0, 5, True), 4)  # a press on 3: nearest allowed is 4 (the other side is 1, distance 2)

    def test_the_wavelength_filter_asks_about_the_frames_of_the_cube_shown(self) -> None:
        slideshow = _Slideshow({(3, 0.0), (3, 550.0), (2, 500.0)})
        allowed = wavelength_index_filter(_Dataset(), _Selection(3, 600.0), slideshow)
        self.assertEqual(allowed(2, 3, 0, 3, False), 1)  # a step down from 600 meets 550 (skipped in cube 3) and goes on to 500
        self.assertEqual(allowed(1, 3, 0, 3, True), 1)  # 500 is fine in cube 3 (only cube 2's 500 is skipped)
        self.assertEqual(allowed(0, 3, 0, 3, True), 1)  # the dark frame (index 0) is skipped in cube 3: the nearest allowed is 500

    def test_nothing_is_filtered_while_both_skip_settings_are_off(self) -> None:
        slideshow = _Slideshow({(2, 500.0), (3, 500.0)}, _Settings(skip_ignored=False, skip_dark=False))
        allowed = cube_index_filter(_Dataset(), _Selection(1, 500.0), slideshow)
        self.assertEqual(allowed(2, 1, 0, 5, False), 2)
        self.assertEqual(slideshow.asked, [])  # not even asked

    def test_one_skip_setting_on_is_enough_to_filter(self) -> None:
        slideshow = _Slideshow({(2, 500.0)}, _Settings(skip_ignored=False, skip_dark=True))
        allowed = cube_index_filter(_Dataset(), _Selection(1, 500.0), slideshow)
        self.assertEqual(allowed(2, 1, 0, 5, False), 3)

    def test_standing_on_a_wholly_skipped_axis_filters_nothing(self) -> None:
        dark_everywhere = {(cube, 0.0) for cube in range(6)}
        allowed = cube_index_filter(_Dataset(), _Selection(2, 0.0), _Slideshow(dark_everywhere, _Settings(True, True)))
        self.assertEqual(allowed(3, 2, 0, 5, False), 3)  # the dark frame is shown on purpose: the cube slider still moves
        wholly_ignored_cube = {(4, wavelength) for wavelength in (0.0, 500.0, 550.0, 600.0)}
        allowed = wavelength_index_filter(_Dataset(), _Selection(4, 500.0), _Slideshow(wholly_ignored_cube))
        self.assertEqual(allowed(2, 1, 0, 3, False), 2)


if __name__ == "__main__":
    unittest.main()
