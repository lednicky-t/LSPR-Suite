# Performance tuning: apps that adapt to the machine (2026-10-22)

Maintainer request: the suite will run on computers much faster (or slower) than the development laptop, so parallel work must tune itself,
and the rule must be written down so future building stays performance-aware. This is the design record and the recipe. The code is
`packages/lspr_core/src/lspr_core/perf/`; the rule is one bullet in the root `CLAUDE.md` ("Common Pitfalls", parallel fan-out), with the full rules in the `performance-work` skill.

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

## The layers (how this compares with other software, 2026-11-26)

Mature software (.NET's thread pool, FFTW, cuDNN, Photoshop, MATLAB, Spark) splits the problem the same way. Each layer has one job:

| Layer | Job | Here |
|---|---|---|
| 1. Look at the machine | Safe *range*, not the best value: cores (honouring affinity), free memory, battery, disk kind and type | `system_profile.py`, `limits.py`: `worker_limits` for tuned jobs, `pool_threads` for the small fixed pools |
| 1b. Share between jobs | Several jobs at once must not each take every core (Grand Central Dispatch, oneTBB, connection pools do the same) | `budget.py`: every `begin()` takes a lease, `finish()` / `close()` gives it back (next section) |
| 2. Measure while working | Find the best value on the running job | `tuner.py` (hill climbing on units per second) |
| 3. Remember | Do not search twice on the same kind of machine | `profile_store.py` (`perf_profile.json`), like FFTW's wisdom file |
| 4. The user decides | Standard, Tune automatically, Fixed number, cores kept free, priority | `preferences.py` and the Preferences dialog |

**Every thread or process count in an app comes from layer 1 (and 1b-3 where the job is big enough to be worth tuning).** No file calls
`os.cpu_count()` for a count: it ignores a CPU-affinity limit and "cores kept free". A small pool that is not worth tuning (a few hundred
tiny files) asks `get_runtime().pool_threads(cap, extra_free=, floor=)`; a big job asks `get_runtime().begin(...)`. **This is enforced:**
`tests/unit/test_parallel_work_follows_perf_rules.py` reads the code of every app and package and fails when a thread or process pool, or a
read of the core count, appears in a file that is not registered there with the way it gets its count.

## The shared budget (2026-11-26): jobs that run at the same time share the cores

Maintainer decision, 2026-11-26: it is unlikely today that two big jobs overlap, but it will not stay that way (a second acquisition app, a
batch queue), so the mechanism is built now and every future job goes through it. The code is `perf/budget.py`; the glue is in `runtime.py`.

**How it works.**

* One number, the **capacity**: the cores the app may keep busy (usable cores minus the cores kept free). It is set from the machine and the
  Preferences at every `begin()`.
* `begin()` takes a **lease**: "this job would like N workers" (its range's maximum). The budget answers with an **allowance**: how many
  it may use now. Fair share (max-min): equal shares, nobody above its wish, what a small job does not need goes to the others. On a
  7-core budget two big jobs get 4 + 3 (the older keeps more), a 2-task job beside a big one gets 2 + 5.
* **Never blocks, never starves**: every lease gets at least 1 worker at once. With more jobs than cores the total may exceed the capacity
  by that minimum (the operating system would do the same).
* **Running jobs adapt both ways.** When another job starts or ends the budget calls the lease's callback; the run applies its new
  allowance by giving its tuner a new range (`ThroughputTuner.set_limits`): the count moves at once, the gate that holds back the
  threads follows, a job that was being searched restarts its search inside the new range and climbs back when cores come free again.
  The pool is still created at `run.maximum`; only the number of tasks in flight (`run.workers`) shrinks. Processes start lazily, so an
  allowed-but-unused slot costs nothing.
* **A fixed number of workers (Preferences) is a hard lease**: exactly that many; the other jobs share what is left.
* **A shared run does not teach the profile**: a rate measured while another job took cores is not what the computer does alone, so
  `RunReport.shared` is true and nothing is saved for it. Logged as `shared` in `perf.run`.
* **What is not shared**: memory. It is checked when a job starts, from the memory that is free then (a second job starts with less free,
  so it gets fewer workers by memory too).

**Giving back is the part that must not be forgotten.** A lease that is never released would starve every later job for the rest of the
session. The ways it ends, in order of preference: `run.finish()` (the job completed: remembers, reports, releases); `run.close()` in a
`finally` or `except` (failed or cancelled: releases, no report); `with get_runtime().begin(...) as run:` (closes on any exit);
`run.report()` for a job whose tuned stage ends before the job does (reports and remembers, **keeps** the lease: the transient scan's
later stages). As a last resort, a run that is simply dropped gives its share back when it is freed (weak references, so neither the
budget nor the tuner keeps it alive). Do not rely on that last one. Code that only needs a pool *size* calls `get_runtime().plan(...)`
(the range, no lease): a `begin()` used for peeking would take a share and make the real job smaller.

## What is in `lspr_core.perf`

| File | What it does |
|---|---|
| `system_profile.py` | `probe_system()`: usable logical cores (honours CPU affinity), physical cores, total and free memory, battery, a fingerprint of the machine kind. `probe_destination(path)`: local, removable or network disk and free space (Windows drive type), and for a fixed disk whether it is an SSD or an HDD (2026-11-26, Windows `IncursSeekPenalty`; `unknown` when the drive does not answer). The SSD/HDD word is **information only** (log field `media`, debug line): it changes no count and is not part of the saved-count key, because nobody has measured what an HDD should get |
| `preferences.py` | `PerfPreferences`: mode (`standard` default, `auto` = tune, `manual` = fixed number), the fixed number, cores kept free (default 1), low priority (default on) |
| `limits.py` | `pool_threads(...)` (2026-11-26): the count for a small fixed pool that is not tuned: cores this process may use, minus the cores kept free, minus `extra_free`, between `floor` and the caller's `cap`. `worker_limits(...)`: the allowed range. Cores minus the reserved ones, capped by free memory (`0.6 x free / per-worker bytes`) and by the number of tasks. Starts at the top of the range, which is what these jobs did before tuning existed. Standard mode is that one count (or the count tuning saved) with no range to search |
| `budget.py` | `WorkerBudget`, `BudgetLease`, `fair_shares()` (2026-11-26): the cores shared between live jobs (previous section). Pure, thread-safe, no Qt |
| `tuner.py` | `ThroughputTuner`: the search (below). Pure, no clock or threads of its own, tested on simulated machines. `set_limits()` takes a new range while the job runs |
| `gate.py` | `ConcurrencyGate`: a counting semaphore whose limit can change while it is used. Thread pools are created once at the largest size and only the tuned number of tasks run at once |
| `priority.py` | `lower_process_priority()` for worker processes (below normal; the window keeps its turn) |
| `profile_store.py` | `ProfileStore`: the counts tuning saved, one small JSON file, keyed by machine fingerprint, task and disk kind (`entries()` to list them, `forget()` to delete this machine's). Never an input to a result: it can be deleted, edited or corrupt |
| `runtime.py` | `get_runtime().begin(task_key, ...)` returns a `TunedRun` (`workers`, `maximum`, `share`, `unit_done()`, `report()`, `finish()`, `close()`, usable in a `with`), `plan()` (a size, no lease), `pool_threads()`, `RunReport` for the log, `set_runtime()` for the app and for tests |

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
| Transient scan (`image_tools/mask/transient_scan.py`) | `mask.scan.screen` | frames | `ScanParams.workers=None` hands the choice to the runtime (Standard: the earlier measured default of at most 6, now worked out by `pool_threads(6, extra_free=1, floor=2)`, or the saved count; Tune automatically: searched from there); a number is exact. The screening stage is tuned; the later stages keep the count it settled on. Reading threads are gated at their entry points only |
| Analysis step 1 (`analysis/scan.py` `scan_cubes`) | `analysis.scan` | tasks of `cubes_per_task` cubes | `processes=None` hands the choice to the runtime (see above); a number is exact (<= 1 runs in this process). The Analysis tab's scan (`analysis/scan_task.py`) uses it, and so does the warm pool's size (`scan_task` asks for `run.maximum` once, without tuning) |
| Feature map (`image_tools/mask/feature_map_run.py` `ProcessMapper`, 2026-11-04; replaced the ROI features fit) | `mask.feature_map.fits`, `mask.feature_map.wavelengths` | tasks of 8 disk fits; one wavelength | The Mask tab's Feature map tool: the radial fit of every disk (twice, ~27 s for 160 disks on one core) and the model of every wavelength (1.45 s each, 38 s for 26) on worker processes; one pool per run, one perf run per stage (very different unit costs). `processes=None` hands the choice to the runtime, a number is exact (<= 1 runs in this process). The map is identical with the plain loop, 1 and 2 processes (`tests/unit/test_lspri_feature_map_run.py`; on the real 2026-10-22 data every kind's mask identical, 69.5 s plain, 26.7 s at Standard). Limit 500 MB per worker (numpy + scipy ~90 MB, the picture's model ~60 MB, a job ~40 MB) |
| Sensorgram fits (`apps/LSPRi/eva/.../analysis/trace_pool.py` `TracePool`, 2026-11-26) | `sensorgram.fits` | tasks of ~2000 cubes (one ROI of 2655 cubes) | The Gaussian / Voigt / polynomial metric of every ROI at every cube on helper processes. **One warm process always; more only when the measured work pays for them**: the pool is made at `run.maximum` but a process starts only when a task finds none idle, and the in-flight count is `min(run.workers, pool size, max(processes already running, tasks left x mean task compute time / 3 s))`, so a 2 s polynomial run stays on one process and a 28 s Voigt run uses all allowed (a new process costs ~1-2 s to start). Limit 250 MB per worker (measured 145 MB idle, 175 MB peak). Extra processes are released after 2 min idle (one warm helper stays). Same tasks at any count, results collected by ROI id: traces identical at Fixed 1, Fixed 3 and Standard, on the real scan too (`tests/unit/test_lspri_analysis_trace_pool.py` `FanOutTests`; simulated 4-core machine, real processes). Real scan, 160 ROIs x 2655 cubes, 4 cores / 8 threads: Gaussian 8.6 s -> 5.7-6.6 s cold, ~4.5-5 s warm; Voigt 28.5 -> 13.4 s cold, 9.8 s warm; the polynomial fit unchanged (1 process). The throughput curve is flat from 4 to 7 workers (4 physical cores) |

Deliberately **not** wired:

- **Playback read-ahead and the render thread** (`panels/image/render.py`): latency-bound, not throughput-bound; a tuner that probes would show as stutter.
- **Tuning the session mask reads and the session carry** (`storage/session.py`, `storage/session_carry.py`): hundreds of tiny PNGs, a fixed small pool is fine. Their count is still worked out by `pool_threads` (cap 8), so affinity and "cores kept free" reach them.
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
try:
    pool = ProcessPoolExecutor(max_workers=run.maximum, initializer=...)   # or a thread pool of that size + run.gate.wrap(fn)
    # keep run.workers tasks in flight (processes), or gate the entry points (threads); never nest the gate.
    # run.workers can change at any moment: another job started or ended (the budget), or the tuner moved
    run.unit_done(units)            # after each finished unit, from any thread
finally:
    run.close()                     # always: a failed or cancelled job must give its share of the cores back (idempotent)
report = run.finish()               # only after the job completed; log_event(logger, "perf.run", **report.as_fields())
# or:  with get_runtime().begin(...) as run:  ...   (closes on any exit)
# a small fixed pool that is not worth tuning:   threads = get_runtime().pool_threads(cap=8)
# only a size is needed (a warm pool made ahead):  get_runtime().plan(per_worker_bytes=...).maximum   (never begin() for that)
```

Checklist: output identical at any worker count (test it with manual 1, manual 3 and automatic, compare file hashes); a unit is one finished
image, frame or task, all of similar size; the memory estimate is on the safe side; the log carries `perf.run`; worker processes lower their
priority when `run.low_priority`; **the run is closed on every exit path** (`close()` in a `finally`, or a `with`); a test drives the new
job's tuning on a simulated machine, not on hardware; **the file is registered** in `tests/unit/test_parallel_work_follows_perf_rules.py`
(that test fails for a pool nobody registered).

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
- Disk kind and SSD / HDD are only detected on Windows (fixed, removable, network; SSD or HDD for a fixed disk). A USB SSD is reported as a fixed disk, which is right for speed. The word is information only (see the table above); if logs from an HDD machine show tuning settling far below Standard, use it to cap the starting count.
- A "Tune now (30 s)" button and a per-disk profile view in Preferences are not built (the maintainer asked for a stand-alone test on 2026-11-26). Options weighed: (A) run the existing tuner on a short slice of the real jobs and save the counts, like FFTW's wisdom; (B) a synthetic benchmark (rejected so far: it does not represent each job); (C) a smarter system reader, which is the "layers" work above. A is the next step if the maintainer wants it.
- **Shared budget: built 2026-11-26, not yet measured on two real overlapping jobs.** Tested on simulated machines (shares, shrinking and growing, a failed job giving its share back, 12 threads starting and ending jobs). What to measure when a second job can really overlap: an export with a Sensorgram fit running; the window should stay responsive, and each job's `perf.run` line says `shared`. Not done, on purpose: (1) a settled tuned job could lower its *wish* to the count it settled on, freeing the rest for others at once; (2) priorities between jobs (a foreground job getting more than a background one): the lease has no weight yet, add one when the first job needs it; (3) the warm pools' idle processes (Performance mode, Sensorgram helper) hold no lease: they cost memory, not cores.
- **Hybrid CPUs** (Intel performance + efficiency cores) count as plain logical cores here. Only the tuner (Tune automatically) can find that the efficiency cores are slower; Standard cannot. The start count could use physical cores instead of logical: not changed, because the one machine measured (4 cores / 8 threads) showed a flat curve from 3 to 7 workers and a second machine is needed to tell.
- Threads inside a worker (OpenCV uses every hardware thread of each export worker; `tifffile` `maxworkers` is 1 or 2) are fixed by measurement on one machine. Limiting them is another scheduling-only knob for the tuner, but NumPy / BLAS thread counts can change the order of floating-point sums, so any such change needs the hash test first.

## Rules audit (2026-11-26): is every job following the recipe?

| Job | `begin()` | Worker count from `lspr_core.perf` | Low priority in workers | `finish()` and log | Identical-results test |
|---|---|---|---|---|---|
| Export | yes | yes | yes | `perf.run` INFO | `test_lspri_export_workers.py` |
| Transient scan | yes | yes (start from `pool_threads`) | thread priority (`thread_priority.py`) | `workers`, `worker_mode` in `mask.transient_scan` | transient scan tests |
| Analysis scan | yes | yes | yes | `perf_*` fields in `analysis.scan_done` (not a `perf.run` event) | scan tests |
| Feature map | yes | yes | yes | `perf.run` INFO | `test_lspri_feature_map_run.py` |
| Sensorgram fits | yes (`TracePool`) | yes | yes | `perf.run` DEBUG | `test_lspri_analysis_trace_pool.py` `FanOutTests` |
| Session mask reads, session carry | no (not tuned, on purpose) | `pool_threads` | n/a (threads) | none | session tests |

Found and fixed the same day: three hard-coded `os.cpu_count()` counts (transient scan start, session mask reads, session carry); the Preferences status line showed the raw task keys of the Feature map; two callers used `begin()` only to read a pool size (`scan_task` warm start, `TracePool._wanted_pool_size`: now `plan()`, which takes no share); no job gave its run back when it failed or was cancelled (now `close()` in the `finally` / `except` of the export, the analysis scan, the Sensorgram fits and the Feature map, and a `with` in the transient scan). Not changed: the analysis scan and the transient scan log their worker count under their own events instead of `perf.run` (the information is there; one name would be tidier). The acquisition apps and the launcher have no worker pools (one acquisition process, a one-thread device pool).
