"""Scale bar sizing rules (pure functions, no Qt app needed)."""

from __future__ import annotations

import pytest

from lspr_imaging_app.panels.image.scale_bar_overlay import TARGET_FRACTION, choose_bar, nice_length


@pytest.mark.parametrize(
    "target, expected",
    [(1.0, 1.0), (1.4, 1.0), (1.6, 2.0), (3.4, 2.0), (3.6, 5.0), (7.4, 5.0), (7.6, 10.0), (0.03, 0.02), (730.0, 500.0)],
)
def test_nice_length_is_1_2_5(target, expected):
    assert nice_length(target) == pytest.approx(expected)


@pytest.mark.parametrize("width", [3.0, 50.0, 640.0, 2048.0, 1e5])
def test_pixel_bar_stays_within_view_fraction(width):
    length, label = choose_bar(width, None)
    assert label.endswith(" px")
    assert 0.13 * width <= length <= 0.36 * width or length == 1.0  # 1 px floor at extreme zoom-in


@pytest.mark.parametrize("width, um_per_px", [(2048, 0.1), (2048, 0.5), (100, 0.065), (20, 0.065), (4000, 3.0), (5000, 0.0004)])
def test_um_bar_label_is_readable_and_consistent(width, um_per_px):
    length_px, label = choose_bar(width, um_per_px)
    number, unit = label.split(" ")
    assert unit in ("nm", "µm", "mm")
    assert 1.0 <= float(number) < 1000.0
    factor = {"nm": 1e-3, "µm": 1.0, "mm": 1e3}[unit]
    # the drawn length really is the labelled physical length
    assert length_px * um_per_px == pytest.approx(float(number) * factor)
    assert 0.13 * width <= length_px <= 0.36 * width


def test_target_fraction_is_what_the_docstring_says():
    assert TARGET_FRACTION == pytest.approx(0.2)
