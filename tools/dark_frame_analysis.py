"""Dark-frame (WL0) significance analysis for LSPRimaging datasets.

Answers the question behind the exclusion-filter feature: do the WL0 "dark"
images (LED/LCTF off) carry information that matters for absorbance/extinction
processing, or are they negligible camera noise that a future exclusion
filter can safely skip?

Reuses the app's own dataset loader (lspr_imaging_app.io.dataset) so this
reads exactly what the GUI would read -- same TIFF-vs-OME-Zarr autodetection,
same float32 cast.

Five checks, in order:
  1. Magnitude    -- dark level vs bright level, globally and inside an ROI.
  2. Fireflies    -- hot/outlier pixels in dark frames: how many, and are
                     they at fixed locations (real hot pixels) or scattered
                     (per-frame noise)?
  3. Stability    -- does the dark frame drift over the run (thermal drift),
                     and how correlated is it frame-to-frame?
  4. Leakage      -- do the dark frame's hot pixels also show up as outliers
                     in the bright frames (i.e. does subtracting dark actually
                     clean the bright images)?
  5. Impact       -- how much does subtracting the per-cube dark frame change
                     the ROI-mean intensity used in absorbance_from_means()
                     (processing/analysis.py), relative to frame-to-frame
                     repeatability noise?

Usage:
    python tools/dark_frame_analysis.py <dataset_folder> [options]

    --roi x y w h     ROI box in pixels (default: central 40% of the image)
    --cube-stride N   Use every Nth spectral cube for the per-cube/bright
                       comparisons (default: auto, ~40 samples across the run)
    --out PATH         Output PNG path (default: tools/dark_frame_analysis.png)

Example:
    python tools/dark_frame_analysis.py "C:\\Users\\Admin\\Desktop\\Data_PyTest\\04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2\\images"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (
    str(REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"),
    str(REPO_ROOT / "packages" / "lspr_core" / "src"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

from lspr_imaging_app.io.dataset import load_dataset, dataset_load_plane  # noqa: E402


def default_roi(height: int, width: int) -> tuple[int, int, int, int]:
    rw, rh = int(width * 0.4), int(height * 0.4)
    return (width - rw) // 2, (height - rh) // 2, rw, rh


def crop(arr: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = roi
    return arr[y:y + h, x:x + w]


def hot_pixel_mask(frame: np.ndarray, z_thresh: float = 8.0) -> np.ndarray:
    """Outlier mask using mean + z_thresh*std.

    Not MAD: these dark frames are zero-inflated (a majority of pixels read
    exactly 0), which makes the median -- and therefore the MAD -- exactly 0,
    so a MAD-based z-score degenerates and flags almost every nonzero pixel
    as "hot". Plain std stays well-behaved on this distribution.
    """
    mean = frame.mean()
    std = frame.std()
    if std <= 1e-9:
        return np.zeros_like(frame, dtype=bool)
    return frame > (mean + z_thresh * std)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset_folder", type=Path)
    parser.add_argument("--roi", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--cube-stride", type=int, default=0)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "tools" / "dark_frame_analysis.png")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset_folder)
    cubes = dataset.spectral_cube_indices
    wavelengths = dataset.wavelengths_nm
    dark_wl = min(wavelengths)
    bright_wls = [wl for wl in wavelengths if wl != dark_wl]
    print(f"Dataset: {len(cubes)} spectral cubes x {len(wavelengths)} wavelengths "
          f"({dark_wl:g} nm = dark, {min(bright_wls):g}-{max(bright_wls):g} nm = bright), "
          f"format={dataset.format_label}")

    first = dataset_load_plane(dataset, cubes[0], dark_wl)
    height, width = first.shape[:2]
    roi = tuple(args.roi) if args.roi else default_roi(height, width)
    print(f"Image size: {width}x{height} px | ROI for impact test: x={roi[0]} y={roi[1]} w={roi[2]} h={roi[3]}")

    stride = args.cube_stride or max(1, len(cubes) // 40)
    sample_cubes = cubes[::stride]
    print(f"Sampling {len(sample_cubes)} of {len(cubes)} cubes (stride={stride}) for the bright/impact comparisons.\n")

    # ---- Pass 1: full dark-frame series (cheap: 1 plane/cube) -------------
    sum_dark = np.zeros((height, width), dtype=np.float64)
    sumsq_dark = np.zeros((height, width), dtype=np.float64)
    hot_count = np.zeros((height, width), dtype=np.int32)
    dark_mean_per_cube = []
    dark_p99_per_cube = []
    dark_max_per_cube = []
    n_hot_per_cube = []
    kept_frames: dict[int, np.ndarray] = {}
    keep_at = {cubes[0], cubes[len(cubes) // 2], cubes[-1]}

    for ci in cubes:
        frame = dataset_load_plane(dataset, ci, dark_wl)
        sum_dark += frame
        sumsq_dark += frame.astype(np.float64) ** 2
        mask = hot_pixel_mask(frame)
        hot_count += mask
        dark_mean_per_cube.append(float(frame.mean()))
        dark_p99_per_cube.append(float(np.percentile(frame, 99)))
        dark_max_per_cube.append(float(frame.max()))
        n_hot_per_cube.append(int(mask.sum()))
        if ci in keep_at:
            kept_frames[ci] = frame

    n = len(cubes)
    dark_pixel_mean = sum_dark / n
    dark_pixel_std = np.sqrt(np.maximum(sumsq_dark / n - dark_pixel_mean ** 2, 0.0))

    # ---- Pass 2: sampled bright frames + ROI impact -----------------------
    bright_means_global = []
    roi_raw_means: dict[float, list[float]] = {wl: [] for wl in bright_wls}
    roi_darksub_means: dict[float, list[float]] = {wl: [] for wl in bright_wls}
    leak_z_scores = []  # bright-frame z-score at dark-frame hot-pixel locations

    persistent_hot_mask = hot_count >= max(2, int(0.5 * n))  # hot in >=50% of dark frames
    print(f"Firefly/hot pixels (MAD z>8 in the per-cube dark frame): "
          f"{int(persistent_hot_mask.sum())} pixels are hot in >=50% of all {n} dark frames "
          f"(out of a mean {np.mean(n_hot_per_cube):.1f} hot pixels flagged per single dark frame).")

    for ci in sample_cubes:
        dark_frame = dataset_load_plane(dataset, ci, dark_wl)
        dark_roi_mean = float(crop(dark_frame, roi).mean())
        cube_bright_means = []
        for wl in bright_wls:
            bright_frame = dataset_load_plane(dataset, ci, wl)
            cube_bright_means.append(float(bright_frame.mean()))
            roi_bright = crop(bright_frame, roi)
            roi_raw_means[wl].append(float(roi_bright.mean()))
            roi_darksub_means[wl].append(float(roi_bright.mean()) - dark_roi_mean)
            if persistent_hot_mask.any():
                local_median = np.median(bright_frame)
                local_mad = np.median(np.abs(bright_frame - local_median)) + 1e-9
                z_at_hot = 0.6745 * (bright_frame[persistent_hot_mask] - local_median) / local_mad
                leak_z_scores.append(float(np.median(z_at_hot)))
        bright_means_global.append(float(np.mean(cube_bright_means)))

    # ---- Report: magnitude --------------------------------------------------
    dark_global_mean = float(np.mean(dark_mean_per_cube))
    bright_global_mean = float(np.mean(bright_means_global))
    print("\n[1] Magnitude")
    print(f"    Dark mean (whole frame, avg over {n} cubes):   {dark_global_mean:.3f} counts")
    print(f"    Bright mean (whole frame, avg over samples):   {bright_global_mean:.3f} counts")
    print(f"    Dark / bright ratio:                           {100 * dark_global_mean / bright_global_mean:.2f} %")

    # ---- Report: stability ---------------------------------------------------
    drift_slope = float(np.polyfit(np.arange(n), dark_mean_per_cube, 1)[0])
    corr_first_last = float(np.corrcoef(kept_frames[cubes[0]].ravel(), kept_frames[cubes[-1]].ravel())[0, 1])
    corr_first_mid = float(np.corrcoef(kept_frames[cubes[0]].ravel(), kept_frames[cubes[len(cubes) // 2]].ravel())[0, 1])
    mean_temporal_std = float(dark_pixel_std.mean())
    print("\n[2] Temporal stability")
    print(f"    Dark-frame mean drift across the run:          {drift_slope:+.5f} counts/cube "
          f"({drift_slope * n:+.2f} counts over the full run)")
    print(f"    Pixel-wise Pearson r, dark frame cube[0] vs cube[mid]: {corr_first_mid:.3f}")
    print(f"    Pixel-wise Pearson r, dark frame cube[0] vs cube[-1]:  {corr_first_last:.3f}")
    print(f"    Mean per-pixel std across all {n} dark frames:  {mean_temporal_std:.3f} counts "
          f"(vs mean level {dark_global_mean:.3f})")
    if corr_first_mid > 0.5 and corr_first_last > 0.5 and mean_temporal_std < dark_global_mean:
        print("    -> High frame-to-frame correlation and low temporal variance: the dark pattern is "
              "a stable fixed structure (sensor bias / hot pixels at fixed locations). A single shared "
              "dark reference (e.g. averaged over the run) would work as well as a per-cube one.")
    elif corr_first_mid < 0.3 and corr_first_last < 0.3:
        print("    -> Low frame-to-frame correlation: the dark frame's spatial pattern is NOT "
              "reproducible between cubes -- it behaves like independent per-frame noise rather than "
              "a fixed structure. A single shared/averaged dark reference would suppress this noise "
              "better than reusing one single per-cube dark frame verbatim.")
    else:
        print("    -> Mixed signal: partially reproducible spatial structure plus per-frame noise. "
              "Worth checking the saved figure's std-map panel to see how much of the frame is "
              "structured vs noisy.")

    # ---- Report: leakage into bright frames -----------------------------------
    print("\n[3] Hot-pixel leakage into bright frames")
    if leak_z_scores:
        print(f"    Median MAD z-score of bright frames AT the dark frame's persistent hot-pixel "
              f"locations: {np.median(leak_z_scores):.2f} (z>8 would mean those pixels are still "
              f"outliers under illumination, i.e. dark subtraction is doing real cleanup there).")
    else:
        print("    No persistent hot pixels found -- nothing to check.")

    # ---- Report: impact on ROI absorbance-style ratio --------------------------
    print("\n[4] Impact on ROI mean (the quantity absorbance_from_means() consumes)")
    pct_shifts = []
    for wl in bright_wls:
        raw = np.array(roi_raw_means[wl])
        sub = np.array(roi_darksub_means[wl])
        pct_shift = float(np.mean(np.abs(raw - sub) / raw) * 100)
        pct_shifts.append(pct_shift)
    repeatability_noise_pct = float(
        np.mean([np.std(np.diff(roi_raw_means[wl])) / np.mean(roi_raw_means[wl]) for wl in bright_wls]) * 100
    )
    print(f"    Mean |raw - dark-subtracted| ROI-mean shift across wavelengths: {np.mean(pct_shifts):.3f} % "
          f"(range {min(pct_shifts):.3f}-{max(pct_shifts):.3f} %)")
    print(f"    Cube-to-cube ROI-mean repeatability noise (proxy, consecutive-cube std / mean): "
          f"{repeatability_noise_pct:.3f} %")
    if np.mean(pct_shifts) > repeatability_noise_pct:
        print("    -> Dark-subtraction shift is LARGER than frame-to-frame noise: subtracting the "
              "dark frame changes the ROI mean by more than measurement noise, so it likely matters "
              "for absorbance/extinction accuracy.")
    else:
        print("    -> Dark-subtraction shift is SMALLER than frame-to-frame noise: for this ROI, "
              "skipping dark subtraction is within the run's own repeatability noise floor.")

    # ---- Figure -----------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    axes[0, 0].imshow(kept_frames[cubes[0]], cmap="inferno", vmax=np.percentile(kept_frames[cubes[0]], 99.5))
    axes[0, 0].set_title(f"Dark frame, cube {cubes[0]}")
    axes[0, 1].imshow(dark_pixel_std, cmap="inferno")
    axes[0, 1].set_title("Per-pixel std of dark frame across all cubes\n(structure = fixed pattern, not noise)")
    axes[0, 2].imshow(persistent_hot_mask, cmap="gray")
    axes[0, 2].set_title(f"Persistent hot pixels (n={int(persistent_hot_mask.sum())})")

    axes[1, 0].plot(cubes, dark_mean_per_cube, label="mean")
    axes[1, 0].plot(cubes, dark_p99_per_cube, label="p99")
    axes[1, 0].plot(cubes, dark_max_per_cube, label="max", alpha=0.5)
    axes[1, 0].set_xlabel("spectral cube index")
    axes[1, 0].set_ylabel("counts")
    axes[1, 0].set_title("Dark frame level over the run")
    axes[1, 0].legend(fontsize=8)

    axes[1, 1].plot(bright_wls, pct_shifts, marker="o")
    axes[1, 1].axhline(repeatability_noise_pct, color="gray", linestyle="--", label="repeatability noise floor")
    axes[1, 1].set_xlabel("wavelength (nm)")
    axes[1, 1].set_ylabel("% ROI-mean shift from dark subtraction")
    axes[1, 1].set_title("Impact of dark subtraction vs wavelength")
    axes[1, 1].legend(fontsize=8)

    mid_wl = bright_wls[len(bright_wls) // 2]
    axes[1, 2].plot(sample_cubes, roi_raw_means[mid_wl], label="raw ROI mean")
    axes[1, 2].plot(sample_cubes, roi_darksub_means[mid_wl], label="dark-subtracted ROI mean")
    axes[1, 2].set_xlabel("spectral cube index")
    axes[1, 2].set_title(f"ROI mean at {mid_wl:g} nm, raw vs dark-subtracted")
    axes[1, 2].legend(fontsize=8)

    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=130)
    print(f"\nFigure saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
