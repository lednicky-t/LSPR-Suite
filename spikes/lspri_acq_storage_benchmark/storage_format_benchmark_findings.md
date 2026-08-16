# Storage format benchmark findings — TIFF-per-frame vs. live zarr v3 shard writes

Run via `benchmark_storage.py` in this folder. See that file's module docstring for
the full caveat on synthetic frame content (real spatial structure/noise, not random
bytes, but not a real captured frame either — no camera was attached).

Two questions asked: (1) how fast is each option compared to TIFF, and (2) how much
space is saved — plus whether pre-packing to the sensor's real 10-bit range helps.

## Setup

- Frame sizes match Phase 0's two tested configurations: full resolution
  (3840×2160) and 2×2 binning (1920×1080).
- Both a 10-bit-range (0–1023) and 12-bit-range (0–4095) content variant tested,
  both stored in a `uint16` container (no numpy dtype natively supports 10/12-bit).
- zarr writes use the exact hand-rolled shard format
  `apps/LSPRi/eva/src/lspr_imaging_app/io/_zarr_export_worker.py`'s `write_shard()`
  uses (bypassing zarr's own async chunk-write API — the thing that made eva's batch
  exporter fast), but **single-threaded**, not spread across a `ProcessPoolExecutor`
  — matching how a live `SaveWriterThread` actually runs (one dedicated thread, per
  the architecture plan's section 8, not a process pool per frame).
- Shard grouping is **per-spectral-cube** (one shard file per completed cube,
  covering all its wavelengths), not eva's default per-image sharding — chosen
  because it produces exactly one new file per cube as a live experiment runs,
  matching the "avoid too many files" goal better than one-file-per-wavelength would.

## Headline numbers (2 cubes × 4 wavelengths, this machine's disk)

| Format | Full res ms/frame | Binned ms/frame | Compression ratio |
|---|---|---|---|
| TIFF, uncompressed | ~8 | ~4-5 | 1.0x |
| TIFF, zlib | ~110-150 | ~33-40 | ~2.0-2.4x |
| zarr shard, uncompressed | ~32-35 | ~8 | ~1.0x |
| zarr shard, lz4+bitshuffle (eva's own setting) | ~123-125 | ~31-33 | ~1.1-1.3x |
| zarr shard, zstd-5+bitshuffle | ~490-620 | ~127-172 | ~2.0-2.5x |
| packed-10bit + lz4+bitshuffle | ~130-132 | ~35-40 | 1.6x (fixed) |

## Findings

1. **eva's own lz4+bitshuffle setting is borderline at full resolution, on a single
   save thread.** Per-cube write time (4 wavelengths × ~124ms) ≈ 495ms, against a
   sweep's own per-cube time (4 steps × ~110ms settle+capture, per Phase 0's
   settle-time numbers) ≈ 440ms — the save thread would slowly fall behind over a
   long experiment. This matters specifically because eva's proven 119 MB/s number
   was measured with a `ProcessPoolExecutor` spread across every CPU core for a
   batch export, not a single dedicated live-save thread — the two aren't the same
   claim, and re-measuring for the single-thread case was the point of this
   benchmark.
2. **At 2×2 binning, every compressed option comfortably keeps up** (lz4: ~124-132ms
   per cube vs. the same ~440ms budget). This is now a second, independent reason
   (beyond Phase 0's capture-throughput margin) favoring 2×2 binning as the default.
3. **Compression ratio is genuinely different between codecs for this content** —
   lz4+bitshuffle (fast, eva's default) only reaches ~1.1-1.3x, well below TIFF's
   plain zlib (~2.0-2.4x) and zarr's own zstd-5 option (~2.0-2.5x, but 4-5x slower
   than lz4 and not viable live at any tested resolution). Don't assume "zarr ⇒ more
   compact than TIFF" without checking which codec — it depends entirely on the
   codec/level choice, not the container format itself.
4. **Bit-packing to 10 bits/pixel before compression doesn't meaningfully help
   throughput**, despite the smaller payload (1.6x ratio, matching the raw
   16-to-10-bit ratio almost exactly) — the packing step's own CPU cost eats the
   savings, and it isn't standard zarr-chunked layout (would need real extra
   engineering to make it zarr-v3-compliant). Not worth pursuing for v1; store as
   plain `uint16` and let the chosen codec handle it.

## Implication for the real writer

Two live-safe options, not mutually exclusive:
- Write **uncompressed** live (comfortably fast at both resolutions: ~8-35ms/frame,
  no risk of falling behind), optionally followed by a **background/end-of-experiment
  recompression pass** — reusing eva's *already-validated* multiprocess batch shard
  writer (same `write_shard()` code, just called after the fact instead of during
  acquisition) rather than fighting for compression time during live acquisition at
  all.
- Or write compressed live with **lz4+bitshuffle at 2×2 binning only** (comfortable
  margin), and prefer the uncompressed-then-recompress path specifically at full
  resolution.

Not decided here — this file reports the measurements; the actual choice is the
maintainer's call.
