"""Synthetic images of dark spots on a lattice, for the array finder's tests (a known truth to compare against)."""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def synth(
    rows: int = 6, cols: int = 8, pitch: tuple[float, float] = (44.0, 44.0), diameter: float = 20.0, tilt_deg: float = 0.0, shape: tuple[int, int] = (360, 480),
    origin: tuple[float, float] = (70.0, 60.0), missing: tuple[tuple[int, int], ...] = (), dirt: tuple[tuple[float, float, float], ...] = (), noise: float = 0.01,
    row_shift: float = 0.0, size_gradient: float = 0.0, seed: int = 3,
) -> tuple[np.ndarray, dict[tuple[int, int], tuple[float, float]]]:
    """``(image, {(row, col): (x, y)})``. Dark disks (contrast 0.25) on a bright, gently sloping background, blurred, with noise; ``dirt`` = round smudges (x, y, d)
    that are not part of the array; ``row_shift``: every second row moves along x by this fraction of the pitch (0.5 with a pitch y of 0.866 x = triangular)."""
    rng = np.random.default_rng(seed)
    height, width = shape
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    image = np.full(shape, 1.0) + 0.15 * (xx / width)
    t = np.radians(tilt_deg)
    c, s = np.cos(t), np.sin(t)
    a1 = np.array([pitch[0] * c, pitch[0] * s])
    a2 = np.array([-pitch[1] * s, pitch[1] * c])
    centres: dict[tuple[int, int], tuple[float, float]] = {}
    for j in range(rows):
        for i in range(cols):
            if (j, i) in missing:
                continue
            cx, cy = np.array(origin) + (i + (row_shift if j % 2 else 0.0)) * a1 + j * a2
            centres[(j, i)] = (float(cx), float(cy))
            d = diameter * (1.0 + size_gradient * (i / max(cols - 1, 1) - 0.5))
            image -= 0.25 / (1.0 + np.exp((np.hypot(xx - cx, yy - cy) - d / 2.0) / 1.0))
    for cx, cy, d in dirt:
        image -= 0.2 / (1.0 + np.exp((np.hypot(xx - cx, yy - cy) - d / 2.0) / 1.5))
    image = ndimage.gaussian_filter(image, 1.2) + rng.normal(0.0, noise, shape)
    return image.astype(np.float32), centres
