# Performance tuning: apps that adapt to the machine (2026-10-22)

Maintainer request: the suite will run on computers much faster (or slower) than the development laptop, so parallel work must tune itself,
and the rule must be written down so future building stays performance-aware. This is the design record and the recipe. The code is
`packages/lspr_core/src/lspr_core/perf/`; the rule is one bullet in the root `CLAUDE.md` ("Parallel work is performance-aware").

## The rule

**Tuning changes how work is scheduled, never what is computed.** Worker count, priority and how many tasks are in flight are fair game.
Anything that is saved (chunk shape, compression, rounding, file layout) is never tuned: it is a data decision, reviewed like any format
change (engineering priorities 1 and 2 outrank speed, `docs/engineering_policy.md`). A job that adopts tuning must produce
byte-identical output at any worker count and in any completion order, and a test must pin that.

## Why this design

| Question | Decision | Reason |
|---|---|---|
| Automatic or user-controlled? | **Standard by default** (every core but one, no searching); **Tune automatically** is opt-in in Edit -> Preferences and saves what it finds for the machine; **Fixed number** for a shared workstation or a benchmark | Decided 2026-10-22 after the first measurements: on the development laptop tuning gained nothing and cost a few percent (see "Measured"). Scientists and students should not need to know what a worker is, so the default needs no thought; tuning is there to be tried on a new or much faster computer, and its result is kept |
| Benchmark the computer at start-up? | No. A cheap static look (cores, free memory, battery, disk kind) and measuring the running job | A start-up benchmark is slow and noisy (the development laptop drifts up to 3x between identical runs) and can be wrong ten minutes later |
| What does the tuner optimise? | Units of work per second (images/s), nothing else | The earlier export tuner used the share of time "in I/O". On the development laptop it counted decoding and rotation as I/O, so it reacted to the wrong thing. Throughput is right whatever the bottleneck (cores, disk, memory bandwidth) |
| Where does it live? | `lspr_core.perf`, a Qt-free subpackage | Several apps need it; `lspr_core` already has no GUI dependencies (import-linter contract). `psutil` is soft-imported |

## What is in `lspr_core.perf`

| File | What it does |
|---|---|
| `system_profile.py` | `probe_system()`: usable logical cores (honours CPU affinity), physical cores, total and free memory, battery, a fingerprint of the machine kind. `probe_destination(path)`: local, removable or network disk and free space (Windows drive type) |
| `preferences.py` | `PerfPreferences`: mode (`standard` default, `auto` = tune, `manual` = fixed number), the fixed number, cores kept free (default 1), low priority (default on) |
| `limits.py` | `worker_limits(...)`: the allowed range. Cores minus the reserved ones, capped by free memory (`0.6 x free / per-worker bytes`) and by the number of tasks. Starts at the top of the range, which is what these jobs did before tuning existed. Standard mode is that one count (or the count tuning saved) with no range to search |
| `tuner.py` | `ThroughputTuner`: the search (below). Pure, no clock or threads of its own, tested on simulated machines |
| `gate.py` | `ConcurrencyGate`: a counting semaphore whose limit can change while it is used. Thread pools are created once at the largest size and only the tuned number of tasks run at once |
| `priority.py` | `lower_process_priority()` for worker processes (below normal; the window keeps its turn) |
| `profile_store.py` | `ProfileStore`: the counts tuning saved, one small JSON file, keyed by machine fingerprint, task and disk kind (`entries()` to list them, `forget()` to delete this machine's). Never an input to a result: it can be deleted, edited or corrupt |
| `runtime.py` | `get_runtime().begin(task_key, ...)` returns a `TunedRun` (`workers`, `maximum`, `unit_done()`, `finish()`), `RunReport` for the log, `set_runtime()` for the app and for tests |

### The search

Every window (at least 3 s and 2 units per worker) the tuner computes units/s at the current count, then moves:

1. The first window after a start or a change is discarded (the pool is still starting).
2. **Baseline** at the starting count, then **up**: try more workers; keep them only if the rate rises by more than 5 % (more workers cost memory,
   so they must win clearly). A failed step is retried with half the step, down to one worker.
3. **Down**: try fewer workers from the best count; keep them if the rate stays within 5 % of the **best rate seen in this search** (not of the
   previous window, so small losses cannot add up). On a plateau the tuner therefore settles on the cheapest count within noise of the best.
4. **Settled**: hold. After 15 windows, or when the rate falls 25 % (another program started, the laptop got hot), search again in steps of one.
5. Not tuned at all: Standard and Fixed modes, a range with one value, a job shorter than 12 windows, and no new probe near the end of a job.
   Only a finished search in "Tune automatically" mode is saved; Standard uses the saved count but never overwrites it.

The pool is created **once** at the largest size. `ProcessPoolExecutor` starts a process only when a submitted task has no idle one to take it, so
the number of tasks in flight *is* the worker count: raising it starts processes once, lowering it just stops submitting. (The earlier design
re-created the whole pool on every change: several seconds of Windows process start-up each time.)

## What is wired (2026-10-22)

| Job | Task key | Unit | Notes |
|---|---|---|---|
| OME-Zarr export (`dataset/io.py`) | `export.ome_zarr` | planes | Replaces the I/O-fraction tuner (`ADAPTIVE_*` constants removed). `adaptive_workers_enabled=False` pins the starting count; `adaptive_batch_mb` is kept in the signature but unused. Status line and the carry prompt say "Used N workers (auto)" |
| Transient scan (`image_tools/mask/transient_scan.py`) | `mask.scan.screen` | frames | `ScanParams.workers=None` hands the choice to the runtime (Standard: the earlier measured default of at most 6, or the saved count; Tune automatically: searched from there); a number is exact. The screening stage is tuned; the later stages keep the count it settled on. Reading threads are gated at their entry points only |
| Analysis step 1 (`analysis/scan.py` `scan_cubes`) | `analysis.scan` | tasks of `cubes_per_task` cubes | `processes=None` hands the choice to the runtime (see above); a number is exact (<= 1 runs in this process). Only the lab tool calls it today |
| ROI features (`image_tools/mask/roi_feature_run.py` `fit_crops`, 2026-10-29) | `mask.roi_features` | tasks of 4 ROIs | The Mask tab's ROI features tool: the per-ROI fit (69 ms each, 11 s for 160 ROIs on one core) on worker processes; `processes=None` hands the choice to the runtime, a number is exact (<= 1 runs in this process). The pool is made for the run and closed at its end. Patches and mask are identical at 1, 2 and 3 processes (`tests/unit/test_lspri_roi_feature_run.py`). Measured through the real task: 6.9 s first run, 1.4 s for a re-run (the fit is kept). A worker measured 93 MB resident (numpy + scipy 92 MB, fitting adds ~1 MB, development laptop); the limit uses 300 MB |

Deliberately **not** wired:

- **Playback read-ahead and the render thread** (`panels/image/render.py`): latency-bound, not throughput-bound; a tuner that probes would show as stutter.
- **Session mask reads** (`storage/session.py`, `_MASK_READ_WORKERS`): hundreds of tiny PNGs, a fixed small pool is fine.
- **The old generation** (`io/dataset.py`, `gui/analysis_tasks.py`, `calibrate_analysis_worker_count`): edited only on the maintainer's request. The old app keeps its own I/O-fraction tuner.
- **Chunk shape, compression level, rounding**: saved data, never tuned (the rule above).

## Performance mode of the analysis scan (2026-10-27)

Not a worker *count* but the *lifetime* of the workers: the Analysis tab's Mode pick-up (Normal / Performance, remembered as `analysis/mode`) keeps
the scan's worker processes alive between scans (`apps/LSPRi/eva/.../analysis/scan_pool.py`), because starting them (about 1,000 imports per worker, 4-6 s
for all of them) is most of a short refresh. The count is still chosen by `lspr_core.perf` (a pool is made for the count a big scan would get and is
replaced if a scan needs more or the priority setting changed). Measured: a one-ROI refresh of 63 cubes 5.8 s -> 1.1 s, of all 314 cubes 12.1 -> 7.8 s;
the cost is about 150 MB of memory per idle worker. Recipe for another job that wants it: `scan.scan_cubes(warm=...)` shows the pattern (settings and a
run number travel with each task, a worker drops its caches when a new run starts, workers watch their parent). Record:
`apps/LSPRi/eva/docs/roi_nudge_partial_scan_proposal_2026-10-26.md` section 11.

## Preferences screen (Edit -> Preferences..., group "Parallel work (export and scans)")

Screen purpose: keep the standard behaviour, try tuning on this computer, or fix the number of workers. Main actions: choose Standard
(default), Tune automatically or Fixed number; set the number (Fixed) or the cores kept free (Standard and Tune automatically); keep
background work at low priority; "Forget saved counts" (enabled only when tuning has saved something). Layout: the group sits under
"Performance and memory" in the existing one-page dialog; a greyed status shows this computer ("4 cores / 8 threads, 31.8 GB memory
(13.6 GB free), on mains") and the counts tuning saved ("export to a local disk: 4 workers (31/s)"). Validation: the number is 1 to twice the
logical cores; cores kept free is 0 to cores - 1. A choice takes effect for the next export or scan (a running one keeps its own).
Remembered like every control (`storage/ui_state_keys.py` `PERF_*`); OK without a change writes nothing; "Forget" is a request that OK
carries out (Cancel forgets nothing).

**How a user tries tuning**: pick "Tune automatically", do one or two exports (the first search takes about 20-30 s of an export), look at
the status line and the export result ("Used 4 workers (auto)"), then switch back to Standard: it keeps using the saved count. Switching
tuning on again re-searches and replaces the saved count.

## Learned settings file

`perf_profile.json` next to `lspri_eva_rewrite_settings.json` (`%LOCALAPPDATA%` or `%APPDATA%` `lspr-suite`, via `platformdirs`). Key:
machine fingerprint (`platform-pCORES-lTHREADS-rGIB`), task, disk kind. Written only when a "Tune automatically" run finished its search with
at least 5 measured windows; Standard mode reads it. "Forget saved counts" in Preferences, or deleting the file, returns to every core but
one. A different machine kind (other core count or memory) never uses another's entry. Tests never touch it: `install_runtime` runs in
`main()` only.

## How to make a new parallel job performance-aware (the recipe)

```python
run = get_runtime().begin("domain.task", total_units=n_units, per_worker_bytes=bytes_one_worker_needs, max_useful=n_tasks,
                          destination=output_folder_or_None, start_hint=measured_default_or_None)
pool = ProcessPoolExecutor(max_workers=run.maximum, initializer=...)   # or a thread pool of that size + run.gate.wrap(fn)
# keep run.workers tasks in flight (processes), or gate the entry points (threads); never nest the gate
run.unit_done(units)            # after each finished unit, from any thread
report = run.finish()           # only after the job completed; log_event(logger, "perf.run", **report.as_fields())
```

Checklist: output identical at any worker count (test it with manual 1, manual 3 and automatic, compare file hashes); a unit is one finished
image, frame or task, all of similar size; the memory estimate is on the safe side; the log carries `perf.run`; worker processes lower their
priority when `run.low_priority`; a test drives the new job's tuning on a simulated machine, not on hardware.

## Measured (development laptop: i7-10510U, 4 cores / 8 threads, Windows)

Real `export_ome_zarr_dataset`, 1620 planes of the Bulk sensitivity dataset (rotation 1.42 deg, crop, 80 px chunks, lz4), one run per row
and round, alternating, a fresh output folder each time. The machine's speed drifts between rounds (rounds of the first table ran
slower than the second: not comparable across tables, only within a round).

**Table 1, first tuner (before the fixes below)** - images/s:

| Round | Committed code (8 -> 10 workers by its I/O tuner) | New, tuning off (7 workers) | New, tuning on |
|---|---|---|---|
| 1 | 20.9 | 20.4 | 19.1 (settled on 3) |
| 2 | 21.4 | 25.7 | 25.6 (settled on 7) |
| 3 | 23.7 | 26.2 | 24.9 (settled on 6) |
| median | 21.4 | 25.7 | 24.9 |

New code with tuning off is about 20 % faster than the committed code: the committed tuner replaced the whole pool when it flipped (several
seconds of process start-up) and ran 10 workers; the new code keeps exactly 7 tasks in flight in one pool.

**Table 2, after the fixes** (clock starts at the first finished unit; a downward probe costs one window, not two; the downward step shrinks
when the rate starts to fall) - images/s:

| Round | Tuning off (7 workers) | Tuning on, first run | Tuning on, starting from the learned count |
|---|---|---|---|
| 1 | 32.8 | 30.1 (settled on 4) | 27.9 (4) |
| 2 | 27.3 | 26.7 (3) | 30.1 (3) |
| 3 | 33.2 | 32.4 (5) | 31.9 (4) |
| median | 32.8 | 30.1 | 30.1 |

(The default was changed to Standard after these tables, 2026-10-22: tuning is opt-in.)

**What this says, honestly.** On this laptop the throughput curve is flat from 3 to 7 workers (each worker's OpenCV rotation already uses all
8 hardware threads, so 3 workers keep the CPU busy). There is nothing for the tuner to win here, and the search costs a few percent
(median 8 %, 2 % in the better two rounds; the search lasts about 20-30 s of a 50 s run, so on the full 8478-plane export it is a smaller share).
What it buys here: it settles on 3-5 workers instead of 7 (less CPU and memory taken from the rest of the computer, and from the window). Its
purpose is the machines we cannot measure: a 32-thread workstation, a network share, a computer with little memory. Simulated versions of
those are in `tests/unit/test_lspr_core_perf_tuner.py`. If a future measurement on a real second machine shows the search costing more than it
finds, the answer is the default, which is now Standard; if a second machine shows tuning clearly winning, the default can move back.

**Memory per export worker** (peak resident size sampled every 0.25 s over a 810-plane export, 7 workers): 122-128 MB each. The limit uses
280 MB (`_EXPORT_WORKER_BASE_BYTES` + 6 float32 planes + the shard), about twice the measurement, on purpose. `_SCAN_WORKER_BYTES` (analysis
step 1, 600 MB) is a guess, not measured: that job is only run from the lab tool today.

**Identical results at any count**: `tests/integration/test_lspri_export_workers.py` exports the same dataset with 1 worker, 3 workers and
automatic and compares the SHA-256 of every file (shards, index, metadata): identical. The scan tests do the same for the transient scan
(automatic count equals a fixed one, never more reads at once than the limit) and for analysis step 1.

## Open items

- Only one machine has been measured. The point of the `perf.run` log event and the machine line in the log (`perf.machine`) is that logs from
  other computers tell us whether the search finds something a fixed default would miss.
- The per-worker memory estimates (`_EXPORT_WORKER_BASE_BYTES`, `_SCAN_WORKER_BYTES`) are measured on this machine only.
- Disk kind is only detected on Windows (fixed, removable, network). SSD versus hard disk is not detected: the tuner measures it instead.
- A "Tune now (30 s)" button and a per-disk profile view in Preferences were not built: no evidence yet that they are needed.
