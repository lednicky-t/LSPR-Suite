"""Synthetic wavelength sweeps with a *known* chromatic transform, for the
LSPRi automatic chromatic-correction tests.

Dark disks on a bright background (the physical assumption: always darker, at
every wavelength), one frame per wavelength. At wavelength w the whole
pattern is magnified about the image centre by `1 + SCALE_PER_NM * (w - ref)`
and shifted by `SHIFT_PER_NM * (w - ref)`, and the disk depth changes with
wavelength (contrast changes, sign never does). So the true landmark
position at any wavelength is known exactly - real ground truth, unlike a
self-consistency check on measured data.
"""

from __future__ import annotations

import numpy as np

SHAPE = (300, 450)  # (height, width)
REFERENCE_NM = 550.0
WAVELENGTHS = tuple(float(w) for w in range(490, 611, 10))  # 13 wavelengths
SCALE_PER_NM = 0.00012  # +-0.7 % across the sweep -> ~1.6 px at the image edge
SHIFT_PER_NM = (0.004, -0.003)
DISK_RADIUS = 13.0
PITCH_X, PITCH_Y = 66.0, 60.0
FIRST_X, FIRST_Y = 60.0, 60.0


def disk_centres() -> np.ndarray:
    """True disk centres at the reference wavelength, (N, 2) x then y."""
    xs = np.arange(FIRST_X, SHAPE[1] - 40, PITCH_X)
    ys = np.arange(FIRST_Y, SHAPE[0] - 40, PITCH_Y)
    return np.array([(x, y) for y in ys for x in xs], dtype=np.float64)


def transform(points: np.ndarray, wavelength_nm: float) -> np.ndarray:
    """Where reference-wavelength `points` are at `wavelength_nm`."""
    dw = wavelength_nm - REFERENCE_NM
    centre = np.array([SHAPE[1] / 2.0, SHAPE[0] / 2.0])
    scale = 1.0 + SCALE_PER_NM * dw
    return (points - centre) * scale + centre + np.array(SHIFT_PER_NM) * dw


def make_frame(wavelength_nm: float, seed: int = 0, noise: float = 0.004) -> np.ndarray:
    """One float32 frame (arbitrary counts, ~40000 background)."""
    rng = np.random.default_rng(seed + int(wavelength_nm))
    height, width = SHAPE
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    depth = 0.45 + 0.15 * np.sin((wavelength_nm - REFERENCE_NM) / 40.0)  # contrast varies, never flips
    scale = 1.0 + SCALE_PER_NM * (wavelength_nm - REFERENCE_NM)
    darkness = np.zeros(SHAPE, dtype=np.float32)
    for cx, cy in transform(disk_centres(), wavelength_nm):
        radius = DISK_RADIUS * scale
        y0, y1 = int(cy - radius - 3), int(cy + radius + 4)
        x0, x1 = int(cx - radius - 3), int(cx + radius + 4)
        distance = np.hypot(xx[y0:y1, x0:x1] - cx, yy[y0:y1, x0:x1] - cy)
        darkness[y0:y1, x0:x1] = np.maximum(darkness[y0:y1, x0:x1], np.clip(0.5 + (radius - distance), 0.0, 1.0))
    background = 40000.0 * (1.0 + 0.05 * (xx / width - 0.5))  # gentle illumination gradient
    frame = background * (1.0 - depth * darkness)
    frame *= 1.0 + noise * rng.standard_normal(SHAPE).astype(np.float32)
    return frame.astype(np.float32)
