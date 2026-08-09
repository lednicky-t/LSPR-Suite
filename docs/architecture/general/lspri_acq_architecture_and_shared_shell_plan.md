# LSPRimaging Acquisition — Architecture & Build Plan

Status: **planning document, no code written yet**. This is the design the maintainer
signed off on before implementation starts. Update it as decisions change; don't let it
drift out of sync with what actually gets built.

This document covers two things that have to happen together:

1. Extracting the modality-agnostic parts of singleLSPR Acquisition (`apps/sLSPR/acq`)
   into a shared package, so a bug fix in fluidics/experiment-control/sensorgram/HDF5
   only has to happen once.
2. Building the new `LSPRimaging Acquisition` app (`apps/LSPRi/acq`) on top of that
   shared shell, with its own camera + illumination device layer, image/ROI panels,
   and extinction-spectrum-to-sensorgram pipeline.

Decisions already made (see prior conversation, not repeated here in full):
- Separate app, not a mode bolted onto sLSPR acq — but sharing a real code layer, not
  duplicating it.
- Shared shell extracted **before** the new app is built against it (safer sequencing
  than build-then-extract).
- v1 targets **SW-triggered** acquisition only (explicit set-wavelength → grab-frame
  sequencing). HW-triggered (TTL sync) is a deliberate fast-follow once SW-triggered
  timing is measured for real.
- A camera+ROI throughput spike (Phase 0 below) runs **before** any of this is built,
  to replace guesses about 16 Hz/5 MP feasibility with real numbers.
- ROI domain types and vectorized mask/mean extraction are **ported** (copied and
  adapted) from `apps/LSPRi/eva`, not imported — that app stays fully decoupled.
- The Lori control SW audit (buffer/desync root cause: synchronous save sharing a
  thread with live display) is a concrete "don't repeat this" reference for the new
  acquisition worker design (§8).

---

## 1. Goals and non-goals

**Goals for v1:**
- Control a multispectral illumination source (LCTF or LED array) and a camera
  (Basler first) through a common device abstraction that doesn't hardcode a specific
  vendor pairing.
- Sweep a configured wavelength list, assemble a spectral cube, extract per-ROI
  extinction spectra, compute a metric, and feed a sensorgram — continuously, for the
  duration of an experiment.
- Run the existing pump/valve experiment-plan machinery alongside acquisition,
  unmodified in behavior, reused rather than duplicated.
- Record raw frames losslessly without the live view falling behind (the Lori SW bug,
  avoided by construction).

**Explicit non-goals for v1** (do not build these yet):
- HW-triggered (TTL sync) acquisition.
- Chromatic correction across wavelengths.
- Automated ROI/spot detection (manual ROI placement is enough for v1).
- Any camera vendor beyond Basler (IDS comes after the abstraction is proven).

---

## 2. Ecosystem change

```
LSPR Suite
├── apps/
│   ├── sLSPR/acq     — spectrometer-specific acquisition, now built on the shell
│   ├── sLSPR/eva
│   ├── LSPRi/acq     — NEW: camera + illumination acquisition, built on the shell
│   └── LSPRi/eva
└── packages/
    ├── lspr_core
    ├── lspr_io       — HDF5 schema extended with image + ROI groups (§10)
    ├── lspr_ui
    └── lspr_acq_shell — NEW: fluidics device framework, experiment-control runtime,
                          sensorgram widget, session/HDF5-writer base, diagnostics
```

`docs/architecture/overview.md`'s dependency rule ("`lspr_core`, `lspr_io`, `lspr_ui`
must not depend on each other or on any app package") is about those three specific
packages being pairwise-independent. `lspr_acq_shell` is a new, fourth shared package
that legitimately depends on all three — it's shared *application* code (Qt widgets,
device runtime), not a domain/IO/theme primitive, so it doesn't belong in any of the
existing three. Update `docs/architecture/general/dependency-matrix.md` to record this
exception explicitly when Phase 1 starts, so it doesn't look like an accidental
violation later.

---

## 3. Phase 0 — Performance validation spike

**Do this first, standalone, before touching any app architecture.** Goal: replace the
16 Hz / 5 MP assumption with a measured number, and find out whether the bottleneck (if
any) is capture, frame transport, or per-ROI compute.

Location: a throwaway script, not part of any app — e.g.
`C:\Users\Admin\AppData\Local\Temp\claude\...\scratchpad` or a personal scratch folder,
explicitly **not** committed into `apps/LSPRi/acq` since that doesn't exist yet.

What it measures:
1. Sustained Basler capture rate via `pypylon` at the target resolution/pixel format,
   free-running (no illumination switching yet) — establishes the camera's own ceiling.
2. Per-frame cost of the ROI extraction primitive (cached boolean mask + `image[mask].mean()`,
   the pattern already proven fast in LSPRimaging eva's `processing/roi.py`) at a
   realistic ROI count (ask: how many sample + reference ROIs do you actually expect
   per experiment? This number changes the compute budget a lot — 5 ROIs and 50 ROIs
   are different engineering problems).
3. Combined: capture + ROI extraction + a trivial extinction calculation, sustained for
   e.g. 30 seconds, watching for frame drops or growing backlog.

Output of this phase: a short note (append it to this document under a new "Phase 0
results" heading) stating achieved fps, per-frame ROI-compute latency at your real ROI
count, and whether a plain single-threaded loop suffices or a producer/consumer split
(camera thread → processing thread, like sLSPR acq's pattern) is needed even for v1.

This also answers a design question for §8: if per-ROI compute is cheap (likely, since
it's vectorized numpy), the sweep loop can plausibly stay simple; if it's not, the
processing stage needs its own thread from the start.

**Location correction**: this ended up living in `spikes/lspri_acq_phase0/benchmark_ui.py`
(a live-preview + timed-benchmark PyQt6 tool, not a headless script) rather than a
purely throwaway scratchpad file — it turned out worth keeping and rerunning as
settings changed, and it's a useful reference for the real acquisition worker's
capture-thread pattern (§8). Still not part of any app package.

### Phase 0 results

Camera: Basler a2A3840-45umBAS (USB3, native 3840×2160 ≈ 8.3MP). Target: 16 Hz.

**2026-08-07, run 1 — full resolution, buggy ROI extraction** (pixel format Mono10,
binning 1×1, 10 ROIs, 30s): 21.80 fps achieved, 0 late frames, but ROI extraction
avg **27.465 ms**, max 44.480 ms — almost the entire ~46ms frame period at that fps.
Root cause: the benchmark's first `extract_roi_means()` indexed each ROI with a
*full-image-sized* boolean mask (`image[full_size_mask]`), which is O(total image
pixels) per ROI regardless of ROI size — numpy has to scan every element of the mask
to gather the `True` positions. Fixed by cropping to each ROI's bounding box first,
then masking only that small sub-array (O(ROI area) per ROI). See the `RoiMasks`
docstring in `benchmark_ui.py` for the detail — **this lesson carries into the real
app's `processing/roi_extraction.py` (§7): use bounding-box-cropped local masks, not
full-image masks, no matter how the code gets there.**

**2026-08-07, run 2 — 2×2 binning, fixed ROI extraction** (pixel format Mono12,
binning 2×2 → 1920×1080 ≈ 2.1MP, exposure 1146µs, 10 ROIs, 30s): **48.73 fps**
achieved, 0 late frames, ROI extraction avg **0.448 ms**, max 2.087 ms. At a 16 Hz
budget of 62.5ms/frame, ROI extraction for 10 ROIs now costs under 1% of it — the fix
alone was roughly a 60× improvement, confirming the bug (not fundamental compute
cost) was the dominant factor in run 1.

**Interpretation so far**: at 2×2 binning, both capture (3× the target rate) and
per-frame ROI compute (negligible) have comfortable headroom. The camera's free-running
ceiling (~43 fps unbinned, per its own `ResultingFrameRate` node) was already above
run 1's 21.8 fps result even before any fix — meaning run 1's bottleneck was the
*consumption* side (the ROI bug, plus general Qt/display overhead), not the camera.

**2026-08-07, run 3 — full resolution, fixed ROI extraction** (Mono12, binning 1×1,
10 ROIs, 30s): **21.43 fps**, 0 late frames, ROI extraction avg **0.519 ms**, max
0.764 ms. This is the trustworthy full-res number run 1 couldn't provide. It matches
`DeviceLinkThroughputLimit` (360MB/s) ÷ (3840×2160×2 bytes for 10/12-bit unpacked) =
21.70 fps theoretical almost exactly — **full resolution is bandwidth-capped by the
USB3 throughput limit, not by anything in our code.** This also retroactively explains
the very first (pre-benchmark) observation of `ResultingFrameRate ≈ 43.4` at Mono8
(1 byte/pixel, so double the fps at the same bandwidth cap) — same formula, same
constant. Corollary: run 1's 27ms ROI bug was *not* actually the bottleneck at full
res — the USB3 link was already capping the frame period below where the bug would
have started causing drops. That was luck, not something to rely on at a higher ROI
count.

**2026-08-07, run 4 — 2×2 binning, 200 ROIs** (Mono12, 1920×1080, fixed ROI code,
30s): **48.70 fps** (unchanged from the 10-ROI binned run), 0 late frames, ROI
extraction avg **6.653 ms**, max 10.869 ms — a 20× ROI-count increase cost ~15× more
time (sub-linear), still under 11% of the 62.5ms/16Hz budget. Binned mode's 86.8 fps
theoretical bandwidth ceiling (same formula as above, smaller frame) is well above
the achieved 48.7 fps, meaning binned mode is **not** bandwidth-limited — something
else (most likely sensor readout time at that binning mode: frame period 20.5ms −
1.146ms exposure ≈ 19.4ms of readout/overhead) sets its ceiling instead.

**2026-08-07, run 5 — 2×2 binning, 150 ROIs, concurrent disk-write load** (Mono12,
1920×1080, dedicated `SaveWriterThread` per §8's design — plain `threading.Thread`
+ `queue.Queue`, writing real frame bytes to a bounded rotating set of files, 30s):
**48.77 fps** — unchanged from the no-disk-write case. **Max save-queue depth seen:
0** for the entire run — the writer thread never once fell behind. Write latency
avg 5.66ms, max 14.45ms, comfortably under the ~20.5ms inter-frame period even at
its worst. ~7.9GB written over 30s. **This empirically confirms §8's "save must never
block capture/display" assumption on real hardware, not just by design** — the
architecture's own answer to the exact Lori SW bug this project started by auditing
(2026-08-06 build-log entry) holds up under real, sustained disk I/O.

**Resolved — full spectral-cube sweep-cycle rate** (was: deferred, needs the real
LCTF/LED driver to measure). Two more pieces of Phase 0 work, one in a separate
session, closed this out: LCTF settle-time characterization and passband calibration
against the real VariSpec filter (`illumination_probe.py`), and a second camera vendor
(IDS uEye) added to `benchmark_ui.py` along with a real software-trigger-latency
measurement. Full detail and data in
[`spikes/lspri_acq_phase0/docs/camera_backend_and_throughput_findings.md`](../../../spikes/lspri_acq_phase0/docs/camera_backend_and_throughput_findings.md),
[`settle_time_analysis.md`](../../../spikes/lspri_acq_phase0/docs/settle_time_analysis.md),
and
[`lctf_passband_centroid_shift.md`](../../../spikes/lspri_acq_phase0/docs/lctf_passband_centroid_shift.md).
Headline conclusion, not a full combined end-to-end run but enough to answer the
question: **the LCTF's own settle time dominates the per-step budget, not camera
latency.** A single software-triggered capture (Mono12, native resolution, either
camera family) takes ~17-20ms; the LCTF's settle time is ~35-40ms in its best case
(small ascending steps) and up to ~80-90ms in its worst (descending, or a single
direction-agnostic safety margin) - camera latency is under half the filter's *best*
case and under a quarter of its worst. Per-wavelength-step budget for Phase 1's
sweep loop should be `LCTF settle margin (35-90ms, direction/step-size dependent) +
~20ms capture`, dominated by the first term; whole-sweep time is that × wavelength-step
count. Camera choice is therefore not a sweep-*speed* question between any of the
cameras evaluated so far - it's a science/imaging-quality one (resolution, pixel
size/SNR, shutter type).

**Phase 0 conclusion**: camera capture, ROI extraction (once the bounding-box fix
landed), concurrent disk writing, and now the full sweep-cycle rate question are all
confirmed comfortably capable of running well past any realistic target on this
hardware — full resolution alone clears the original 16Hz reference point by >30%,
2×2 binning clears it by ~3×, none of the three degrade each other when run together,
and the LCTF (not the camera) sets the real sweep-cycle ceiling regardless of which
evaluated camera is used. The goal, per the maintainer, was never a 16Hz pass/fail line
but "as fast as achievable" — on that framing, Phase 0's throughput questions are fully
answered and the remaining decisions (camera model, resolution/binning) are science
calls, not open engineering risk, heading into Phase 1.

**Working recommendation** (pending the maintainer's call — resolution/pixel-size/
shutter-type affect spatial precision and signal quality, a science question, not a
throughput one): maintainer's current call is to stay on the Basler a2A3840-45umBAS as
primary; 2×2 binning (≈2.1MP) gives ~3× headroom on capture rate vs. full res's ~1.3×,
negligible ROI cost even at 200 ROIs, and a smaller HDF5 footprint later, while full
resolution remains viable (21.4 fps still clears 16Hz) if spatial resolution turns out
to matter for ROI placement precision or signal quality. The IDS UI-3160CP-M-GL
Rev.2.1 was evaluated as a documented alternative (see the findings doc linked above)
- notably global-shutter (immune to the rolling-shutter/LED-PWM row-striping risk the
other two cameras carry) at the cost of much lower resolution (2.3MP vs 8.3/20MP) and
much larger pixels (4.8µm vs 2.0-2.4µm, better per-pixel SNR). Worth revisiting if
LED-PWM striping turns out not to be fully solved by longer exposure/lower intensity
in practice.

Other camera-level levers identified but not yet added to the tool (see chat/build
log for the full discussion): sensor ROI/ crop (ask: does the imaging area only cover
part of the sensor?), raising `DeviceLinkThroughputLimit` toward its ~419MB/s max,
packed pixel formats (`Mono10p`/`Mono12p`) if >8-bit precision is needed and
bandwidth turns out to matter, and `GrabStrategy_OneByOne` (vs. the tool's current
`LatestImageOnly`) to measure true lossless camera throughput rather than "safe
sustained display rate."

---

## 4. Phase 1 — Extract `lspr_acq_shell`

### 4.1 Ground truth: two in-repo plans this phase must build on, not duplicate

Before writing anything, read these — they change how this phase should be sequenced:

- **`apps/sLSPR/acq/docs/experiment-control/CODEX_EXPERIMENT_CONTROL_REUSE_SPLIT_V49.md`**
  — an existing plan for exactly the experiment-control extraction this phase needs,
  already partially implemented. It specifies a module split (shared visualization /
  runtime controller / device backend / IO), a capability-flag system
  (`devices_enabled`, `runtime_control_enabled`, `plan_import_export_enabled`,
  `show_runtime_buttons`, `show_device_columns`, `show_device_status_strip`,
  `show_step_navigation_controls`), and a public API
  (`set_capabilities`/`load_plan`/`save_plan`/`set_runtime_state`/`set_connected_devices`/
  `request_run`/`request_stop`/`request_pause`/`request_hold`/`request_step_next`/
  `request_step_previous`/`request_step_jump`). It targets reuse across sLSPR
  acquisition, sLSPR evaluation, and LSPRi evaluation — which already implies these need
  to live somewhere importable by separate git submodules, i.e. a shared package, even
  though the document itself doesn't name one. **This phase is "finish V49, and land the
  result in `lspr_acq_shell` instead of inside `apps/sLSPR/acq`."**
  Confirmed already in progress: `gui/experiment_control_backend.py` exists with an
  `ExperimentControlBackend` `Protocol` (`capabilities()`, `device_states()`,
  `is_device_connected()`, `refresh_devices()`, `send_command()`, `connect_device()`,
  `disconnect_device()`), a `NullExperimentControlBackend`, and
  `AcquisitionExperimentControlBackend` (sLSPR's concrete implementation, which wraps
  `ExperimentControlWindow` and — per its own docstring — is not yet the full split:
  "window's lifecycle operations... remain window-owned until the full V49 split
  lands"). `experiment_control_capabilities.py` and `experiment_control_controller.py`
  also already exist as separate files. The backend Protocol is exactly the seam LSPRi
  acq will implement its own concrete backend against (§7 below) — don't redesign it,
  finish extracting it.

- **`apps/sLSPR/acq/docs/device-layer/DEVICE_LAYER_AUDIT_2026.md`** and
  **`CODEX_DEVICE_LAYER_NUMBERED_LABELS_V51_IMPLEMENTATION.md`** — the fluidics device
  layer's real incident history. Read the whole audit file, not a summary — it documents
  roughly 30 real bugs found and fixed against real hardware in the last few weeks
  (dated through 2026-07-23), several of them genuinely subtle: an AMF vendor SDK that
  isn't thread-safe (fixed by routing all hardware I/O through a single-lane
  `device_io_pool()`), a two-lock design in `DeviceCommunicationService` (`_state_lock`
  for fast reads, `_lock`/`_device_lock` for hardware I/O, one-way nesting only, to
  avoid both deadlock and a GUI freeze bug that already happened once), per-instance
  port-claim ownership (`self._claim_owner = f"{controller_type}:{id(self)}"`, fixed
  after two different device types got this wrong in two different ways), and canonical
  device labels resolved via `ensure_device_profile()` rather than
  `find_or_create_profile()` (fixed after a stale duplicate profile silently stole a
  real device's identity — incident #31, "selector silently ignored plan-step move
  commands"). **This is not stable, quiescent code.** `DeviceLifecycleController`
  (`device_lifecycle.py`, pure Python, no Qt) plus `DeviceCommunicationService`
  (`device_manager.py`) are, as of the rewrite documented there, the single owner of
  discovery/connect/disconnect for spectrometer/pump/valve/selector — a real, hard-won
  architecture that took multiple rounds of real-hardware testing to get right. Treat
  it accordingly (§4.2).

  Also relevant: this audit explicitly discussed and **dropped** simulated
  pump/valve/selector devices, for reasons specific to those devices — no Windows
  virtual-COM-port pairing without an unsigned kernel driver, and the AMF selector
  bypasses `pyserial` entirely via a proprietary SDK. **This decision does not transfer
  to Camera/IlluminationSource** — see §4.4.

### 4.2 Risk framing: sequence the fragile piece last, move it verbatim, generalize separately

Given §4.1, the fluidics extraction is the highest-risk item in this phase, not a
routine file move. Two rules:

1. **Extract `device_manager.py`, `device_lifecycle.py`, `communication_models.py`,
   `serial_controllers.py`, `connection_registry.py`, and `port_assignments.py` as close
   to a mechanical relocation as possible** — same logic, same two-lock discipline, same
   single-lane `device_io_pool()`, same per-instance `_claim_owner` pattern, same
   `ensure_device_profile()`-based canonical-label resolution. The goal of this step is
   "still works exactly like before, just importable from `lspr_acq_shell`" — verified
   against real pump/valve/selector hardware afterward, not rewritten along the way.
2. **Do the registry generalization (the actual fix for the hardcoded `PUMP`/`SWITCH`/
   `SELECTOR` triad, so `Camera`/`IlluminationSource` can register into the same system)
   as a separate, distinct, reviewed step afterward** — once the verbatim move is
   confirmed stable. Don't bundle "move this fragile code" and "change this fragile
   code's structure" into one commit; §4.5 below is that second step.
3. Sequence the fluidics move **last** among the shell-extraction items (after settings,
   diagnostics, the HDF5-writer plumbing, sensorgram/session, and experiment-control) —
   by the time you get to it, the extraction pattern (how imports move, how tests get
   re-homed, how to verify against sLSPR acq afterward) will already be practiced on
   lower-risk code, and there will be a working shell to extract it into rather than
   being the first, riskiest thing moved into an empty package.

### 4.3 Extraction order

After **each** item: run `python -m pytest tests/`, launch sLSPR acq (Full and
Simulation profiles), and confirm nothing regressed before moving to the next item.

1. **Settings persistence pattern** — `lspr_settings.json`-style JSON read/write helpers
   currently in sLSPR acq's `storage/app_config.py`. Small, no device coupling.
2. **Diagnostics** — the off/normal/debug/deep verbosity-profile system, actually in
   top-level `diagnostics.py` (`DiagnosticsConfig`), not `gui/runtime_diagnostics.py`
   (that file is the diagnostics-*panel*'s content builder, deeply main-window-specific
   - see the extraction note below). Also the launch-profile env-var plumbing already in
   `lspr_core` (`LAUNCH_PROFILE_*`) - no relocation needed there, it's already reachable
   by every app and the suite launcher, though its *content* (profile labels,
   sensorgram/spectrometer-specific flags) is sLSPR-specific; LSPRi acq will define its
   own `LaunchProfileSpec` set later, not a Phase 1 concern.
   **Extracted 2026-08-07**: `diagnostics.py` moved to `lspr_acq_shell` as-is - confirmed
   zero sLSPR-specific assumptions (every env var it reads is suite-scoped, not
   app-prefixed). `gui/runtime_diagnostics.py`'s `SessionDiagnosticsSnapshot` and
   `gui/main_window_startup_diagnostics.py` do NOT move - unlike this profile/config
   layer, both are deeply coupled to sLSPR acq's specific main window internals
   (spectrum/trace/sensorgram plots, `_top_content_stack`) with no modality-agnostic
   seam. LSPRi acq will need its own diagnostics-panel content builder against its own
   window, reusing only `DiagnosticsConfig`.
3. **HDF5 async-writer base** — the threading/queue plumbing in `storage/hdf5_export.py`'s
   `AsyncHDF5MeasurementWriter` (tag-dispatch `append`/`append_metrics`/etc., same-process
   `threading.Thread` + `queue.Queue`). Leave the spectrum-specific dataset/group code
   (`HDF5MeasurementWriter`) behind in sLSPR acq.
   **Extracted 2026-08-07/08**: turned out the "plumbing" wasn't actually decoupled from
   the concrete writer - the original `_run()` hardcoded `HDF5MeasurementWriter(...)`
   construction and spectrum-shaped tags directly, so a literal move would have relocated
   an sLSPR-specific class, not a reusable base. Generalized into `lspr_acq_shell.AsyncTaggedWriter`
   (queue/thread/periodic-flush/close-draining/save-copy-ordering, generic across four
   structural tags - `flush`/`close`/`save_copy`/`timeout`) with three subclass hooks:
   `_open_writer()`, `_apply(writer, tag, payload)` (handle one non-structural queued
   item), `_flush_pending(writer)` (write out anything `_apply` batched). Confirmed with
   the maintainer before implementing - this is the seam LSPRi acq's cube writer builds
   against later (§8/§9's "new tags dispatched the same way append/metrics already are").
   `AsyncHDF5MeasurementWriter` is now a thin subclass with its exact original dispatch
   logic moved into those hooks; public API unchanged.
4. **Sensorgram plotting engine** — **corrected 2026-08-08**: `gui/plot_controller.py`
   and `gui/sensorgram_secondary_axis.py` turned out NOT to be a clean lift (37/54 and
   effectively all of their functions are window-coupled Qt orchestration, not
   curve-data-shaped logic) - genuine rewrite territory for Phase 2, like the ROI panel
   (§10). "Session/run bookkeeping" didn't name a real generic module either. What
   actually extracted cleanly was `gui/plot_view_cache.py` - not originally named here -
   a multi-resolution downsampling/caching engine, 28 of 30 functions pure numpy with
   zero window coupling. See the 2026-08-08 build-log entry for the full investigation.
5. **Finish V49 (experiment-control)** — **corrected 2026-08-08**: this was NOT
   "largely already scoped." Only `experiment_control_capabilities.py`
   (`ExperimentControlCapabilities`) and the `ExperimentControlBackend` Protocol +
   `NullExperimentControlBackend` in `experiment_control_backend.py` (~150 lines
   total) were genuinely ready and moved as-is to `lspr_acq_shell`.
   `AcquisitionExperimentControlBackend` stays behind in sLSPR acq (concrete
   sLSPR-specific implementation); LSPRi acq will write its own concrete class
   against the same Protocol (§7) once it has a panel to drive.

   Everything else V49 describes - splitting `experiment_control_window.py`
   (6,165 lines) and its eleven satellite files (`_editing.py`, `_timeline.py`,
   `_import.py`, `_dialogs.py`, `_widgets.py`, `_table.py`, `_plan_view.py`,
   `_step_runner.py`, `_runtime.py`, `_builders.py`, `_export.py` - ~11,000 lines
   combined) into a real shared visualization panel + decoupled controller + IO
   module, driven by capability flags instead of window reach-through - is
   **un-implemented planning**, not a near-done extraction, confirmed by reading
   V49's own doc (which explicitly says "do not treat as an implementation
   patch") and by inspection (`experiment_control_controller.py` still calls
   `window._toggle_experiment_control_run_hold()` etc. directly - not the
   "small public API... should not depend on the main window" V49 calls for).
   This is a multi-session project in its own right and is being tracked
   separately rather than folded into this Phase 1 checklist item.

   **Resolved, not carried forward**: `AcquisitionExperimentControlBackend
   .device_states()` iterated literal keys `("pump", "valve", "mswitch")`, but
   `device_label_for()` (`device_lifecycle.py`) looks device families up by exact
   key with no legacy-alias normalization of its own (`_normalize_device_type()`,
   which does have that aliasing, is a different function used for a different
   purpose in `device_manager.py`) - so `"valve"`/`"mswitch"` silently missed the
   registered `SWITCH`/`SELECTOR` families and fell back to a fabricated,
   never-matching label. Fixed to iterate the canonical `PUMP`/`SWITCH`/`SELECTOR`
   constants. Confirmed `device_states()` has zero live callers today (anticipatory
   V49 infrastructure, not yet wired to any UI) - a latent bug, not a currently
   visible one, but worth fixing while already touching this exact function
   rather than moving an unverified inconsistency into the shared package.
6. **Fluidics device framework** — verbatim move per §4.2, rule 1. Verify against real
   pump/valve/selector hardware before proceeding to §4.5.
   **Done 2026-08-08**: moved, with the real file set turning out to be 12 files, not
   the 6 named above (the transitive dependency closure - concrete drivers,
   `device_types.py`, `device_driver.py`, `connection_registry.py`,
   `probe_diagnostics.py` - all had to move together for `device_manager.py` to even
   import). The spectrometer-stage coupling in `device_lifecycle.py` (not mentioned in
   this section, found during implementation) was generalized into
   `register_primary_detector_stage()` - approved by the maintainer first, since it's a
   real design decision, not a mechanical relocation. Test-suite-verified equivalent to
   before (862/862); **real hardware verification against pump/valve/selector is still
   the maintainer's to do**, same as §4.5's registry generalization already noted.

### 4.4 Simulated devices — a different decision for imaging than for fluidics

The fluidics "no simulated devices" call (§4.1) was for reasons specific to pump/valve/
selector hardware, neither of which applies to Camera/IlluminationSource:

- VariSpec and the Lori-protocol LED array are both plain ASCII-over-serial — a
  `FakeSerial` test double at the `pyserial` boundary (the same idea the device-layer
  audit considered and rejected *only* because AMF bypasses `pyserial` entirely) is
  realistic here and should be built for both.
- Basler's pylon SDK ships an emulated-camera device, enabled via the `PYLON_CAMEMU`
  environment variable — worth using directly in `SimulatedCamera` if it behaves as
  documented; confirm against the actually-installed pylon version before relying on
  it, since this hasn't been verified in this environment.

So `apps/LSPRi/acq`'s device layer should follow the `SimulatedSpectrometer` pattern
sLSPR acq already uses for its spectrometer (§11), even though the fluidics layer in the
same app deliberately does not have simulated pump/valve/selector devices. That's not an
inconsistency to resolve — it's the correct call in both cases, for different reasons.

### 4.5 The registry generalization (after §4.3 item 6 is verified stable)

Concrete before/after, using the exact current shapes:

- **Before**: `PortRefreshData(generation, pump_ports, valve_ports, selector_devices,
  amf_tools_available)` — a frozen dataclass with fixed fields
  (`communication_models.py:47-53`). `DEVICE_ORDER: tuple[str, ...] = (PUMP, SWITCH,
  SELECTOR)` (`device_lifecycle.py:47`), and `_discover_and_connect()`
  (`device_lifecycle.py:431-436`) is a three-way `if device_key == PUMP: ... if
  device_key == SWITCH: ... else: # selector` dispatch to
  `_discover_and_connect_pump`/`_discover_and_connect_valve`/`_discover_and_connect_selector`.
- **After**: `PortRefreshData` carries `ports_by_family: dict[str, list[object]]` instead
  of three named fields. `DEVICE_ORDER` becomes a registration list populated by a
  `register_device_family(key: str, discover_and_connect: Callable[[Sequence[object], EmitFn], DeviceLifecycleEvent])`
  call per family, made once at controller construction — pump/switch/selector register
  through the same call new families (camera, illumination) will use, so no new
  hardcoded branches are needed for Phase 2's device layer.
- **Preserve exactly, do not simplify away**: the two-lock discipline in
  `DeviceCommunicationService` (one-way nesting only — the I/O lock may acquire the
  state lock, never the reverse); the single-lane `device_io_pool()` requirement for any
  vendor SDK not proven thread-safe; per-instance `_claim_owner = f"{type}:{id(self)}"`;
  and canonical-role resolution via `ensure_device_profile()` rather than
  `find_or_create_profile()` for any device this app treats as a fixed singleton role
  (exactly the bug class in incident #31 — a stale duplicate profile silently owning
  the real device under the wrong label — would recur for Camera/IlluminationSource if
  this rule isn't followed).
- Whether Camera/IlluminationSource in LSPRi acq need their **own** single-lane pool or
  can share `device_io_pool()`'s pattern is a decision, not an assumption: they're
  unrelated hardware in a different app/process from sLSPR's fluidics, so a separate
  pool is the natural default — but if Phase 0's spike or early driver testing shows
  `pypylon` or the serial drivers misbehave under concurrent access (the same class of
  problem the AMF SDK had), apply the same defensive single-lane pattern rather than
  assuming "simpler protocol" means "thread-safe."

After Phase 1, sLSPR acq should be **functionally identical** to before, just assembled
from `lspr_acq_shell` + its own spectrometer-specific device/domain/panel code, verified
against real hardware, not just the test suite. That equivalence is the acceptance
criterion for this phase — not a rewrite, a relocation (plus one deliberate, separately
reviewed generalization step at the very end).

---

## 5. Phase 2 — New app scaffold: `apps/LSPRi/acq`

Mirrors sLSPR acq's package layout so the pattern stays familiar:

```
apps/LSPRi/acq/
├── pyproject.toml          — depends on lspr_core, lspr_io, lspr_ui, lspr_acq_shell
├── src/main.py
└── src/lspri_acq_app/
    ├── app.py
    ├── device/
    │   ├── camera_base.py          — Camera ABC (§6)
    │   ├── basler_camera.py        — pypylon implementation
    │   ├── simulated_camera.py     — for tests, mirrors sLSPR's SimulatedSpectrometer
    │   ├── illumination_base.py    — IlluminationSource ABC (§6)
    │   ├── variSpec_lctf.py        — VariSpec serial driver, from the manual
    │   ├── lori_led_array.py       — LED-array driver, protocol from Lori SW audit
    │   └── simulated_illumination.py
    ├── domain/
    │   ├── models.py               — Frame, SpectralCube, ImagingAcquisitionSettings (§9)
    │   ├── roi.py                  — AreaRoi/AreaRoiGroup, ported from LSPRi eva
    │   └── extinction.py           — absorbance_from_means, metric/fit functions
    ├── processing/
    │   └── roi_extraction.py       — cached-mask vectorized ROI mean extraction, ported
    ├── gui/
    │   ├── main_window.py          — assembles shell panels + new imaging panels
    │   ├── image_view_panel.py
    │   ├── roi_panel.py
    │   └── image_processing_panel.py  — crop/rotate/background-flatten only, v1
    └── storage/
        └── image_writer.py         — extends lspr_io's HDF5 schema (§10)
```

Launcher wiring: `apps/suite_launcher/src/suite_launcher/targets.py` already has the
`lspri_acq` `AppTarget` (currently `enabled=False`, `note="Coming soon."`). Once the app
has a working entry point, flip `enabled=True` and point `root_candidates`/`script` at
the real paths — no new launch-profile mode needed, this reuses the existing card.

---

## 6. Device layer

Two ABCs, registered into the generalized device registry from Phase 1 §4 as new
families alongside `PUMP`/`SWITCH`/`SELECTOR`.

```python
class Camera(ABC):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def configure(self, settings: CameraSettings) -> None: ...  # exposure, gain, pixel format
    def acquire_frame(self, timeout_ms: int) -> Frame: ...       # single synchronous grab, v1
    def device_name(self) -> str: ...
    def capabilities(self) -> CameraCapabilities: ...            # resolution, max fps, trigger modes

class IlluminationSource(ABC):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def set_wavelength(self, nm: float) -> None: ...
    def current_wavelength(self) -> float | None: ...
    def wavelength_range(self) -> tuple[float, float] | None: ...   # None for discrete-channel devices
    def settle_time_ms(self) -> float: ...                          # how long to wait after set_wavelength
    def device_name(self) -> str: ...
```

**Important asymmetry discovered from the two real protocols available**: VariSpec is
*continuously tunable* (`W <nm>`, any value in range) while the Lori LED array is
*discrete-channel* (`CONF_LED_CURRENT=<channel>,<value>` — a fixed set of LED channels,
each with a nominal wavelength, no continuous tuning). `set_wavelength(nm)` on a
channel-based device should resolve to "nearest configured channel," with the concrete
driver owning a channel→nm table. Don't design the ABC assuming continuous tuning
everywhere — that would misrepresent how LED arrays actually work.

### 6.1 Registering into the generalized device registry

LSPRi acq gets its **own** `DeviceCommunicationService`/`DeviceLifecycleController`
instance (imported from `lspr_acq_shell`, post-§4.5 generalization) — not a shared
instance with sLSPR acq, since these are separate app processes potentially running on
different host PCs with different attached hardware. That instance registers **five**
device families through the same `register_device_family()` call: `PUMP`, `SWITCH`,
`SELECTOR` (unchanged, reused as-is if this app also drives fluidics — confirm this
against your actual setup, since the plan so far assumes it does, per the original
request to reuse "already implemented flow control system"), plus new `CAMERA` and
`ILLUMINATION` families. Each new family's `discover_and_connect` callback follows the
same shape as the existing pump/valve/selector ones (probe candidates, rank them,
connect the best match, emit `DeviceLifecycleEvent`s) — `Camera`/`IlluminationSource`
implementations plug into lifecycle management (BUSY state during a sweep, canonical
label resolution via `ensure_device_profile()`, single-lane pool if warranted per §4.5's
last point) for free, rather than needing their own bespoke connect/disconnect code path.

### 6.2 Implementing LSPRi acq's `ExperimentControlBackend`

Per §4.3 item 5, `ExperimentControlBackend` (the `Protocol` from `lspr_acq_shell`) is
already generic — `device_key: str` isn't hardcoded to fluidics roles in the interface
itself, only in sLSPR's concrete `AcquisitionExperimentControlBackend`. LSPRi acq writes
its own concrete class (e.g. `ImagingExperimentControlBackend`) that:
- Implements `device_states()` by iterating whichever fluidics device keys this app's
  `DeviceCommunicationService` instance actually has registered (pump/switch/selector,
  if reused) — camera/illumination status is a separate concern, surfaced through the
  image-view/ROI panels' own status display, not through the experiment-control panel,
  since that panel's job (per V49) is fluidics plan execution, not imaging device state.
- Sets `ExperimentControlCapabilities` appropriately for an acquisition app (full
  runtime controls, device columns, status strip — the same capability profile sLSPR
  acq uses, not the restricted evaluation-app profile V49 also defines).
- Delegates `send_command`/`connect_device`/`disconnect_device` to this app's own
  service instance, exactly mirroring `AcquisitionExperimentControlBackend`'s shape.

**VariSpec LCTF driver** (`variSpec_lctf.py`) — protocol is fully specified in the
manual you provided (`1794348.pdf`, now worth saving into
`apps/LSPRi/acq/docs/manuals/` once the app exists, matching sLSPR acq's
`docs/manuals/` convention):
- USB-virtual-COM, ASCII, `<c/r>` terminated, one-letter commands.
- Startup: filter self-initializes on power-up (<1s for current-gen USB units); issue
  `I 1` only if idle >8h or temperature drifted >3°C (per manual, §"Initialize").
- Use **Brief format** (`B 1`) to cut per-command overhead — the manual explicitly
  describes this as existing for exactly this purpose.
- `W <nm> <cr>` to tune; `W ? <cr>` to query current wavelength (confirms tuning
  completed — poll or just trust the documented 50-150ms response time and the `!`
  busy-check character before declaring settled).
- Error handling: after any command, an error puts the Status LED red and records a
  code (Table 5 in the manual) queryable via `R ?`; clear with `R 1`. The driver should
  surface this as a raised exception with the decoded error meaning, not swallow it.
- `settle_time_ms()` → 50ms for VIS-range filters, 150ms for NIR-range, per the
  manual's operating specifications table — read the actual model's spec rather than
  hardcoding one value.

**LED-array driver** (`lori_led_array.py`) — protocol reconstructed from the audited
Lori SW source (`Form1.cs` "LED driver control" region), two serial ports (`MASTER`/`SLAVE`):
- `CONF_LED_CURRENT=<channel>,<value_mA>` — set a channel's drive current.
- `CONF_LED_CURRENT_LIMIT=<channel>,<limit_mA>` — safety ceiling per channel.
- `CONF_STEP=<ch>,<ch>,<delay>,<pulse>` — timing config (appears to configure per-channel
  pulse delay/width; confirm exact semantics before relying on it — this was inferred
  from usage, not from a spec document).
- `START_EXTERN_TRIGGERED_MASTER`/`START_EXTERN_TRIGGERED_SLAVE`, `STOP` — this
  existing system is actually HW-triggered by design (LEDs step on an external pulse).
  For v1's SW-triggered mode, the new driver should NOT use this trigger mode — issue a
  one-shot pulse/enable per channel under direct software control instead. Confirm this
  is possible with the real controller (may need a different command not visible in the
  decompiled subset) before committing to it.
- This driver is explicitly a **second reference implementation**, not necessarily the
  production driver for whatever LED hardware you actually pair with the new app later
  — its value here is validating that the `IlluminationSource` ABC works for a
  discrete-channel device, not VariSpec-shaped continuous tuning alone.

**Generic LED controller** — placeholder only until you provide the protocol doc for
the actual device being paired with this build. Don't guess at its command set.

**Basler camera driver** (`basler_camera.py`) — `pypylon` (confirmed installed).
Straightforward: `pylon.TlFactory`, `InstantCamera`, `GrabOne`/`RetrieveResult` for v1's
synchronous single-frame acquire. Grayscale per your hardware — request `Mono8`/`Mono12`
pixel format explicitly rather than trusting a default.

---

## 7. Domain model

```python
@dataclass(slots=True)
class Frame:
    image: np.ndarray            # 2D grayscale, dtype matches camera bit depth
    wavelength_nm: float
    acquired_at: datetime
    metadata: dict

@dataclass(slots=True)
class SpectralCube:
    frames: list[Frame]          # one per swept wavelength, in sweep order
    cube_index: int               # increments once per completed sweep
    started_at: datetime
    completed_at: datetime

@dataclass(slots=True)
class ImagingAcquisitionSettings:
    wavelengths_nm: list[float]   # the sweep list
    exposure_us: float
    gain: float | None
    settle_time_override_ms: float | None   # None = use illumination.settle_time_ms()

@dataclass(slots=True)
class AbsorbanceSpectrumResult:
    roi_id: int
    wavelengths_nm: np.ndarray
    absorbance: np.ndarray
    cube_index: int
```

`AreaRoi`/`AreaRoiGroup` — ported verbatim (they're already Qt-free `@dataclass(slots=True)`)
from `apps/LSPRi/eva/src/lspr_imaging_app/domain/models.py`. Use the current names, not
the `DetectedSpot`/`SpotGroup` aliases — that rename is still in-progress in the source
app, no reason to import the legacy names into a new app.

---

## 8. Acquisition pipeline (v1, SW-triggered)

This is where the Lori SW bug is the explicit design constraint: **saving must never
share a thread with anything that has to keep pace with the next frame.**

```
Sweep controller thread (one per running experiment):
  for wavelength in acquisition_settings.wavelengths_nm:
      illumination.set_wavelength(wavelength)
      sleep(settle_time_ms)
      frame = camera.acquire_frame(timeout_ms=...)
      cube_builder.add(frame)
  cube = cube_builder.finalize()          # one SpectralCube
  processing_queue.put_latest(cube)        # display/eval — drop stale, like sLSPR acq's rule
  save_queue.put(cube)                     # lossless — dedicated writer thread, own queue

Save writer thread (dedicated, like the Lori SW fix):
  for cube in save_queue:                 # GetConsumingEnumerable-equivalent, drains on shutdown
      write_cube_to_hdf5(cube)             # never blocks the sweep controller

Processing thread (separate from both):
  for cube in processing_queue:            # latest-only — a slow processing cycle should
      for roi_pair in configured_rois:      # skip stale cubes, not queue them up
          spectrum = extract_roi_spectrum(cube, roi_pair)   # cached-mask vectorized means
          metric = compute_metric(spectrum)  # centroid / peak / fit
          sensorgram.append_point(roi_pair.id, cube.completed_at, metric)
      display.show_latest(cube)             # image view — latest frame of the cube, not every frame
```

Three independent threads/queues, matching sLSPR acq's proven pattern (separate
acquisition/processing, latest-only display, lossless recording queue) — but unlike
sLSPR acq's current implementation, **do not** route the lossless queue through
`multiprocessing.Queue` pickling from the start. Image-sized payloads make that
transport choice expensive (audited finding: pickling a 5MP frame repeatedly through
`mp.Queue` doesn't scale the way it does for KB-sized spectra). Start with same-process
`threading.Thread` + `queue.Queue` (cheaper for in-process numpy arrays, no pickling)
and only move to shared memory / a separate process if Phase 0's measurements show the
GIL contention between capture and processing actually matters in practice — don't
build the more complex version speculatively.

`put_latest` for the processing queue — reuse or reimplement the same "replace pending
item instead of enqueueing" pattern documented in sLSPR acq's `gui/workers.py`
(`_queue_put_latest`) — this is exactly the "lossy is OK for display, never for raw
recording" rule from `runtime_pipeline_architecture.md`, applied to cubes instead of
spectra.

---

## 9. Storage

**Revised 2026-08-08 — images are NOT stored in HDF5.** The original version of
this section put raw frames in an HDF5 `/raw/cubes/{cube_index}/frames` dataset,
matching sLSPR acq's all-in-one-file convention. Maintainer's explicit call:
split storage by kind, not by file-format convenience —

- **HDF5** (via `lspr_io`, the same mature, versioned schema sLSPR acq uses —
  `LSPR_MEASUREMENT_SCHEMA_VERSION`, not LSPRi eva's separate JSON-integer
  convention) holds **experimental data only**: device status/inventory,
  spectra-shaped values, sensorgram points, ROI definitions — the same kinds of
  things sLSPR acq already stores there. Additive schema groups, minor version
  bump per `docs/schemas/hdf_standard.md`:
  - `/processed/roi_definitions` — `AreaRoi`/`AreaRoiGroup` snapshot at
    experiment start.
  - `/processed/absorbance_spectra/{roi_id}` — `(None, n_wavelengths)`, appended
    once per completed cube.
  - `/processed/sensorgram/{roi_id}` — `(None, 2)` (timestamp, metric value),
    same shape family as sLSPR acq's existing sensorgram storage.
  - Device inventory / status — reuse `lspr_io`'s existing device-inventory
    writer path, same as sLSPR acq.
  - Not yet built (§12's remaining checklist item) — the async-writer plumbing
    from Phase 1 §4 item 3 is the intended seam (`AsyncTaggedWriter` subclass,
    new tags dispatched the same way `"append"`/`"metrics"` already are).
- **Image frames** go to a **separate, user-selectable** image store — either a
  TIFF stack or an OME-Zarr dataset, both real options, not one hardcoded
  default (this is an experimental app; customization matters more than a
  single "right" answer here). **Built 2026-08-08** —
  `storage/image_writer.py`'s `TiffCubeWriter`/`OmeZarrCubeWriter`, both
  implementing a common `ImageCubeWriter.write_cube(cube) -> int` (bytes
  written) protocol that plugs directly into `SaveWriterThread` (§8). See the
  2026-08-08 build-log entry and
  [`spikes/lspri_acq_storage_benchmark/storage_format_benchmark_findings.md`](../../../spikes/lspri_acq_storage_benchmark/storage_format_benchmark_findings.md)
  for the real write-throughput/compression measurements behind the defaults:
  - `TiffCubeWriter` — one file per frame, named `WL<wavelength>Frame<cube_index>.tif`,
    the *exact* filename convention LSPRimaging Evaluation's reader already
    parses (`IMAGE_PATTERN` in `apps/LSPRi/eva/src/lspr_imaging_app/io/dataset.py`)
    — no eva-side change needed to read this app's output.
  - `OmeZarrCubeWriter` — grows a real zarr v3 array one cube at a time (zarr's
    own API for array/metadata structure; the same hand-rolled shard/index/
    CRC32C byte format eva's batch exporter uses for the pixel data itself,
    bypassing zarr's slow async per-chunk write path). Also writes the same
    `lspr` attrs group eva's exporter does, updated after every cube, so a
    dataset from an interrupted experiment stays readable for whatever cubes
    did complete, and eva's own fast-read path works against it. **Proven
    compatible with eva's actual reader**, not just "valid zarr" — see
    `tests/integration/test_lspri_acq_zarr_compat.py` (umbrella-level, since
    it's specifically about the integration point between the two apps):
    `lspr_imaging_app.io.dataset.load_ome_zarr_dataset()` +
    `load_image_array()`, unmodified, correctly read back pixel-perfect data
    written by this app's writer, for both shard modes and with/without
    compression.
  - `StorageSettings` (format/compression/compression_level/shard_mode/
    chunk_size_px) is the full user-choosable surface — no settings UI built
    yet (a later GUI item), but every field is wired through
    `build_image_writer()` already.
  - `SaveWriterThread` (§8) now tracks live save-lag metrics (queue depth,
    write latency, bytes written — `SaveWriterThread.stats()`), the same
    pattern already validated in the Phase 0 spike's own `SaveWriterThread` —
    so a user can see, on their own hardware, whether their chosen
    format/compression/resolution is actually keeping up, rather than trusting
    a number measured on a different machine. No GUI display of this yet
    (also a later item).

---

## 10. GUI panels

- **Image view**: live display, latest-frame-of-latest-cube only (never blocks on the
  processing/save threads). A `pyqtgraph.ImageView`-based widget is a reasonable
  starting point for a fast-updating grayscale image at this frame size — verify
  redraw cost against Phase 0's numbers before committing.
- **ROI panel**: manual placement/editing only for v1 (no auto-detection).
  **Corrected 2026-08-08**: eva's `domain/roi_editor_tools.py` is NOT "reusable
  as-is" as this item originally claimed — checked before assuming so, and every
  clamp/move/clone function there is built around `RoiDefinition` (rectangle/
  ellipse, `size_x`/`size_y`), a different type from this app's `AreaRoi`
  (sample disk + reference annulus, `sample_radius_px`) — not directly
  reusable. Only `build_grid_positions()` there is genuinely generic (no
  `RoiDefinition` dependency), and it isn't needed for v1's manual-placement-
  only scope. Built a fresh, `AreaRoi`-shaped equivalent instead:
  `apps/LSPRi/acq/src/lspri_acq_app/domain/roi_editor_tools.py`
  (`clamp_center_to_image`, `move_roi`, `next_area_roi_id`, `roi_outer_radius_px`
  — the last one because clamping must account for the reference annulus's
  outer edge, not just the sample disk). The interaction/overlay code around it
  (`ImageInteractionController`, `OverlayManager`) is still Qt-coupled to eva's
  specific `MainWindow` and still needs a genuine rewrite for this app's panel,
  not a port — that part of the original claim held up.
- **Image processing panel**: crop/rotate/background-flatten only for v1, using the
  vectorized functions from LSPRi eva's `processing/preprocess.py` (`apply_preprocessing`,
  `flatten_background`) as a starting point — these are already numpy/scipy-vectorized,
  no Qt dependency, safe to port.
- **Sensorgram + spectrum panels**: corrected 2026-08-08 (§4.3 item 4) — only the
  multi-resolution cache/downsampling *engine* (`lspr_acq_shell.PlotViewCache`) is
  reused directly; the actual Qt plotting/panel code (`plot_controller.py`,
  `sensorgram_secondary_axis.py`) turned out to be deeply main-window-coupled with no
  clean seam, same as the ROI panel below — LSPRi acq needs its own sensorgram panel
  built fresh, feeding data through the shared cache engine rather than porting the
  panel code itself.
- **Experiment control panel**: reused directly from `lspr_acq_shell` — pump/valve plan
  editing and execution UI, unchanged.

---

## 11. Testing strategy

- **Unit, no Qt/no hardware**: ROI mask/mean extraction, `absorbance_from_means`,
  metric/fit functions, HDF5 schema read/write round-trip. These should be fast and
  numerous.
- **Simulated devices**: `SimulatedCamera` (returns synthetic frames, e.g. a Gaussian
  spot pattern with configurable noise) and `SimulatedIllumination` (instant
  `set_wavelength`, zero settle time) — mirrors `SimulatedSpectrometer`'s existing role
  in sLSPR acq's test suite. This is what lets `tests/integration/` exercise the full
  sweep → cube → extinction → sensorgram pipeline without any hardware present, same as
  the rest of the suite's "all tests pass without real hardware" rule.
- **Golden-path smoke test**: one complete sweep with simulated devices and 2-3 ROI
  pairs, asserting a sensorgram point is produced with a sane value — this is the test
  that would have caught a threading bug like the Lori SW one, since it exercises save +
  display + processing concurrently.

---

## 12. Delivery milestones (the TODO list)

This is the actual, living TODO list — check items off as they're done, add new ones
as they're discovered, and don't let it drift from reality. Each item that's done
should have a matching dated entry in
[`lspri_acq_build_log.md`](lspri_acq_build_log.md) explaining what was actually done
and what, if anything, is still outstanding (e.g. "done, but not verified against
real hardware") — the checkbox alone doesn't carry enough information for someone
picking this up cold.

### Phase 0 — Performance spike

- [x] Camera connected 2026-08-07; spike tool built (`spikes/lspri_acq_phase0/benchmark_ui.py`).
- [x] Sustained Basler capture rate measured, full-res and 2×2 binned.
- [x] Per-frame ROI-extraction cost measured (10, then 200/150 ROIs) — a real
      O(image size)-per-ROI bug found and fixed along the way (see §3 results and
      the 2026-08-07 build-log entries).
- [x] Concurrent disk-write load tested — confirmed the save-writer thread never
      blocks capture/display, empirically, not just by design (run 5, §3).
- [x] Results appended to this document (§3) and to the build log.
- [x] LCTF settle time characterized against real VariSpec hardware (792 optically-
      measured transitions, direction-aware margins) — see §3 and
      `spikes/lspri_acq_phase0/docs/settle_time_analysis.md`.
- [x] LCTF passband calibration measured (61-point optical sweep, offset correction
      table produced) — see `spikes/lspri_acq_phase0/docs/lctf_passband_centroid_shift.md`.
- [x] `benchmark_ui.py` generalized to a second camera vendor (IDS uEye via `pyueye`,
      behind a `CameraBackend` interface) and evaluated against the two Baslers — see
      `spikes/lspri_acq_phase0/docs/camera_backend_and_throughput_findings.md`.
- [x] Software-trigger single-shot capture latency measured on real hardware (~17-20ms) —
      same doc as above.
- [x] Full spectral-cube sweep-cycle rate question closed: not one combined end-to-end
      run, but both halves (LCTF settle time, camera trigger latency) independently
      measured on real hardware, conclusively showing the LCTF dominates and camera
      choice isn't a sweep-speed bottleneck — see §3.

### Phase 1 — Extract `lspr_acq_shell`

- [x] Read V49 (`CODEX_EXPERIMENT_CONTROL_REUSE_SPLIT_V49.md`) and the device-layer
      audit (`DEVICE_LAYER_AUDIT_2026.md`, `CODEX_DEVICE_LAYER_NUMBERED_LABELS_V51_IMPLEMENTATION.md`)
      in full (§4.1). *(2026-08-06)*
- [x] Registry generalization (§4.5) done **in place in `apps/sLSPR/acq`**, ahead of
      extraction — see the 2026-08-06 build-log entry for exactly what changed and
      what tests cover it. Real pump/valve/selector hardware re-verification still
      outstanding; do that before relying on it for a real experiment.
- [x] `packages/lspr_acq_shell` scaffolded (empty package: `pyproject.toml`,
      `README.md`, `version.py`, `__init__.py`), installs and imports cleanly, added
      to `requirements.txt`. *(2026-08-06)* **Nothing extracted into it yet.**
- [x] `dependency-matrix.md` updated with the `lspr_acq_shell` entry and the
      pairwise-independence-exception note. *(2026-08-06)*
- [x] `overview.md`'s ecosystem map updated. *(2026-08-06)*
- [x] **1.3.1 — Settings persistence pattern** extracted from `storage/app_config.py`
      into `lspr_acq_shell` (`settings_store.py`) — done, umbrella + pyflakes clean,
      sLSPR acq launched (Simulation) with no errors. `user_profile.py` extracted
      alongside it (not originally scoped to this item, but coupled — see the
      2026-08-07 build-log entry for why and for the app-scoping generalization
      this required).
- [x] **1.3.2 — Diagnostics** extracted — done, but scope corrected from what
      this item originally described. The actual "off/normal/debug/deep"
      profile system is `diagnostics.py` (`DiagnosticsConfig`, top-level, not
      `gui/`) - genuinely app-agnostic, confirmed zero sLSPR-specific
      assumptions, moved as-is. `gui/runtime_diagnostics.py` (~1200 lines,
      `SessionDiagnosticsSnapshot`) and `gui/main_window_startup_diagnostics.py`
      turned out to be deeply coupled to sLSPR acq's specific main window
      (spectrum/trace/sensorgram plot internals) with no modality-agnostic
      seam - correctly left behind, not extracted. See the 2026-08-07
      build-log entry for the full reasoning and for a note on
      `lspr_core/launch_profiles.py`'s content also being sLSPR-specific
      (left in place, not this item's problem to solve).
- [x] **1.3.3 — HDF5 async-writer plumbing** extracted — done, but required
      real generalization, not a mechanical move (the original class hardcoded
      `HDF5MeasurementWriter(...)` construction and spectrum-shaped tags
      directly in its run loop, so a literal move would have relocated an
      sLSPR-specific class). New generic `AsyncTaggedWriter` base in
      `lspr_acq_shell` (queue/thread/flush-timing/close-draining/
      save-copy-ordering) with three subclass hooks (`_open_writer`,
      `_apply`, `_flush_pending`); `AsyncHDF5MeasurementWriter` is now a thin
      subclass implementing those hooks with its exact original tag
      dispatch. Public API to its 2 real callers unchanged. Confirmed with
      the maintainer before implementing, since this defines the seam
      LSPRi acq's future cube writer builds against (§8/§9). See the
      2026-08-07/08 build-log entry.
- [x] **1.3.4 — Sensorgram plotting engine** extracted — done, but scope
      corrected significantly from this item's original description. Investigated
      first: `plot_controller.py` (37/54 functions window-coupled) and
      `sensorgram_secondary_axis.py` (Qt widget/menu building) are GUI-panel
      code with no clean seam, same situation as the ROI panel precedent in
      §10 ("needs a genuine rewrite, not a port") - confirmed with the
      maintainer, left both entirely in sLSPR acq. "Session/run bookkeeping"
      didn't name a real generic module either (`domain/session.py`'s
      `MeasurementSession` is dark/reference/absorbance-spectrum math,
      correctly sLSPR-specific; the session GUI files are window-coupled
      action handlers) - nothing extracted there. What *did* extract cleanly:
      `gui/plot_view_cache.py` (not named in this item originally) - a
      1600-line multi-resolution downsampling/caching engine where only 2 of
      30 functions touched the window; the other 28 (`PlotViewCache`,
      `MetricDisplayCache`, compression-block building, peak-preserving
      decimation) are pure numpy and moved as-is. See the 2026-08-08
      build-log entry.
- [x] **1.3.5 — partial**: moved only what was genuinely ready
      (`experiment_control_capabilities.py`'s `ExperimentControlCapabilities`,
      `experiment_control_backend.py`'s `ExperimentControlBackend` Protocol +
      `NullExperimentControlBackend` - ~150 lines total, zero window coupling).
      **Corrected scope, confirmed with the maintainer**: the "finish V49" framing
      was wrong by an order of magnitude - the real migration this implies is
      ~11,000 lines across 15 files (`experiment_control_window.py` alone is 6,165
      lines), none of it split yet despite the plan's "largely already scoped"
      description. That's un-implemented planning (V49's own doc says so
      explicitly), not a near-done extraction, and is not something to fold into
      a single Phase 1 checklist item - tracked as its own future project instead.
      `AcquisitionExperimentControlBackend` and `experiment_control_controller.py`
      (still window-reach-through, not yet the decoupled controller V49 describes)
      stay in sLSPR acq. The `("pump","valve","mswitch")` vs. canonical
      device-type-key mismatch **was** resolved (fixed to iterate
      `PUMP`/`SWITCH`/`SELECTOR`) - confirmed it had zero live callers today, so
      this was a latent bug, not a visible one. See the 2026-08-08 build-log entry.
- [x] **1.3.6 — Fluidics device framework moved** into `lspr_acq_shell` — done,
      scope grew from the plan's 6 named files to **12** once the real dependency
      closure was traced (`device_manager.py` transitively requires the concrete
      pump/selector/valve drivers - `amf_mswitch.py`, `reglo_icc.py`,
      `valve_controllers.py` - plus `device_types.py`, `device_driver.py`,
      `connection_registry.py`, `probe_diagnostics.py` - to even import). Same
      two-lock discipline, same `device_io_pool()`-adjacent comments, same
      per-instance `_claim_owner` pattern, same `ensure_device_profile()`-based
      canonical-label resolution — preserved exactly, not restructured. One real
      generalization, approved by the maintainer before implementing:
      `device_lifecycle.py`'s hardcoded spectrometer stage (which directly
      imported `OceanSpectrometer`, a backwards dependency for a shared package)
      became `register_primary_detector_stage(key, run_stage)`, the same
      registration idiom as `register_device_family()`; sLSPR acq registers its
      spectrometer stage at import time in its own shim, behavior unchanged.
      `ACTIVE_PUMP_CHANNELS`/`VALID_ROLLER_COUNTS`/`DEFAULT_ROLLER_COUNT` moved
      from `domain/pump_plan.py` to `reglo_icc.py` alongside this (pump-hardware
      facts, not plan-execution facts). Also resolved the `("pump","valve",
      "mswitch")` device-type-key mismatch already fixed in 1.3.5. See the
      2026-08-08 build-log entry for the full investigation, the one bug found
      and fixed while moving (a `DeviceLifecycleReport.spectrometer=` constructor
      kwarg break, caught by the test suite and fixed at its one call site), and
      the false-alarm "Windows fatal exception" traced to a pre-existing Qt/
      Windows quirk unrelated to this change (confirmed by reproducing it against
      the unmodified code via `git stash`).
- [x] After every 1.3.x item: `python -m pytest tests/` green, sLSPR acq launches
      (Full and Simulation profiles) with no regression, before moving to the next.
      Phase 1 (1.3.1-1.3.6) complete on this basis.
- [ ] After Phase 1 completes: sLSPR acq confirmed **functionally identical** to
      before (the acceptance criterion for this whole phase, §4.3). Test-suite-level
      equivalence confirmed (862/862, same pre-existing flake); **real pump/valve/
      selector hardware re-verification is still outstanding** - not possible from
      this environment, needed before relying on this for a real experiment. Same
      caveat the 2026-08-06 registry generalization already carried forward.

### Phase 2 — New app scaffold: `apps/LSPRi/acq`

- [x] App scaffold created (`pyproject.toml`, `src/main.py`, package layout per §5),
      depends on `lspr_core`/`lspr_io`/`lspr_ui`/`lspr_acq_shell`. *(2026-08-08)* New
      GitHub repo (`lednicky-t/LSPRimaging-Acquisition`) created and wired in as a
      submodule at `apps/LSPRi/acq`, matching the other three apps. Minimal main
      window boots and renders correctly (screenshot-verified) but has no real
      acquisition content yet - see the 2026-08-08 build-log entry.
- [x] `Camera`/`IlluminationSource` ABCs + `SimulatedCamera`/`SimulatedIllumination`
      + unit tests (§4.4, §6). *(2026-08-08)* `capabilities()`/`settle_time_ms()` made
      abstract (no default), unlike `lspr_acq_shell.Spectrometer`'s precedent - see the
      build-log entry for why a default doesn't transfer safely to these two. 9 unit
      tests, no Qt/no hardware.
- [x] `Camera` and `IlluminationSource` both registered as device families into the
      generalized registry from `lspr_acq_shell` (§6.1). *(2026-08-08)* Found and
      fixed a real gap first: the plan assumed
      `DeviceCommunicationService.connect()` was already generic (only *discovery* was,
      from the earlier registry generalization - construction was still a hardcoded
      three-way `reglo_icc`/`amf-mswitch`/valve-detect dispatch). Confirmed with the
      maintainer before implementing: added an additive `register_driver_connect_factory()`
      to `device_manager.py` (same idiom as `register_device_family()`, for the
      construction step), inserted before the valve catch-all branch (which would
      otherwise have silently swallowed any new driver key). See the build-log entry,
      including a real test-isolation finding (`register_device_family()` mutates
      process-global state - this app's tests must not run in the same pytest
      invocation as the umbrella `tests/` suite).
- [ ] `LspriAcqExperimentControlBackend` (renamed from this item's original
      `ImagingExperimentControlBackend` - "Imaging" was flagged by the maintainer
      as an unclear adjective, 2026-08-09) implemented against the shared
      `ExperimentControlBackend` Protocol (§6.2). **Confirmed with the maintainer**:
      this app *does* drive the same pump/valve/selector fluidics system as sLSPR
      acq, and should reuse the *same* experiment-control panel with full
      functionality, not a separate/reduced one - this resolves §6.1's "confirm
      this against your actual setup" open question. Given the panel is ~11,500
      lines (`experiment_control_window.py` + 14 satellite files) with real
      safety-critical logic (decides what commands actually go to the pump/valve/
      selector), a real research pass (per-file window-coupling numbers, not
      guesses) produced a 4-tier extraction/rewrite plan - see the 2026-08-09
      build-log entry for the full breakdown and the maintainer's approval of the
      sequencing. **Tier 0 (pure, zero window coupling, already tested) done
      2026-08-09**: `pump_plan.py` (the `PumpPlanStep` domain model - a real
      dependency all four Tier 0 GUI files share, not originally scoped as part
      of "Tier 0" until traced) plus `experiment_control_runtime.py`/
      `_export.py`/`_import.py`/`_step_runner.py` moved to `lspr_acq_shell`,
      verbatim except one generalization (`to_core_experiment_plan()` no longer
      imports `APP_VERSION` from `lspr_app.version` directly - see the build-log
      entry). **Tier 1 (Qt-heavy but window-decoupled widgets) done 2026-08-09**:
      `experiment_control_timeline.py` (792 lines, `PumpPlanTimelineWidget` -
      custom-painted zoom/pan/drag-reorder timeline) and
      `experiment_control_widgets.py` (287 lines, `ExperimentControlTableView`/
      `PlanColorDelegate`/`TubeDiameterComboBox`/etc.) moved to `lspr_acq_shell`
      verbatim except import repointing (both already depended only on
      `pump_plan.py` and, for the timeline, `device_lifecycle.py`/
      `device_types.py` - all three already in `lspr_acq_shell` from Tier 0 -
      confirmed by tracing real usage, not by trusting the file's own docstring
      claim of self-containedness). sLSPR acq keeps thin re-export shims at the
      old paths; screenshot-verified sLSPR acq still renders the table and
      timeline correctly post-move (this tier moved actual custom-painting
      code, so a visual check mattered, not just passing tests). Tiers 2-3 (the
      actual run/hold/pause/stop state machine and step-command decision logic,
      which needs real redesign, not just a move; the window-specific dialogs/
      editing, a rewrite candidate) not started - Tier 2 needs the maintainer's
      real-hardware sign-off before either app relies on a refactored version.
      **Tier 2 started 2026-08-09**: traced real coupling (only `_plan_step_commands()`,
      ~160 lines, decides hardware commands - the actual dispatch was already
      shared in Tier 0; the run/hold/pause/stop timer loop is genuinely
      window-entangled, with its guard flags read at 250+ other sites).
      Maintainer chose to share the state machine too (not just the decision
      function), given LSPRi acq's own run loop needs sweep-pipeline hooks
      anyway. Given only 10 pre-existing tests covered this logic, maintainer
      chose to write thorough characterization tests against the current,
      unmodified state machine first - see the 2026-08-09 build-log entry for
      the 53-test suite (mutation-tested for real, not just run) that is now
      the safety net for the actual extraction, still to come.
- [x] Basler driver (`pypylon`) built *(2026-08-08)* - **not yet manually verified
      against real hardware** (no Basler camera was attached in the environment this
      was written in; confirmed 0 devices via real `pylon.TlFactory.EnumerateDevices()`
      calls, which is what the unit tests exercise). Pixel-format/binning/exposure
      handling mirrors the Phase 0 spike's real-hardware-verified `PylonBackend`;
      software-trigger sequencing and single-shot `GrabOne`-style acquisition are new
      and unverified. Verify end-to-end against physical hardware before relying on it.
- [x] VariSpec driver built *(2026-08-08)* - **not yet manually verified against real
      hardware** (no VariSpec unit was attached in the environment this was written
      in). Protocol read directly from the manual (not just this plan's paraphrase) -
      found the manual's echo-then-reply framing and the error-persists-until-cleared
      rule were more subtle than a naive read of §6.2 suggested; both are hardware-
      verified via the Phase 0 spike's real bug history (see the build-log entry) even
      though this specific driver class hasn't itself run against the unit yet.
      settle_time_ms() uses the Phase 0 empirical, direction-aware measurement, not
      the manual's generic "50-150ms" figure (that figure covers the whole product
      family, not this unit - see the build-log entry for why the two aren't the
      same claim). 13 unit tests against a fake serial port modeling the real echo
      framing. **Registered as the ILLUMINATION device family** *(2026-08-08,
      continued)* - needed a safe port-discovery strategy first (a serial LCTF looks
      like any other "USB Serial Device," unlike Basler's vendor-SDK enumeration).
      Built one rather than guessing: `_candidate_illumination_ports()`
      (`device/registry.py`) excludes ports manually assigned to another role
      (`get_port_assignment(port) != "auto"`) and ports currently claimed by a live
      connection (`port_owners(port)`) - deliberately *not*
      `should_probe_port_for_role()`, which was traced and found to silently return
      `True` (no restriction) for any role outside its hardcoded `{"pump","switch"}`
      pair, so it would have looked like a safety check while providing none for
      "illumination". `discover_varispec_port()` (`variSpec_lctf.py`) then opens each
      safe candidate and only accepts one whose `V ?` reply actually parses as a
      plausible VariSpec identity (`wavelength_range() is not None`), not just "the
      open() call didn't raise" - `open()` alone doesn't validate a foreign device's
      reply, by design (matches every other driver's connect-time leniency). Manual
      (`1794348.pdf`) copied into `apps/LSPRi/acq/docs/manuals/` *(2026-08-08, done -
      relocated from `spikes/lspri_acq_phase0/` per this item's own note)*.
- [ ] Lori-protocol LED driver (reference implementation) — confirm `CONF_STEP`
      semantics and software-triggered single-pulse capability against real
      hardware before trusting it; this is explicitly a reference, not necessarily
      the production driver for whatever LED hardware ends up paired with this app.
- [x] Domain model (`Frame`, `SpectralCube`, `AreaRoi`/`AreaRoiGroup` ported from
      LSPRi eva, `AbsorbanceSpectrumResult`, `ImagingAcquisitionSettings`) — §7 (fixed
      from this item's own "§9" typo - §9 is the HDF5 schema section, §7 is Domain
      model). *(2026-08-08)* Built to the exact shapes in §7; `AreaRoi`/`AreaRoiGroup`
      ported field-for-field, current names only.
- [x] Sweep controller + three-thread pipeline (§8) built and tested against
      simulated devices — same-process `threading.Thread`/`queue.Queue`, not
      `multiprocessing.Queue`. *(2026-08-08)* `SweepController`/`SaveWriterThread`/
      `ProcessingThread` (`acquisition/sweep_pipeline.py`) - lossless save queue is
      unbounded, processing queue is `maxsize=1` (latest-only, via a ported
      `_queue_put_latest`, sLSPR acq's own drop-oldest idiom adapted from
      `multiprocessing.Queue` to `queue.Queue`). One real fix found before this was
      ever run: a persistently-failing camera would have spun `_run_one_sweep()` in
      a tight retry loop with no delay, hammering the hardware and flooding logs -
      added a `stop_event.wait()`-based backoff (interruptible by `stop()`, not a
      plain `time.sleep()`), with a dedicated regression test proving both the
      backoff *and* that `stop()` doesn't have to wait one out.
      `processing/cube_processing.py` ties ROI extraction + extinction math together
      per cube, reporting results via a callback rather than owning a sensorgram
      data structure (no sensorgram GUI panel exists yet, §10).
- [ ] HDF5 schema extension in `lspr_io` (§9) — experimental data only (device
      status, spectra, sensorgram, ROI definitions), minor version bump, changelog
      entry. Not built yet.
- [x] Image storage: TIFF stack and OME-Zarr writers (§9). *(2026-08-08)* Both
      built, both real user-selectable options (`StorageSettings`), real-measured
      (not guessed) against a live-write-shaped benchmark - see the build-log
      entry and `spikes/lspri_acq_storage_benchmark/`. Proven compatible with
      LSPRimaging Evaluation's actual reader (umbrella-level cross-app test), not
      just "valid zarr." `SaveWriterThread` now tracks live save-lag metrics.
      **Not yet built**: a settings UI to actually choose format/compression, a
      live display of the save-lag metrics, and the recompress-after-acquisition
      fallback path (designed, not implemented).
      `SaveWriterThread.write_cube` is already the injection point this will plug
      into (`storage/image_writer.py` doesn't exist yet).
- [x] GUI: image view (latest-frame-only) + ROI panel (manual placement only for
      v1) — §11. *(2026-08-08)* `gui/image_view_panel.py` (`pyqtgraph.ImageView`,
      same display-orientation convention validated in the Phase 0 spike) +
      `gui/roi_panel.py` (draggable/resizable `pg.CircleROI` per sample disk,
      static reference-annulus overlay, add/delete/list, numeric reference-
      diameter editing) - real screenshot-verified, wired into `main_window.py`
      against a `SimulatedCamera` startup preview. A real GC-timing crash
      ("Windows fatal exception: access violation" during garbage collection,
      from unparented `RoiPanel` instances holding pyqtgraph scene-graph items)
      was found and fixed in the test suite itself, not dismissed - see the
      build-log entry. **Not yet built**: minimal image-processing panel (crop/
      rotate/background-flatten), and no live sweep is wired to this view yet
      (still shows one static startup preview frame, not a running acquisition).
- [x] Unit tests for ROI/extinction/metric math (no Qt, no hardware) — §12.
      *(2026-08-08)* `processing/roi_extraction.py` (bounding-box-cropped
      `RoiMaskSet`/`RoiMaskCache` per the Phase 0 lesson - explicitly *not* a port of
      LSPRimaging Evaluation's own `processing/roi.py`, which still has the
      O(image-size) masking bug Phase 0 found and fixed, and extracts against a
      different ROI type entirely) and `domain/extinction.py`
      (`absorbance_from_means`, `peak_absorbance`, `centroid_wavelength` - the
      latter reimplemented simply from singleLSPR Acquisition's
      `centroid_from_curve()` concept, not that function's fuller
      threshold/legacy-mode parameter set, which this app doesn't need yet).
      32 unit tests total.
- [x] End-to-end smoke test with simulated devices: sweep → cube → extinction →
      sensorgram point. *(2026-08-08)* 2 ROIs, exercises save + processing
      concurrently against `SimulatedCamera`/`SimulatedIllumination` - asserts both
      ROIs produce a finite (non-NaN) sensorgram point and multiple cubes are saved
      losslessly.
- [ ] Launcher: `lspri_acq` target in `targets.py` flipped to `enabled=True`.
- [ ] Real-hardware end-to-end run; sensorgram cross-checked against the Lori SW's
      own output for the same sample if available, since it's a working reference.

---

## 13. Open items — need your input before the corresponding step can start

- LED controller protocol for the device you'll actually pair with this build (separate
  from the Lori-protocol reference driver above).
- Expected sample + reference ROI count per experiment (changes Phase 0's compute
  budget and whether the processing stage needs its own thread even at v1 scope).
- IDS camera SDK choice (`pyueye` vs. IDS's newer Python SDK) — not needed until the
  second camera vendor is actually being added.
- Wavelength list characteristics (how many steps, spacing, VIS vs NIR range) — affects
  VariSpec `settle_time_ms()` defaults and expected sweep duration.
