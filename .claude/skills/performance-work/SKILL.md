---
name: performance-work
description: "Use when writing or optimizing code that scales with dataset size (per-cube, per-wavelength, per-ROI, per-frame), investigating a 'this is slow' report, verifying a performance fix, or changing numeric scientific-compute paths where computed values or fast-path vs reference-path test tolerances could shift."
---

# Performance Work: Instrumentation, Verification, and Testability

Refined from a 2026-09 investigation into "Start analysis" slowness in LSPRi
Evaluation — see `apps/LSPRi/eva/docs/bulk_analysis_performance_investigation.md`
and `apps/LSPRi/eva/docs/roi_scoped_resample_cv2_fast_path.md` for the full
case studies these rules are drawn from.

**Instrument hot paths at write time, not after a complaint.** Add cheap,
always-on stage timing (`time.perf_counter()` around each phase, one
aggregated `logger.debug(...)` line per outer unit of work) to any new code
that scales with dataset size (per-cube, per-wavelength, per-ROI, per-frame,
etc.) *when it's written* — this repo already has the right convention in
places (e.g. the "SG fast task stage timing" pattern in `analysis_tasks.py`),
the gap is applying it consistently up front rather than only retrofitting
it once someone reports something slow.
- Aggregate within the inner loop and log **once per outer unit of work**
  (e.g. sum every wavelength's timing and log one line per cube) — never log
  per-ROI or per-pixel; the logging itself becomes the bottleneck otherwise.
- Use lazy `%s`-style logging with cheap arguments
  (`logger.debug("... %s", value)`), never an f-string or a call to
  something expensive inside the log call — an argument is still evaluated
  eagerly regardless of style, so nothing expensive should be passed in.
- `time.perf_counter()` overhead is negligible (tens of nanoseconds) against
  anything measured in milliseconds — there is no performance argument
  against leaving stage timing permanently in place, gated behind DEBUG
  level exactly like this repo's existing Normal/Debug console toggle
  (DEBUG records always reach the log *file* regardless of that toggle,
  which only filters the on-screen console — see `workflow_log_controller.py`).
- Reserve heavier tools for on-demand diagnosis only, never baked into
  normal runs: `cProfile` (2-10x slowdown, but shows exact call counts and
  hot functions) once stage timing has identified *which* stage is slow but
  not *why*; a sampling profiler (`py-spy`, `austin` — near-zero overhead,
  attaches to an already-running process with no code changes) as the
  lighter first reach for "where is time going" generally.

**Verify a fix with a real before/after measurement — always, even when it
"obviously" mirrors a pattern that worked before elsewhere.** A plausible
optimization can regress performance for reasons invisible from reading the
code alone: a fix modeled directly on a previously-successful pattern
(`PlotManager.roi_area_masks()`'s per-ROI bounding box instead of a
full-image distance grid) made a *different* code path 100x slower, because
that path's own geometry functions already did per-ROI box-shrinking
internally — the "fix" routed it through a different branch that trusted the
caller's much bigger box instead. Caught only because it was measured (a
standalone script against real data) before shipping, not because the code
looked wrong.
- Before considering a performance change complete, measure it — a
  standalone script against realistic data/scale if driving the full app
  isn't practical — not just "the code now looks like the fast pattern used
  elsewhere."
- When a fix doesn't help (or hurts), profile the *mechanism* (call counts,
  `cProfile`) rather than iterating on more guesses.
- When one fix's win is small, check whether the dominant cost is actually a
  **data or configuration decision** (chunk size, index choice, batch size)
  rather than something fixable in application code.

**Parallel work: let `lspr_core.perf` choose the worker count.** Do not hard-code a pool size or read `os.cpu_count()` for a new fan-out (export, scan, bulk read). Ask the runtime (`get_runtime().begin(...)`), report finished units, and test that the output is identical at worker counts 1, 3 and automatic. Tuning is for throughput-bound batch jobs; playback and other latency-bound paths are not tuned (a probing tuner shows as stutter). Recipe, wiring and the reasons: `docs/performance_tuning.md`. Rules (moved from root CLAUDE.md, 2026-11-25): (1) **tuning changes scheduling, never results**: output identical at any worker count and completion order, pinned by a test; (2) tune on measured throughput (units per second), not a proxy such as I/O-wait fraction; (3) default is **Standard** (every core but one, no searching; searching gained nothing on the dev laptop and cost a few percent); **Tune automatically** is opt-in in Preferences and saves the count it finds for the machine (Standard then uses it); plus a Fixed number, cores kept free and low priority, all remembered; (4) log the chosen count and mode (`perf.run`); (5) test a new job's tuning on a simulated machine, not hardware; (6) never tune what is saved (chunk shape, compression, rounding); (7) **jobs share the cores** (2026-11-26, `lspr_core/perf/budget.py`): `begin()` takes a lease, so `run.workers` can shrink or grow while the job runs (keep `run.workers` tasks in flight, re-read it every time) and **the run must be closed on every exit path** (`run.close()` in a `finally`, `with ... begin(...) as run`, or `finish()`); a run that is only asked for a size uses `plan()`; a small fixed pool uses `pool_threads(cap)`; (8) **register a new pool or core-count read** in `tests/unit/test_parallel_work_follows_perf_rules.py`: that test fails for a thread / process pool nobody registered, which is how this rule binds future code.

**Numeric changes to scientific-compute paths need their own correctness
check, separate from unit tests.** A change touching measured or derived
values (not just control flow) needs a standalone before/after comparison
against realistic data, in addition to whatever unit tests pass. Compare the
discrepancy against a domain-meaningful baseline (e.g. the sensor's own
shot-noise floor, not just "looks small") before deciding it's acceptable.
- An existing test that pins two code paths to tight numerical agreement
  (e.g. "fast path must equal reference path to `atol=1e-6`") is a
  deliberate tripwire, not an obstacle to route around. If a legitimate
  change trips it, don't loosen the tolerance on intuition — measure the
  actual worst-case discrepancy on that test's own data, set the new bound
  from that measurement with explicit margin, and document why in both the
  test and a comment at the change site.
- Flag any change that measurably alters computed values to the maintainer
  explicitly, with the quantified impact, even when it is negligible
  relative to real-world noise — don't fold it in silently just because
  tests still pass.

**Check upstream fixes before designing a workaround, when a dependency's
behavior looks like a bug.** When a third-party library's behavior is an
inexplicable performance cliff or looks like a bug rather than expected
behavior, check its changelog/release notes/issue tracker for a targeted,
already-merged fix before designing an application-level workaround —
especially for pre-1.0-adjacent or actively-developed libraries (the same
libraries this repo's Dependency Pinning policy already treats as
higher-churn are also more likely to have just-fixed rough edges). This is
not "keep dependencies generally current" — it's "when you hit an
inexplicable wall, check whether it's already been fixed upstream before
building around it," and then verify the fix against the project's own real
data/workload before adopting it, not just the changelog's claim (recheck
sibling packages that share the dependency for compatibility, same as any
other pin update).
