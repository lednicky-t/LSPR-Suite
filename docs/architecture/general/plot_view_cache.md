# Plot View Cache

The acquisition GUI now treats display rendering as a separate concern from raw scientific data retention.

## Purpose

The app must keep the full measurement history available for reproducibility, absolute-view plots, and later freeze/zoom analysis. That raw history should not be capped just to keep the GUI fast.

At the same time, the GUI should not rebuild large plot inputs from scratch on every refresh.

The `PlotViewCache` layer exists to bridge that gap:

- raw spectra and derived metric points remain the source of truth
- display data is cached separately
- metric traces and sensorgram heatmaps use the same cache model
- cache entries are bounded and invalidated by source revision, so the cache does not grow without limit
- display resolutions are quantized into level-of-detail buckets so nearby viewport sizes reuse the same view cache
- absolute metric display uses a min/max envelope sampler so peaks and dips are preserved while the visible point count stays screen-sized
- the absolute metric cache keeps explicit source length and stride metadata so tail updates can reuse earlier display work

## What It Caches

- active trace-series extraction from the retained metric histories
- metric plot view slices for the current viewport
- absolute metric view slices using a min/max envelope display cache
- heatmap row matrices reconstructed from the retained sensorgram history
- heatmap view slices for the current viewport

## What It Does Not Do

- it does not truncate raw scientific history
- it does not change the saved measurement file format
- it does not replace freeze/zoom analysis with a lossy live-only shortcut

## Why This Helps

The current slowdown work showed that the GUI was paying repeated costs to:

- convert retained history into arrays
- downsample the same data again and again
- rebuild heatmap matrices on refresh

`PlotViewCache` centralizes those view transforms so they can be reused, measured, and replaced later with a richer multiresolution cache if needed.

## Next Step

If runtime drift still grows after this first cache layer, the next logical evolution is a fuller multiresolution plot pyramid:

- metric plots pick the closest detail level for the current pixel width
- heatmaps pick the closest row density for the current pixel height
- freeze mode can request a higher-resolution slice from file for zooming
