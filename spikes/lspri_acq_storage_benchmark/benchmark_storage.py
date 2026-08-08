"""Live-write storage format benchmark: TIFF-per-frame vs. a hand-rolled
zarr v3 shard writer (the same approach LSPRimaging Evaluation's OME-Zarr
exporter uses - see apps/LSPRi/eva/src/lspr_imaging_app/io/_zarr_export_worker.py
- adapted here for INCREMENTAL/streaming writes from in-memory frames,
since eva's own writer is a batch exporter that requires the full dataset
size known upfront and reads source images from files, not from a live
camera).

Why this exists: the architecture plan's acquisition pipeline (section 8)
needs a real answer for write throughput and compressed size before
choosing a storage format, and eva's own zarr benchmark (119 MB/s shard
writes vs. 21 MB/s through zarr's async API, see dataset.py's
export_ome_zarr_dataset docstring) was measured converting already-saved
TIFF files, not a live camera stream - not directly applicable without
re-measuring for the write-as-you-go case.

Caveat, stated once here rather than repeated at every finding: no physical
camera was attached when this was run (see the architecture plan's Phase 2
build log). Frame content below is synthetic - a smooth Gaussian-spot
pattern plus Poisson-like shot noise, standing in for real sensor data -
compressibility depends on real spatial structure/noise characteristics
that synthetic data can only approximate. Byte-throughput numbers (how fast
this disk can write N MB via each path) ARE directly measured and real;
compression-ratio numbers should be treated as directionally informative,
not exact, until re-run against real captured frames.

Run: python spikes/lspri_acq_storage_benchmark/benchmark_storage.py
"""

from __future__ import annotations

import shutil
import struct
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile
from numcodecs import Blosc

OUT_DIR = Path(__file__).resolve().parent / "_bench_scratch"

# Matches Phase 0's two tested configurations (see
# docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md
# section 3 results) - full resolution and 2x2 binning on the
# a2A3840-45umBAS.
CONFIGS = [
    ("full_res_3840x2160", 2160, 3840),
    ("binned_1920x1080", 1080, 1920),
]

N_WAVELENGTHS_PER_CUBE = 4
N_CUBES = 2
CHUNK_SIZE_PX = 64  # matches eva's default chunk_size_px


def _synthetic_frame(height: int, width: int, wavelength_index: int, rng: np.random.Generator, max_value: int) -> np.ndarray:
    """A few Gaussian spots (like SimulatedCamera) plus Poisson-like shot
    noise, scaled to max_value (1023 for 10-bit content, 4095 for 12-bit) -
    real sensor images are spatially smooth with photon-counting-like noise,
    not uniform random bytes (which would be worst-case incompressible and
    make every format look artificially bad)."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    image = np.zeros((height, width), dtype=np.float64)
    centers = [(width * 0.3, height * 0.5), (width * 0.7, height * 0.5)]
    peak = max_value * 0.6
    sigma = min(height, width) * 0.03
    for cx, cy in centers:
        image += peak * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma**2)))
    image += max_value * 0.05  # baseline
    # Shot noise scales with signal (Poisson-like) - real camera behavior.
    noisy = rng.poisson(np.clip(image, 0, None)).astype(np.float64)
    return np.clip(noisy, 0, max_value).astype(np.uint16)


def _pack_10bit(frame_u16: np.ndarray) -> bytes:
    """Bit-pack a 10-bit-range uint16 array to 10 bits/pixel (Mono10p-style),
    matching what the camera's packed pixel format would deliver on the
    wire - tests whether pre-packing before compression helps beyond what
    Blosc's bitshuffle already exploits on the wider container."""
    flat = frame_u16.ravel().astype(np.uint16)
    bits = np.unpackbits(flat.astype(">u2").view(np.uint8)).reshape(-1, 16)[:, -10:]
    packed = np.packbits(bits.ravel())
    return packed.tobytes()


@dataclass
class WriteResult:
    label: str
    total_bytes_raw: int
    total_bytes_on_disk: int
    elapsed_s: float

    @property
    def mb_per_s(self) -> float:
        return (self.total_bytes_raw / (1024 * 1024)) / max(self.elapsed_s, 1e-9)

    @property
    def compression_ratio(self) -> float:
        return self.total_bytes_raw / max(self.total_bytes_on_disk, 1)


def _crc32c_pure_python(data: bytes) -> int:
    poly = 0x82F63B78
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    crc = 0xFFFFFFFF
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


def bench_tiff(frames_by_cube: list[list[np.ndarray]], out_dir: Path, *, compress: bool) -> WriteResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    total_raw = 0
    started = time.perf_counter()
    for cube_index, frames in enumerate(frames_by_cube):
        for wl_index, frame in enumerate(frames):
            path = out_dir / f"cube{cube_index}_wl{wl_index}.tif"
            tifffile.imwrite(path, frame, compression="zlib" if compress else None)
            total_raw += frame.nbytes
    elapsed = time.perf_counter() - started
    on_disk = sum(f.stat().st_size for f in out_dir.glob("*.tif"))
    label = "TIFF (zlib)" if compress else "TIFF (uncompressed)"
    return WriteResult(label, total_raw, on_disk, elapsed)


def bench_zarr_shard_per_cube(
    frames_by_cube: list[list[np.ndarray]], out_dir: Path, *, height: int, width: int, codec: Blosc | None, label: str
) -> WriteResult:
    """Incremental per-cube shard write: one shard file per completed
    SpectralCube (covering all its wavelengths), written directly with the
    same hand-rolled index/CRC32C format eva's write_shard() uses -
    bypassing zarr's own array-assignment API, which is what made eva's
    batch exporter fast (119 MB/s vs 21 MB/s, see this file's docstring).
    Chosen over eva's default per-image sharding specifically because it
    produces one new file per cube as an experiment runs, not one per
    wavelength per cube - fewer files for a live, open-ended-length write.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    ich = icw = CHUNK_SIZE_PX
    sh = ((height + ich - 1) // ich) * ich
    sw = ((width + icw - 1) // icw) * icw
    n_cy, n_cx = sh // ich, sw // icw

    total_raw = 0
    total_on_disk = 0
    started = time.perf_counter()
    for cube_index, frames in enumerate(frames_by_cube):
        n_wl = len(frames)
        n_inner = n_wl * n_cy * n_cx
        all_bufs: list[bytes] = []
        offsets = np.full(n_inner, np.uint64((1 << 64) - 1), dtype=np.uint64)
        lengths = np.full(n_inner, np.uint64((1 << 64) - 1), dtype=np.uint64)
        byte_offset = 0
        for wl_index, frame in enumerate(frames):
            total_raw += frame.nbytes
            if (height, width) != (sh, sw):
                padded = np.zeros((sh, sw), dtype=frame.dtype)
                padded[:height, :width] = frame
                plane = padded
            else:
                plane = np.ascontiguousarray(frame)
            base_ci = wl_index * (n_cy * n_cx)
            for cy in range(n_cy):
                for cx in range(n_cx):
                    tile = np.ascontiguousarray(plane[cy * ich : (cy + 1) * ich, cx * icw : (cx + 1) * icw])
                    encoded = codec.encode(tile.tobytes()) if codec is not None else tile.tobytes()
                    ci = base_ci + cy * n_cx + cx
                    offsets[ci] = byte_offset
                    lengths[ci] = len(encoded)
                    all_bufs.append(encoded)
                    byte_offset += len(encoded)

        index = np.empty(n_inner * 2, dtype=np.uint64)
        index[0::2] = offsets
        index[1::2] = lengths
        index_bytes = index.tobytes()

        shard_path = out_dir / f"cube{cube_index}.shard"
        with shard_path.open("wb") as f:
            for buf in all_bufs:
                f.write(buf)
            f.write(index_bytes)
            f.write(struct.pack("<I", _crc32c_pure_python(index_bytes)))
        total_on_disk += shard_path.stat().st_size
    elapsed = time.perf_counter() - started
    return WriteResult(label, total_raw, total_on_disk, elapsed)


def bench_packed_10bit_zarr(frames_by_cube: list[list[np.ndarray]], out_dir: Path, *, codec: Blosc | None, label: str) -> WriteResult:
    """Same shard-per-cube approach, but each plane is bit-packed to 10
    bits/pixel *before* compression - tests whether pre-packing helps
    beyond what Blosc's bitshuffle already exploits on the wider uint16
    container."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total_raw = 0
    total_on_disk = 0
    started = time.perf_counter()
    for cube_index, frames in enumerate(frames_by_cube):
        bufs: list[bytes] = []
        for frame in frames:
            total_raw += frame.nbytes
            packed = _pack_10bit(frame)
            encoded = codec.encode(packed) if codec is not None else packed
            bufs.append(encoded)
        shard_path = out_dir / f"cube{cube_index}.packed"
        with shard_path.open("wb") as f:
            for buf in bufs:
                f.write(struct.pack("<I", len(buf)))
                f.write(buf)
        total_on_disk += shard_path.stat().st_size
    elapsed = time.perf_counter() - started
    return WriteResult(label, total_raw, total_on_disk, elapsed)


def run_config(name: str, height: int, width: int) -> None:
    print(f"\n=== {name} ({width}x{height}, {N_WAVELENGTHS_PER_CUBE} wavelengths/cube, {N_CUBES} cubes) ===")
    rng = np.random.default_rng(42)

    for bit_depth, max_value in ((10, 1023), (12, 4095)):
        print(f"\n--- {bit_depth}-bit content (values 0-{max_value}, stored as uint16) ---")
        frames_by_cube = [
            [_synthetic_frame(height, width, wl, rng, max_value) for wl in range(N_WAVELENGTHS_PER_CUBE)]
            for _ in range(N_CUBES)
        ]

        results: list[WriteResult] = []

        tiff_dir = OUT_DIR / f"{name}_{bit_depth}bit_tiff_raw"
        if tiff_dir.exists():
            shutil.rmtree(tiff_dir)
        results.append(bench_tiff(frames_by_cube, tiff_dir, compress=False))

        tiff_zlib_dir = OUT_DIR / f"{name}_{bit_depth}bit_tiff_zlib"
        if tiff_zlib_dir.exists():
            shutil.rmtree(tiff_zlib_dir)
        results.append(bench_tiff(frames_by_cube, tiff_zlib_dir, compress=True))

        zarr_none_dir = OUT_DIR / f"{name}_{bit_depth}bit_zarr_none"
        if zarr_none_dir.exists():
            shutil.rmtree(zarr_none_dir)
        results.append(
            bench_zarr_shard_per_cube(frames_by_cube, zarr_none_dir, height=height, width=width, codec=None, label="zarr shard (uncompressed)")
        )

        zarr_lz4_dir = OUT_DIR / f"{name}_{bit_depth}bit_zarr_lz4"
        if zarr_lz4_dir.exists():
            shutil.rmtree(zarr_lz4_dir)
        lz4_codec = Blosc(cname="lz4", clevel=1, shuffle=Blosc.BITSHUFFLE)
        results.append(
            bench_zarr_shard_per_cube(
                frames_by_cube, zarr_lz4_dir, height=height, width=width, codec=lz4_codec, label="zarr shard (lz4+bitshuffle, eva's setting)"
            )
        )

        zarr_zstd_dir = OUT_DIR / f"{name}_{bit_depth}bit_zarr_zstd"
        if zarr_zstd_dir.exists():
            shutil.rmtree(zarr_zstd_dir)
        zstd_codec = Blosc(cname="zstd", clevel=5, shuffle=Blosc.BITSHUFFLE)
        results.append(
            bench_zarr_shard_per_cube(
                frames_by_cube, zarr_zstd_dir, height=height, width=width, codec=zstd_codec, label="zarr shard (zstd-5+bitshuffle)"
            )
        )

        packed_dir = OUT_DIR / f"{name}_{bit_depth}bit_packed_lz4"
        if packed_dir.exists():
            shutil.rmtree(packed_dir)
        results.append(bench_packed_10bit_zarr(frames_by_cube, packed_dir, codec=Blosc(cname="lz4", clevel=1, shuffle=Blosc.BITSHUFFLE), label="packed-10bit + lz4+bitshuffle"))

        print(f"{'format':<38} {'MB/s':>8} {'ratio':>8} {'MB total':>10} {'ms/frame':>10}")
        n_frames = N_CUBES * N_WAVELENGTHS_PER_CUBE
        for r in results:
            ms_per_frame = (r.elapsed_s * 1000.0) / n_frames
            print(f"{r.label:<38} {r.mb_per_s:>8.1f} {r.compression_ratio:>7.2f}x {r.total_bytes_on_disk / (1024*1024):>9.1f} {ms_per_frame:>9.2f}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, height, width in CONFIGS:
        run_config(name, height, width)
    print(f"\nScratch files written under {OUT_DIR} (safe to delete).")


if __name__ == "__main__":
    main()
