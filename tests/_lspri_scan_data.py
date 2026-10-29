"""A tiny synthetic LSPRi dataset for scan-level tests: real TIFF files, six round ROIs, a gradient plus seeded noise (shared by the ROI position tuning tests).

The image rises 7 counts per pixel in x and 3 in y, so shifting a ROI by a pixel changes its sample mean in a known direction; each frame also carries the
cube number and the wavelength, and a seeded noise of 20 counts. Frame 0.0 nm is the dark frame (not spectral).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.scan_jobs import ScanSource, build_scan_plan, snapshot_source
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

H, W = 64, 96
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def make_rois(dx: float = 0.0, dy: float = 0.0, moved: tuple[int, ...] = ()) -> list[AreaRoi]:
    """Six round ROIs 24 px apart (sample r 4 px, ring r 6-9 px); the ones whose index is in ``moved`` are shifted by (dx, dy)."""
    out = []
    for k in range(6):
        x, y = 16.0 + 24.0 * (k % 3), 16.0 + 22.0 * (k // 3)
        if k in moved:
            x, y = x + dx, y + dy
        out.append(AreaRoi(k + 1, x, y, 8.0, reference_inner_diameter_px=12.0, reference_outer_diameter_px=18.0))
    return out


class SyntheticDataset:
    def __init__(self, n_cubes: int = 4, wavelengths: tuple[float, ...] = (0.0, 500.0, 550.0)) -> None:
        self.n_cubes = n_cubes
        self.wavelengths = wavelengths
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.paths: dict[tuple[int, float], str] = {}
        rng = np.random.default_rng(5)
        y, x = np.mgrid[0:H, 0:W]
        for cube in range(n_cubes):
            for wl in wavelengths:
                frame = (2000.0 + 7.0 * x + 3.0 * y + 30.0 * cube + wl + rng.normal(0, 20.0, (H, W))).astype(np.float32)
                path = Path(self._tmp.name) / f"c{cube}_w{int(wl)}.tif"
                tifffile.imwrite(path, frame)
                self.paths[(cube, wl)] = str(path)

    def source(self, rois: list[AreaRoi]) -> ScanSource:
        return ScanSource(
            cube_indices=lambda: tuple(range(self.n_cubes)), wavelengths_for_cube=lambda c: self.wavelengths, is_spectral=lambda c, w: w != 0.0,
            record_path=lambda c, w: self.paths[(c, w)], rois=lambda: rois, rois_at=lambda c: rois, has_timeline=lambda: False,
            chromatic_affine=lambda c, w: IDENTITY, chromatic_affine_between=lambda a, b: IDENTITY, resolve_mask=lambda c, w: None,
            geometry=lambda: GeometrySettings(), background=lambda: BackgroundSettings(), detection=lambda: AreaRoiDetectionSettings(),
            default_reference_diameters=lambda: (12.0, 18.0),
        )

    def plan(self, rois: list[AreaRoi], cubes=None):
        return build_scan_plan(snapshot_source(self.source(rois)), cubes=cubes)

    def close(self) -> None:
        self._tmp.cleanup()
