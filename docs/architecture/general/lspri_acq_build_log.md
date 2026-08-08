# LSPRimaging Acquisition — Build Log

Dated, append-only record of what has actually been done on this project, and why.
This is a history/decision record, not a task list — for the forward-looking task
list see the "Delivery milestones" section of
[`lspri_acq_architecture_and_shared_shell_plan.md`](lspri_acq_architecture_and_shared_shell_plan.md).

**Why this file exists**: this project spans multiple sessions and will very likely
be picked up by a different AI agent instance (with no memory of prior sessions)
partway through. A future reader — human or agent — should be able to read this
file top to bottom and understand what exists, what was tried, what was rejected
and why, without needing to reconstruct it from chat history. Follow the style of
`apps/sLSPR/acq/docs/device-layer/DEVICE_LAYER_AUDIT_2026.md` — specific, dated,
names real files/functions, states *why* a decision was made, not just what.

**Rule for future entries**: append, don't rewrite history. If something described
below turns out to be wrong or gets superseded, add a new dated entry saying so and
pointing back at the old one — don't silently edit the old entry. Update the plan
doc's own content (the target design) freely; this file is the log of how we got
there.

---

## 2026-08-05: Architecture decision — separate app, shared shell, not a bolt-on

Discussed building `LSPRimaging Acquisition` as a mode of `singleLSPR Acquisition`
vs. a fully separate, unrelated app vs. a separate app sharing a real code layer.
Audited both `apps/sLSPR/acq` and `apps/LSPRi/eva` (agent-assisted, see chat history
for the full audit reports — not reproduced here). Key findings that drove the
decision:

- sLSPR acq's device layer was hardcoded to a `PUMP`/`SWITCH`/`SELECTOR` triad in
  multiple places (`device_lifecycle.py`'s `DEVICE_ORDER`/`_discover_and_connect`,
  `communication_models.py`'s `PortRefreshData`) — adding camera/illumination would
  have meant new hardcoded branches, not a plug-in.
- `MainWindow` is a ~4,137-line god object (200+ instance attributes, per the
  project's own tech-debt notes) with no panel-registry/composition boundary.
- `gui/workers.py`'s live-acquisition pipeline pickles every payload through
  `multiprocessing.Queue` — fine for KB-sized spectra, not proven to scale to
  5 MP camera frames at 16 Hz.
- Despite all of the above, the *majority* of sLSPR acq's ~53k lines (fluidics
  control, experiment-plan execution, sensorgram, HDF5 async-writer plumbing) is
  not spectrometer-specific at all — it's generic "run a live flow-cell experiment"
  infrastructure that LSPRimaging acquisition would also need, unchanged.

**Decision**: extract the modality-agnostic majority into a new shared package
(landed as `packages/lspr_acq_shell`, see 2026-08-06 entries) and build LSPRimaging
Acquisition as its own app on top of it, rather than either (a) bolting imaging onto
sLSPR acq directly, or (b) building a second app with no shared code at all.

## 2026-08-06: Lori control SW audit — used as a design input, not adopted as-is

Audited `C:\Users\Admin\Documents\GitHub\Lori control SW` (a working, C#/WinForms
camera+LED control app for one specific rig) at the maintainer's request, since it's
a real, hardware-proven reference for the acquisition sequence this project needs
(set illumination wavelength → grab frame → repeat, building a spectral cube →
per-ROI extinction → sensorgram). Found and fixed (in that C# codebase, a separate
project, not part of LSPR-Suite) a confirmed root-cause bug: saving ran synchronously
inside the same loop that drove live-view display, so a slow disk write blocked the
next frame's display — the live view fell further behind the longer acquisition ran.
Fixed there with a dedicated save-writer thread/queue, decoupled from display.

**Why this matters for LSPRi acq's design** (see the architecture plan's §8,
"Acquisition pipeline"): this is the exact failure mode to design around from day
one — saving must never share a thread with anything that has to keep pace with the
next frame. The plan's three-thread pipeline (sweep/capture, dedicated save writer,
processing) is deliberately shaped by this finding, not by guesswork. The Lori SW's
LED serial protocol (`CONF_LED_CURRENT=<channel>,<value>`, `CONF_STEP=...`,
`START_EXTERN_TRIGGERED_MASTER/SLAVE`) was also extracted as a second, discrete-
channel reference `IlluminationSource` implementation, to make sure the device ABC
doesn't accidentally assume every illumination source is continuously tunable like
VariSpec (see the architecture plan §6).

## 2026-08-06: VariSpec LCTF manual received; architecture plan written

Maintainer supplied the VariSpec LCTF User's Manual (CRi, `1794348.pdf`). Full
architecture and build plan written:
[`lspri_acq_architecture_and_shared_shell_plan.md`](lspri_acq_architecture_and_shared_shell_plan.md).
Covers: Phase 0 (throughput spike), Phase 1 (shared shell extraction), Phase 2 (new
app scaffold), device layer design (`Camera`/`IlluminationSource` ABCs), the
acquisition pipeline, domain model, HDF5 schema extension, GUI panels, and testing
strategy. V1 scope decisions (confirmed with maintainer): SW-triggered acquisition
only for v1 (HW/TTL-sync deferred), shared-shell extraction happens *before* the new
app is built against it, and the throughput spike happens before any of this is
built.

Went deeper on Phase 1/2 at the maintainer's request and found two existing,
directly-relevant in-repo plans that the naive extraction plan would have
duplicated or contradicted:

- `apps/sLSPR/acq/docs/experiment-control/CODEX_EXPERIMENT_CONTROL_REUSE_SPLIT_V49.md`
  — an already-partially-implemented plan for exactly the experiment-control split
  this project needs (module split, capability flags, a `Protocol`-based backend
  seam — `gui/experiment_control_backend.py` already exists). Phase 1's
  experiment-control step is now "finish V49, land the result in `lspr_acq_shell`,"
  not a new design.
- `apps/sLSPR/acq/docs/device-layer/DEVICE_LAYER_AUDIT_2026.md` — ~30 real,
  hardware-verified concurrency bugs fixed in the fluidics device layer over the
  prior few weeks (thread-unsafe AMF SDK, a deliberate two-lock design, per-instance
  port-claim ownership, canonical-label resolution). This reframed the fluidics
  extraction from "routine file move" to "move verbatim, verify against real
  hardware, generalize separately" (architecture plan §4.2).

Maintainer pushed back on an overly-cautious initial characterization of the
fluidics layer as "unstable" — correctly: the audit's own status shows every
Red/Orange/Yellow item fixed, only two Blue/roadmap *features* left open. Corrected
framing: the caution is about *how* to touch hard-won, concurrency-sensitive code
(don't restructure while relocating it), not a claim that it's currently broken.

## 2026-08-06: Device registry generalization (in sLSPR acq, ahead of extraction)

Maintainer asked to do the `PUMP`/`SWITCH`/`SELECTOR` → registry generalization now,
directly in `apps/sLSPR/acq` (before the Phase 1 shell extraction gets to it), as
groundwork. Full investigation (every call site of `PortRefreshData`, `DEVICE_ORDER`,
`_DEVICE_DRIVER`/`_DEVICE_ROLE`/`_DEVICE_LABEL`, and the existing regression tests in
`tests/unit/test_device_lifecycle.py`) done before touching anything, per the
"check it well first" instruction.

Implemented: `DeviceFamily` dataclass + `register_device_family()`/
`device_family_order()` registry in `device_lifecycle.py`, replacing every hardcoded
branch. `PortRefreshData` (`communication_models.py`) now carries
`ports_by_family: dict[str, list[object]]`, with `pump_ports`/`valve_ports`/
`selector_devices` kept as backward-compatible read-only properties. Pump/switch/
selector registered as the three built-ins, each a thin wrapper around the existing,
untouched `_discover_and_connect_pump`/`_valve`/`_selector` methods. The two-lock
`DeviceCommunicationService` design, `device_io_pool()`, and per-instance
`_claim_owner` pattern were **not** touched.

Verified: full umbrella test suite (861 passed, 1 pre-existing unrelated flaky HDF5
test confirmed to pass in isolation — `test_async_writer_reports_failure_via_on_error_callback`
in `tests/integration/test_acq_hdf5.py`, a Windows temp-dir-cleanup race, nothing to
do with this change), `apps/sLSPR/acq/tests/test_device_manager_locking.py` (7/7),
`tests/unit/test_device_lifecycle.py` (37/37, 3 call sites updated to the new
`PortRefreshData` shape), pyflakes clean on all touched files. `DEVICE_ORDER` and
`device_label_for()` confirmed to resolve to byte-identical values as before
(`('pump', 'switch', 'selector')`, `pump_1`/`switch_1`/`selector_1`).

**Not done**: real hardware verification (pump/valve/selector connect/disconnect on
actual devices) — not possible from the environment these sessions run in. Do this
before relying on it for a real experiment.

Committed: submodule `apps/sLSPR/acq` at `6cd9eb3` ("Generalize pump/switch/selector
device dispatch into a registry"), umbrella repo at `001f05b` ("Bump sLSPR/acq
submodule: generalize device dispatch into a registry"). Both commits are local
only — not pushed.

## 2026-08-06: Phase 0 deferred; Phase 1 started — `lspr_acq_shell` package scaffolded

No camera available today to run the Phase 0 throughput spike (planned for
tomorrow). Maintainer asked to move to Phase 1 in the meantime, and to set up a
durable structure/documentation/TODO convention for this project so future work
(including future AI agent sessions) stays organized and findable — this file and
the "Delivery milestones" section of the architecture plan are the result.

Scaffolded `packages/lspr_acq_shell` — empty package (`pyproject.toml`, `README.md`,
`version.py`, `__init__.py`), following the exact structure of `packages/lspr_core`.
Verified it installs (`pip install -e packages/lspr_acq_shell`) and imports cleanly.
Added to `requirements.txt`. Updated `docs/architecture/general/dependency-matrix.md`
(new package entry + the pairwise-independence-exception note) and
`docs/architecture/overview.md`'s ecosystem map to reflect it.

**Nothing has been extracted into the package yet** — it is a structurally-correct
empty shell. The first real extraction (settings persistence, the lowest-risk item
in the architecture plan's §4.3 order) has not started.

**Not done / not committed**: none of today's `lspr_acq_shell` scaffolding or doc
updates have been committed. They're new/untracked files in the umbrella repo
(`packages/lspr_acq_shell/`, `docs/architecture/general/lspri_acq_build_log.md`) plus
edits to `requirements.txt`, `dependency-matrix.md`, `overview.md`, and the
architecture plan doc.

## 2026-08-07: Phase 0 run — real camera, real bug found and fixed

Camera arrived: Basler **a2A3840-45umBAS**, USB3, native 3840×2160 (≈8.3MP — larger
than the "5MP" figure used in earlier planning). `pypylon` was not yet in the venv;
installed. Confirmed via direct node-map query: Mono8/10/10p/12/12p pixel formats,
hardware binning 1–4× (H and V independently, Sum/Average mode), sensor min/max
Width/Height/Offset, `DeviceLinkThroughputLimit` currently 360MB/s (max ~419MB/s),
free-running `ResultingFrameRate` ≈43 fps at default settings — establishing the
camera itself was very unlikely to be the bottleneck at a 16Hz target.

Built `spikes/lspri_acq_phase0/benchmark_ui.py` — a small PyQt6 tool (live preview via
`pyqtgraph.ImageView`, not a headless script) rather than a bare console benchmark,
so the maintainer could visually confirm focus/exposure while real numbers were being
measured. Capture runs on a `QThread` (`CameraGrabThread`), mirroring the separation
the real acquisition worker will need (§8 of the architecture plan). Controls: pixel
format, binning, exposure time, ROI count (synthetic grid of circular ROIs), a "Run
30s timed benchmark" button producing a summary formatted for direct paste into the
architecture doc.

**First run** (full res, Mono10, 10 ROIs): 21.80 fps (target 16Hz, so nominally
"passing"), but ROI extraction cost averaged **27.465ms** — almost the whole frame
period. Investigated rather than accepted: the ROI-extraction code indexed each ROI
with a full-image-sized boolean mask, which is O(total image pixels) per ROI, not
O(ROI area) — numpy scans the entire mask array to gather `True` positions regardless
of how few there are. **Fixed** by switching to bounding-box-cropped local masks (crop
to each ROI's small bounding box first, then mask only that sub-array). This is the
same lesson to carry into the real app's ROI-extraction code later (§7 of the plan) —
flagged explicitly there and in the plan's Phase 0 results section so it isn't
silently reintroduced.

Also added, at the maintainer's request (interested in whether full 8.3MP is
necessary or 2-4MP would suffice): binning and exposure-time controls in the tool.
Confirmed the camera supports genuine hardware binning (not a software downsample),
which reduces resolution while keeping full field of view — different from a sensor
crop, which keeps full pixel density but narrows the FOV (also discussed as an
available option, not yet added to the tool).

**Second run** (2×2 binning → 1920×1080 ≈2.1MP, Mono12, 10 ROIs, fixed ROI code):
**48.73 fps** achieved (3× the 16Hz target), 0 late frames, ROI extraction avg
**0.448ms**, max 2.087ms — roughly a 60× improvement over run 1, confirming the bug
(not fundamental per-ROI compute cost) was the dominant factor. Full details and the
still-open follow-up tests (full-res-with-fixed-code, higher ROI count, concurrent
disk-write load) are in the architecture plan's "Phase 0 results" section, not
duplicated here — this entry is the narrative, that section is the living data table.

**Not done**: full-resolution retest with the fixed ROI code (run 1's 21.8fps number
is confounded by the bug and shouldn't be treated as "the real full-res ceiling");
higher-ROI-count test; concurrent-disk-write test; sensor-ROI-crop, packed-pixel-format,
and throughput-limit controls discussed but not added to the tool. None of today's
spike code or doc updates have been committed.

## 2026-08-07 (continued): full-res retest + 200-ROI test — camera/ROI throughput conclusively cleared

Two follow-up runs closed out the open items from the previous entry:

**Run 3** (full res, Mono12, fixed ROI code, 10 ROIs): 21.43 fps, ROI extraction avg
0.519ms. This is the trustworthy full-res number the buggy run 1 couldn't provide.
Worked out the exact explanation rather than leaving it as an unexplained number:
`DeviceLinkThroughputLimit` (360MB/s) ÷ frame size (3840×2160×2 bytes for unpacked
10/12-bit) = 21.70 fps theoretical, matching the measured 21.43-21.80 fps across both
full-res runs almost exactly. Same formula at Mono8 (1 byte/pixel) gives 43.40 fps,
which matches the very first pre-benchmark `ResultingFrameRate` reading exactly.
**Conclusion: full resolution is hard-capped by the USB3 throughput limit, not by any
of our code.** Corollary worth remembering: run 1's 27ms ROI bug was not actually the
thing limiting run 1's fps - the bandwidth cap already capped the frame period below
where the bug would start causing real drops. Lucky, not a reason to have left the bug
in place - it would have started mattering at a higher ROI count or slightly heavier
per-frame work.

**Run 4** (2×2 binning, 200 ROIs, fixed code): 48.70 fps (unchanged from the 10-ROI
binned run), ROI extraction avg 6.653ms, max 10.869ms - a 20x ROI-count increase cost
~15x more time (sub-linear), still under 11% of the 62.5ms/16Hz budget. Binned mode's
own bandwidth ceiling (86.8 fps by the same formula) is well above what's achieved, so
binned mode is capped by something else (most likely sensor readout time at that
binning mode, not data volume).

**Important scope clarification surfaced while interpreting these**: every run so far
is free-running continuous capture with no illumination switching. The real v1
acquisition design (§8: set wavelength → wait settle time → grab frame, per step) has
a completely different rate-limiter - full spectral-cube sweep-cycle time, dominated
by illumination settle time × wavelength-step count, not camera fps. Camera+ROI
throughput is now conclusively not a bottleneck in either binning mode; the actual
sweep-cycle rate remains untested and needs the real LCTF/LED driver (Phase 2) to
measure - flagged in the plan doc rather than left implicit, so "camera clears 16Hz"
isn't later mistaken for "the full sweep repeats at 16Hz."

**Given the maintainer's stated interest in whether full 8.3MP is necessary**: working
recommendation is 2×2 binning (~2.1MP) as the sensible default - 3x capture headroom
vs. full res's 1.3x, negligible ROI cost even at 200 ROIs, smaller HDF5 footprint
later. Full resolution remains viable if spatial resolution/signal quality turns out
to require it - explicitly a science call for the maintainer, not decided here.

**Still not done**: concurrent-disk-write load test (validating "save must not block
capture/display" empirically); sensor-ROI-crop, packed-pixel-format, and
throughput-limit controls discussed but not added to the tool; nothing from today
committed yet.

## 2026-08-07 (continued): goal correction, disk-write test, Phase 0 throughput questions closed

**Goal correction from the maintainer**: 16Hz was never a hard requirement - the
actual goal is "as fast as achievable." Updated the benchmark tool's labels and the
plan doc's language to stop presenting 16Hz as a pass/fail line; it now appears only
as a historical reference point (the "late frame" diagnostic in the tool still uses
it as a comparison period, but that's just a convenient fixed yardstick, not a target).

Added a `SaveWriterThread` to `benchmark_ui.py` — plain `threading.Thread` +
`queue.Queue` (not `QThread`, deliberately matching §8's "same-process thread/queue,
not multiprocessing" recommendation), writing real frame bytes into a bounded
rotating set of 20 files (so a long run doesn't fill the disk) and reporting queue
depth + write latency back to the UI. Added a "Also write frames to disk" checkbox
and wired it into `_on_frame`. Added `.gitignore` for the scratch write folder.

**Run 5** (2×2 binning, 150 ROIs, disk-write load on, 30s): 48.77 fps (unchanged from
the no-disk-write case), **max save-queue depth seen: 0** for the entire run, write
latency avg 5.66ms/max 14.45ms (well under the ~20.5ms inter-frame period), ~7.9GB
written. **This is the empirical confirmation the architecture plan's §8 needed** -
"save must never block capture/display" now holds up under real, sustained disk I/O
on real hardware, not just as a design intent. Directly closes the loop on the Lori
SW bug this whole project's device-layer thinking started from (2026-08-06 entry
above): the failure mode there (save sharing a thread with display, backlog growing
silently) is the exact thing this test was built to catch, and it didn't happen here.

One methodological note worth remembering: the tool's "MB written" figure is
cumulative from when capture starts, not scoped to just the 30s benchmark window - if
capture ran for a while before clicking the benchmark button, that number overstates
the benchmark-window total. Doesn't affect the queue-depth conclusion (unambiguous
either way), just not a clean per-30s figure like the other numbers.

**Phase 0's throughput-validation goal is now met**: camera capture, ROI extraction,
and concurrent disk writing have each been measured with real hardware and don't
bottleneck each other. The only remaining Phase 0-adjacent question - full
spectral-cube sweep-cycle rate under real illumination switching - needs the LCTF/LED
driver and is correctly deferred to Phase 2, not blocking further work. Maintainer
confirmed access to the VariSpec LCTF manual content is available for designing that
test once the hardware is connected (separate session).

**Not done**: sensor-ROI-crop, packed-pixel-format, and throughput-limit controls
discussed but not added to the tool (not needed - Phase 0's questions are answered
without them); nothing from today (including the disk-write feature) committed yet.

## 2026-08-07 (continued): LCTF settle time and passband calibration measured (separate session, backfilled here)

The illumination-side half of the deferred "full spectral-cube sweep-cycle rate"
question, using `illumination_probe.py` (same `spikes/lspri_acq_phase0/` family as
`benchmark_ui.py`) against the real VariSpec VIS filter (400-720nm, serial 52366).

**Settle time**: 792 optically-measured transitions (tab 6's batch sweep, five step
sizes, both directions, three-plus repeats each). Key finding: *direction* predicts
settle time much better than *step size* does - ascending small steps need only
~35-40ms margin (p99 28ms), while descending small steps need ~80ms (p99 57ms, max
72ms); the two don't even have the same rank order across step sizes. Full detail,
methodology, and the recommended direction-aware two-tier margin in
`spikes/lspri_acq_phase0/docs/settle_time_analysis.md`.

**Passband calibration**: 61-point optical spectral sweep (420-720nm, 5nm steps,
dark-subtracted). Half-max centroid vs. commanded wavelength: mean shift -0.31nm (std
0.51nm), no wavelength-dependent trend (slope -0.00026nm/nm - a small roughly-constant
offset, not a scale error). Largest deviations (up to ±1.4nm) cluster in the filter's
known 560-585nm low-throughput band and at the 705-720nm range edge, both low-SNR
regions - read as measurement noise, not necessarily worse optical tuning there. An
offset correction table was produced
(`spikes/lspri_acq_phase0/lctf_wavelength_offset_calibration.csv`, columns include a
`corrected_command_nm` ready to use directly) in case Phase 1 wants per-point
wavelength correction. Full detail in
`spikes/lspri_acq_phase0/docs/lctf_passband_centroid_shift.md`.

Neither of these blocked anything - both closed out items the architecture plan (§3)
had left as "needs the real LCTF/LED driver," which at the time of the previous entry
above hadn't been connected yet.

## 2026-08-07 (continued): second camera vendor added, IDS uEye fps bug found/fixed, sweep-cycle question closed

Maintainer connected a third camera (IDS UI-3160CP-M-GL Rev.2.1) and asked for it to be
usable independently of the Basler, plus a comparison. Generalized
`benchmark_ui.py`'s `CameraGrabThread` from calling `pypylon` directly to driving a new
`CameraBackend` ABC, with `PylonBackend` (the existing logic, unchanged behavior) and a
new `UeyeBackend` for `pyueye`. Verified against the pyueye package actually installed
(real function signatures/constants read from its source, not assumed from memory) and
against IDS's own official `pyueye_example` reference pattern for the sequence-
buffer/queue capture lifecycle.

`pyueye` needs IDS's own `ueye_api.dll` (from the separate, non-pip-installable "IDS
Software Suite" installer) - confirmed on this machine: the camera showed "Error"
status in Device Manager and `from pyueye import ueye` failed at import time until the
maintainer installed that driver mid-session. Because of this, every `pyueye` import in
`UeyeBackend` is lazy (inside methods, never at module load), so the tool still runs
and reports 0 IDS cameras rather than crashing when the driver isn't present. Added as
an optional dependency comment in the umbrella repo's `requirements.txt`, matching the
existing `AMFTools` pattern.

**Bug found on first real benchmark run**: 25.03fps at Mono12/native/1×1, and - the
tell - 2×2 binning made no difference, ruling out a bandwidth/data-volume explanation.
Root-caused to a freshly-initialized uEye camera's conservative default pixel clock
(200MHz of a 120-400MHz range) and a frame-rate cap stuck at ~25fps regardless of
exposure/binning, neither a real sensor/USB limit. Fixed via a new
`UeyeBackend.maximize_throughput()` (raises pixel clock to max via `is_PixelClock`,
then requests the fastest legal frame rate via `is_GetFrameTimeRange`/`is_SetFrameRate`),
called after binning and before exposure is applied. Verified on the bench:
Mono8/native/1×1 25fps→~117fps, Mono12/native/1×1 (maintainer's real settings)
25.03fps→**85.07fps** (confirmed via a real `benchmark_ui.py` timed-benchmark run: 2552
frames/30s, 0 late frames).

**Also fixed**: `is_WaitForNextImage`/`is_InitImageQueue` are marked deprecated in this
pyueye release (`4.96.952`) with no bundled replacement for the queue-capture pattern
still in use - confirmed functionally correct regardless. First fix attempt (filtering
by `module="pyueye"`) silently didn't work because pyueye's `deprecated()` wrapper uses
`stacklevel=2`, attributing the warning to the *calling* code's module, not to pyueye
itself - fixed by filtering on the warning's message text instead. Verified with
`warnings.simplefilter("always")` forcing everything else to show: zero targeted
warnings leaked through a real capture run.

**Camera comparison extended to three real models** (a2A3840-45umBAS, acA5472-17um,
UI-3160CP-M-GL Rev.2.1) - resolution/pixel-size/frame-rate/shutter-type table and the
global-shutter-vs-LED-PWM-striping reasoning (relevant here specifically, not just
generically) in the new findings doc. Maintainer's call: stay on the a2A3840-45umBAS as
primary; IDS camera documented as an alternative, notably for its global shutter, if
LED-PWM striping turns out not to be fully solved by exposure/intensity tuning alone in
practice.

**Sweep-cycle rate question (§3's deferred item) closed**: maintainer clarified the
real acquisition pattern is single-shot software-triggered capture (settle → trigger
one frame → move on), not continuous streaming - a different question from everything
`benchmark_ui.py` had measured so far. Measured standalone (uEye
`is_SetExternalTrigger(IS_SET_TRIGGER_SOFTWARE)` + blocking `is_FreezeVideo(IS_WAIT)`,
n=200 reps per setting, not yet wired into `UeyeBackend` itself - deliberately deferred,
maintainer will implement triggered capture directly in the real app): ~17ms median
round-trip at Mono12/native (maintainer's real settings), tight jitter (~0.4ms stdev).
Combined with the settle-time numbers above: LCTF settle time (~35-90ms depending on
direction) comfortably dominates camera latency (~17-20ms) in every case - camera choice
is not a sweep-*speed* bottleneck for any of the three cameras evaluated. No single
combined end-to-end run (LCTF + camera together) was performed; both halves measured
independently was enough to answer the question. Full data and reasoning in
`spikes/lspri_acq_phase0/docs/camera_backend_and_throughput_findings.md`; §3 and §12 of
the plan doc updated to match. Nothing from today committed yet.

## 2026-08-07 (continued): Phase 1 item 1.3.1 — settings persistence + user_profile extracted

Maintainer asked to resume Phase 1 (the shell extraction, not the GUI work discussed in
the same session - that's Phase 2, deliberately deferred until the shell is done, per
this doc's own sequencing). Picked up at the next unchecked item, 1.3.1.

**Scope grew by one module, deliberately**: the plan only named `storage/app_config.py`,
but `app_config.py`'s path resolution depends on `storage/user_profile.py` (the per-user
settings-file registry), and that module's own docstring already flagged itself as "not
a shared package function yet... if a second app needs the same registry, promote this
to `lspr_core` then, with a real second caller to design against." LSPRimaging acq is
that second caller, so - confirmed with the maintainer first - moved both together
rather than extracting one and leaving the other stranded pointing at app-specific
internals.

**Real bug caught before it was written**: `user_profile.py`'s paths are suite-wide
(`user_config_dir("lspr-suite")`), but every settings-file path (`GLOBAL_CONFIG_PATH`,
`user_settings_path(name)`) was a *hardcoded* filename (`lspr_settings.json`), not scoped
per app. Moved verbatim, this would have made sLSPR acq and LSPRi acq silently share one
settings file per user - LSPRi acq's UI state, theme, acquisition state would overwrite
(and be overwritten by) sLSPR acq's on every save. Fixed by adding an optional `filename`
parameter to `global_config_path()`/`user_settings_path()`/`current_config_path()`,
defaulting to `DEFAULT_SETTINGS_FILENAME = "lspr_settings.json"` (sLSPR acq's historical
name) so sLSPR acq's on-disk per-user files and every existing call site are completely
unaffected - a future LSPRi acq caller passes its own `filename` explicitly. The *user
registry* itself (`lspr_users.json` - known/active users) stays unparameterized and
genuinely shared, since "who's logged in" is one concept across the whole suite.

**What moved**: `packages/lspr_acq_shell/src/lspr_acq_shell/user_profile.py` (the
registry + the app-scoping fix above + `safe_path_component`, previously in sLSPR acq's
`storage/output_paths.py`) and `.../settings_store.py` (the generic JSON payload cache,
atomic write, corruption quarantine, and `ui_state`/`app` key-value helpers - what the
plan's §4.3 item 1 actually named). `lspr_acq_shell/__init__.py` now re-exports both,
replacing the "nothing exported yet" placeholder.

**What stayed behind in sLSPR acq, and why**: `storage/app_config.py` keeps
`save_processing_settings`/`load_processing_settings` (`ProcessingSettings`-shaped),
`save_processing_settings_to_hdf5`/`load_processing_settings_from_hdf5` (sLSPR's own
HDF5 metadata schema), `save_dark_reference_cache`/`load_dark_reference_cache`
(`Spectrum`-shaped), and `save_acquisition_state`/`load_acquisition_state` (thin
wrappers) - none of these are app-agnostic, matching §4.3 item 3's stated principle
("extract the plumbing, leave the concrete schema behind") applied a step early, to
item 1.

**Backward compatibility, not a rewrite of ~20 call sites**: `storage/user_profile.py`
and the generic parts of `storage/app_config.py` are now thin re-export shims over
`lspr_acq_shell` - every existing `from lspr_app.storage.app_config import
save_app_setting` (etc.) call site across the app (gui/main_window*.py,
device/device_lifecycle.py, device/device_manager.py, and 15+ others) needed zero
changes. This was a deliberate choice for this first extraction (lowest-risk item,
meant to be practiced on before the riskier ones later in §4.3) - a "clean" version
that updates every call site to import from `lspr_acq_shell` directly is possible later
but isn't required for correctness.

**Test fallout, found by running the suite, not guessed**: four integration tests
(`test_discovery_blank_plot.py`, `test_main_window_settings_undo.py`,
`test_source_mode_switch_sync.py`, `test_start_tracking_ready_indicator.py`) patch
`user_profile`'s private module attributes (`_SHARED_CONFIG_DIR`, `_REGISTRY_PATH`,
`GLOBAL_CONFIG_PATH`) directly to isolate a temp config dir - patching those on the new
re-export shim does nothing, since the shim doesn't own that state anymore. Fixed by
pointing all four (plus `tests/unit/test_user_profile.py`, which owns the actual
behavioral coverage) at `lspr_acq_shell.user_profile` - the real state owner - instead of
`lspr_app.storage.user_profile`. No other production call site needed this (none of them
patch internals, they just call the public functions), confirmed by grepping every
`user_profile`/`_SHARED_CONFIG_DIR` reference in `tests/` before considering this done.

**Verified**: full umbrella suite, 862/862 (the one failure on the first run,
`test_async_writer_reports_failure_via_on_error_callback`, is the same pre-existing
Windows temp-dir-cleanup race documented in the 2026-08-06 registry-generalization
entry above - confirmed passing in isolation again, unrelated to this change).
`pyflakes` clean on all 11 touched files. sLSPR acq launched
(`LSPR_FORCE_SIMULATOR=1`, 10s run) with no errors/tracebacks in the log - a real
process launch, not just the test suite, since this touches settings loaded at startup.

**Not done**: real per-user-settings-file behavior on real hardware (not applicable -
this is pure file I/O, already covered by the test suite); nothing from today
committed yet, pending the maintainer's go-ahead (submodule + umbrella commit).

## 2026-08-07 (continued): Phase 1 item 1.3.2 — diagnostics extracted, scope corrected

Picked up 1.3.2 next, per the plan's ordering. This item's own description turned out
to name the wrong file.

**What the plan called "`gui/runtime_diagnostics.py`'s profile system" is actually two
different things of very different sizes**: the real off/normal/debug/deep profile
system is top-level `diagnostics.py` (231 lines, `DiagnosticsConfig` + env-var parsing +
a logging filter) - small, and genuinely app-agnostic on inspection (every env var it
reads - `LSPR_DIAGNOSTICS_PROFILE`, `LSPR_QUIET_DIAGNOSTICS`,
`LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS`, `LSPR_ENABLE_DIAGNOSTIC_EXPORT`,
`LSPR_DISABLE_DIAGNOSTIC_EXPORT`, `TOP_CONTENT_TRACE` - is suite-scoped, not
app-prefixed, and `from_window()`'s attribute reads follow a generic convention any
main window could implement). `gui/runtime_diagnostics.py` is a different, much bigger
file (~1200 lines) - `SessionDiagnosticsSnapshot`, the diagnostics-*panel*'s content
builder, consuming `DiagnosticsConfig` but built entirely out of `getattr(window,
"_last_spectrum_..._ms", ...)`-style reads against sLSPR acq's specific spectrum/trace/
sensorgram plot internals, scheduler stats, and log-buffer counters. Checked
`gui/main_window_startup_diagnostics.py` too (434 lines, widget-stack startup tracing) -
same story, keyed to `_top_content_stack`/`_experiment_control_window`/`_spectra_block`
identity checks specific to this app's window layout.

**Decision**: extract only `diagnostics.py` (the actual profile/config layer). Leave
`gui/runtime_diagnostics.py` and `gui/main_window_startup_diagnostics.py` behind -
contrary to this item's original "mostly already shared-package-shaped" framing, neither
has a modality-agnostic seam; they're consumers of the shared config, not shareable
content themselves. LSPRi acq will write its own diagnostics-panel content builder
later, against its own window, reusing `DiagnosticsConfig` from `lspr_acq_shell`.

**Also checked, deliberately left alone**: `packages/lspr_core/src/lspr_core/launch_profiles.py`
(`LAUNCH_PROFILE_*`, the Full/Simulation/Control-editor launch profile selector). It's
already reachable from both `apps/sLSPR/acq` and `apps/suite_launcher` via `lspr_core`
(confirmed by grep - the suite launcher imports it too, for the launcher's per-app
profile dropdown), so the plan's "already in lspr_core" note holds and no relocation was
needed for this item. Worth flagging for whoever picks up Phase 2 though: its *content*
is 100% sLSPR-specific (`LSPR_ACQ_LAUNCH_PROFILE` env var name, `source_mode="spectrometer"`,
`show_sensorgram`, etc.) - LSPRi acq will need its own `LaunchProfileSpec` set with
different fields (camera/illumination-shaped, not spectrometer/sensorgram-shaped), not a
shared one. Not a Phase 1 problem; noted here so it isn't rediscovered from scratch.

**Mechanics**: same pattern as 1.3.1 - `lspr_acq_shell.diagnostics` gets the real
`DiagnosticsConfig`/`apply_diagnostic_info_filter`; sLSPR acq's `diagnostics.py` becomes
a thin re-export shim (5 call sites - `app.py`, `gui/main_window.py`,
`gui/main_window_logging.py`, `gui/main_window_logging_ui.py`,
`gui/runtime_diagnostics.py` - unaffected). `tests/integration/test_diagnostics.py`
repointed at `lspr_acq_shell.diagnostics` directly (no state-patching concern here,
unlike `user_profile` - `DiagnosticsConfig` is a stateless frozen dataclass - but kept
the "test the real owner" convention from 1.3.1 anyway).

**Verified**: full umbrella suite, 861/862 (the one failure is the same pre-existing
Windows temp-dir-cleanup race as every prior entry - `test_async_writer_reports_failure_via_on_error_callback`,
reconfirmed passing in isolation again). `pyflakes` clean on all 4 touched files. sLSPR
acq launched twice more (`LSPR_FORCE_SIMULATOR=1`, 10s each - once before, once after
this item's changes) with no errors/tracebacks either time.

**Not done**: nothing from today committed yet, pending the maintainer's go-ahead.

## 2026-08-07/08: Phase 1 item 1.3.3 — HDF5 async-writer generalized into AsyncTaggedWriter

Picked up 1.3.3 next. Unlike 1.3.1/1.3.2, this one couldn't be a mostly-mechanical move -
flagged and confirmed with the maintainer before writing any code, since it defines a
seam Phase 2 will build against later.

**Why a literal move wouldn't have worked**: `storage/hdf5_export.py`'s
`AsyncHDF5MeasurementWriter` (267 lines) is the "threading/queue plumbing" the plan
names, but its background-thread `_run()` hardcoded `writer = HDF5MeasurementWriter(...)`
construction directly, and every queued tag (`"append"`, `"metrics"`, `"baselines"`, ...)
was `Spectrum`/`ProcessingSettings`-shaped by name. Moving the class as-is would have
relocated an sLSPR-specific class into the shared package, not something LSPRi acq's
future `SpectralCube` writer could actually reuse - defeating the point, and contradicting
the plan's own §8/§9 language ("new tags (cube, roi_definitions) dispatched the same way
append/metrics already are"), which already assumes a genuinely generic base exists.

**What's actually generic vs. what isn't**: the queue/thread lifecycle, the periodic-flush
timing loop, close()'s drain-then-join-with-timeout, the four structural operations
(`flush`/`close`/`save_copy`/`timeout`) and their exact ordering (flush pending →
`writer.flush()` → for `save_copy`, `writer.copy_into()` only after that flush completes -
this ordering is what keeps a second concurrent file handle from ever touching the file
mid-write), and the on_error escape hatch when the underlying writer fails - none of that
is spectrum-specific. What isn't generic: the concrete writer type, and which tags exist /
what each one does with its payload.

**Design landed**: `lspr_acq_shell.AsyncTaggedWriter` (`packages/lspr_acq_shell/src/lspr_acq_shell/async_writer.py`),
an ABC owning everything in the paragraph above, with three hooks a subclass fills in:
- `_open_writer()` - construct/open the concrete writer, called once on the background
  thread before the dispatch loop starts.
- `_apply(writer, tag, payload)` - handle one dequeued item whose tag isn't one of the
  four structural ones. Immediate-effect tags call straight through to `writer`;
  batch-until-flush tags (sLSPR's `"append"`/`"metrics"`) accumulate into subclass-owned
  instance state instead.
- `_flush_pending(writer)` - write out and clear whatever `_apply` batched. Called before
  every `writer.flush()`, including every periodic timeout tick (even when nothing was
  pending, matching the original's unconditional per-tick `writer.flush()` call) - a
  subclass with no batching can leave this a no-op.
- Two optional message-formatting overrides (`_open_error_message`/`_run_error_message`)
  so a subclass can keep its exact original on_error wording instead of the base's
  generic default text - used to preserve sLSPR acq's exact strings ("Could not open
  measurement file: ...", "Measurement recording stopped unexpectedly: ...") byte-for-byte,
  since nothing else about the on_error contract changed.

`AsyncHDF5MeasurementWriter` is now a ~140-line subclass: its public API (`update_processing`,
`append_batch`, `append_metrics`, `append_environment_reading`,
`append_experiment_control_runtime`/`append_flow_state`, `append_device_state`,
`write_device_inventory`, `update_baselines`, `update_acquisition_state`) is unchanged in
every method's signature and behavior (each is now a one-line `self._put(tag, payload)` call
instead of a hand-rolled `if self._closed: return; self._queue.put(...)`), and
`flush()`/`save_copy()`/`close()` are inherited from the base unmodified - none of the 2
real call sites (`gui/acquisition_controller.py`, `storage/measurement_archive.py`) needed
any change. The original big if/elif tag-dispatch block moved into `_apply`/`_flush_pending`
with identical logic, just renamed `pending_spectra`/`pending_metrics` etc. to
`self._pending_spectra`/`self._pending_metrics` (now instance state, since the base's
generic `_run()` loop no longer owns local dispatch variables).

`hdf5_export.py`'s top-of-file imports lost `queue`/`threading`/`import time.monotonic`
(no longer used anywhere else in the file once the class moved) and gained
`from lspr_acq_shell import AsyncTaggedWriter`.

**Unrelated incident during verification, resolved**: the first full-suite run (background,
started before this item's changes were verified) took an abnormally long time (~150s vs.
the usual ~90-110s) and coincided with the maintainer reporting all three running app
windows frozen. Investigated rather than assumed unrelated: system free memory was at
4.9GB/31.8GB, and `Get-CimInstance Win32_Process` showed, alongside this session's own
test run, six stale `pytest tests/unit/test_live_processing_worker.py` processes dated
2026-08-01 and 2026-08-04 (abandoned mid-session in some earlier, unrelated work and never
cleaned up) plus four `blender-mcp` extension processes and two orphaned
`multiprocessing-fork` workers - none started by this session. Stopped this session's own
background test run first (freed 5.6GB), then, with the maintainer's explicit go-ahead,
killed all fourteen of the other stale/unrelated processes by PID - free memory recovered
to 15.1GB and zero `python.exe` processes remained. Not a bug in anything built today;
noted here only because it interrupted this item's verification pass and because leftover
test processes accumulating across sessions is apparently a real, recurring thing on this
machine worth the maintainer knowing about.

**Verified** (after the above was resolved): full umbrella suite, 861/862 in 59.8s (back
to the normal duration, confirming the earlier slowness was the stale-process contention,
not this change) - the one failure is the same pre-existing Windows temp-dir-cleanup race
as every prior entry (`test_async_writer_reports_failure_via_on_error_callback`, now
logging through `lspr_acq_shell.async_writer` instead of the old module path, exactly as
expected since the exception originates in the shared base now), reconfirmed passing in
isolation. `pyflakes` clean on all 3 touched files
(`packages/lspr_acq_shell/src/lspr_acq_shell/async_writer.py`, that package's
`__init__.py`, `storage/hdf5_export.py`). sLSPR acq launched twice more
(`LSPR_FORCE_SIMULATOR=1`, 10s each) with no errors/tracebacks, and left no orphaned
process behind either time (checked explicitly this time, given the incident above).

**Not done**: nothing from today committed yet, pending the maintainer's go-ahead.

## 2026-08-08: Phase 1 item 1.3.4 — sensorgram plotting investigated, scope corrected again

Picked up 1.3.4 next. This item's framing was wrong in the *opposite* direction from
1.3.2/1.3.3: those undersold the coupling in the file they named; this one named three
things, none of which turned out to be extractable, while a real extraction candidate sat
in a file the plan never mentioned.

**What the plan named, and why none of it moved**:
- `gui/plot_controller.py` (1759 lines): grepped for real `def name(window` signatures
  (not just any occurrence of the substring "window", which is noisy here - `window_min`/
  `window_max`/`window_start_x` are common *non-GUI* parameter names in this codebase,
  e.g. `clip_series_to_window(x, y, *, window_min, window_max)`). 37 of 54 top-level
  functions take the main-window object directly and reach into its private attributes
  (spectrum stats, cursor labels, deferred-refresh timers, scheduler state). This is Qt
  orchestration code intermixing spectrum-plot and sensorgram-plot handling in one file,
  not "curve-data-shaped" logic as described.
- `gui/sensorgram_secondary_axis.py` (1157 lines): same story - pyqtgraph `ViewBox`/menu/
  axis-color widget building, all window-coupled.
- "Session/run bookkeeping" didn't name a real generic module once investigated.
  `domain/session.py`'s `MeasurementSession` (162 lines, Qt-free) turned out to be
  dark/reference/absorbance-spectrum math (`compute_absorbance`, wavelength-axis
  resampling) - correctly sLSPR-specific per `spectral_processing_pipeline_architecture.md`,
  must stay. `gui/main_window_new_session.py`/`main_window_session_copy.py` are
  window-coupled GUI action handlers (New Session dialog, save-a-copy action), same
  pattern as the two files above.

**What actually extracted cleanly, in a file this item never named**: `gui/plot_view_cache.py`
(1601 lines). Grepped the same way - only 2 of 30 top-level functions
(`build_active_trace_series_token`, `build_metric_series_token`) take `window`; the other
28, plus the `MetricDisplayCache`/`MetricCompressionBlock` dataclasses and the
`PlotViewCache` class, are pure numpy with zero Qt/window imports anywhere in the file
(confirmed by grep, not just by not noticing any). This is a genuine multi-resolution
downsampling/caching engine: `MetricCompressionBlock` summarizes (min/max/mean/first/last)
a run of raw points; `PlotViewCache` builds a pyramid of these at increasing block sizes
and picks whichever level keeps the on-screen point count near a target regardless of how
many points have actually accumulated in a long-running session - genuinely reusable by
any app plotting a long time series, not spectrum- or sensorgram-specific despite living
in a file with "plot" in the name. Already used for both the sensorgram *and* spectrum
plots in sLSPR acq, matching the "already curve-data-shaped (time + metric value)"
description this item originally gave to the wrong file.

**Confirmed with the maintainer before implementing** (per the pattern established for
1.3.3): presented the finding, recommended extracting only `plot_view_cache.py`'s engine
and leaving the GUI-panel files for a genuine Phase 2 rewrite - approved.

**Mechanics**: `lspr_acq_shell.plot_view_cache` gets everything except the two
window-coupled token functions, moved verbatim (no logic changes - this file had zero
app-specific assumptions to generalize away, unlike 1.3.1/1.3.3). sLSPR acq's
`gui/plot_view_cache.py` becomes a shim re-exporting the engine plus keeping
`build_active_trace_series_token`/`build_metric_series_token` defined locally (they only
need `pathlib.Path` and `getattr(window, ...)` - no dependency on the rest of the
original file beyond having previously been colocated in it). None of the app's call
sites (`main_window.py`, `plot_controller.py`, `main_window_sensorgram.py`,
`main_window_sensorgram_archive.py`, `runtime_diagnostics.py`, `runtime_probe.py`,
`acquisition_controller.py` - the last several call methods on an already-constructed
`window._plot_view_cache` *instance*, so they're unaffected by the module split
regardless) needed any change.

`tests/unit/test_plot_view_cache.py` split its imports rather than moving wholesale: the
engine tests (`PlotViewCache`, `quantize_view_target_points`,
`sample_absolute_metric_series_for_view`) now import from `lspr_acq_shell.plot_view_cache`
directly (the real owner); the one test exercising the two token functions
(`test_token_helpers_track_live_absolute_state`) still imports those from
`lspr_app.gui.plot_view_cache`, since that's where they actually live now.

**Verified**: full umbrella suite, 861/862 (same pre-existing Windows temp-dir flake,
reconfirmed in isolation), pyflakes clean on all 4 touched files, sLSPR acq launched
(Simulation profile, 10s) with no startup errors, no orphaned processes and 14.6GB free
memory afterward (checked given the earlier incident this session).

**Not done**: nothing from today committed yet, pending the maintainer's go-ahead. The
GUI-panel rewrite this item's original scope implied (sensorgram plotting/secondary-axis
UI, session-management UI) is explicitly deferred to Phase 2, per the ROI-panel precedent
in §10 - not tracked as an open Phase 1 item, since it was never really one.

## 2026-08-08 (continued): Phase 1 item 1.3.6 — fluidics device framework moved, Phase 1 complete

The last Phase 1 item, and the plan's own explicitly-flagged highest-risk one - real
concurrency-sensitive hardware I/O with a ~30-bug incident history
(`DEVICE_LAYER_AUDIT_2026.md`). Followed §4.2's rule strictly: as close to a byte-for-byte
relocation as possible, no restructuring beyond the one pre-approved generalization.

**Scope grew from 6 to 12 files, traced not assumed**: the plan named `device_manager.py`,
`device_lifecycle.py`, `communication_models.py`, `serial_controllers.py`,
`connection_registry.py`, `port_assignments.py`. Grepped every file's own
`from lspr_app.device.X import` lines before writing anything, which surfaced a real gap:
`device_manager.py` directly imports `amf_mswitch.py` (AMF selector driver),
`reglo_icc.py` (pump driver), and `valve_controllers.py` (switch/valve drivers) - none in
the plan's list - plus `device_driver.py` (base ABC) and `device_types.py` (canonical
constants) that several of the six *and* the three drivers need. `device_lifecycle.py`
additionally needed `probe_diagnostics.py`. None of this is optional - `device_manager.py`
literally cannot be imported without the three concrete drivers. Final set: `device_types.py`,
`device_driver.py`, `connection_registry.py`, `probe_diagnostics.py`, `communication_models.py`,
`port_assignments.py`, `serial_controllers.py`, `amf_mswitch.py`, `valve_controllers.py`,
`reglo_icc.py`, `device_manager.py`, `device_lifecycle.py` - ~3,580 lines total (up from
the plan's implied ~2,400).

**The one real design decision, confirmed with the maintainer before writing code**:
`device_lifecycle.py`'s module docstring already said "single owner of the device
(spectrometer/pump/valve/selector) lifecycle" - not fluidics-only as the plan's item title
implied. `run_spectrometer_stage()` directly did `from lspr_app.device.ocean import
OceanSpectrometer` and constructed it inline, called unconditionally first in
`run_full_cycle()` (before port refresh, ungated by `enabled_devices`, its live instance
surfaced via `DeviceLifecycleReport.spectrometer`). Moving this verbatim would have made
`lspr_acq_shell` depend on sLSPR acq's spectrometer driver - backwards, and useless to
LSPRi acq (no spectrometer; its Camera/IlluminationSource use the standard
`register_device_family()` path instead, per the plan's own §6.1).

Presented the finding and two options (generalize into a pluggable hook vs. leave
`device_lifecycle.py` behind entirely); maintainer chose generalization. Implemented by
extending the exact registration idiom `register_device_family()` already established
(2026-08-06 entry above): a new `register_primary_detector_stage(key, run_stage)`, backed
by module-level `_PRIMARY_DETECTOR_KEY`/`_PRIMARY_DETECTOR_STAGE`, called once from
`DeviceLifecycleController.run_primary_detector_stage()` at the same point in
`run_full_cycle()` the spectrometer call used to be - optional (an app that registers
nothing skips the stage entirely, exactly LSPRi acq's case). sLSPR acq's own
`device_lifecycle.py` shim registers `_run_spectrometer_stage` (byte-identical logic to
the original method, just now a module function receiving `(controller, emit)`) at import
time - the same pattern `register_device_family(PUMP, ...)` already used at the bottom of
the pre-move module. `DeviceLifecycleReport.spectrometer` (the field real callers still
read) kept as a backward-compatible read-only property over a new generic
`primary_instrument` field, mirroring `PortRefreshData.pump_ports`'s existing
property-over-generic-field pattern from the 2026-08-06 registry work.

**`ACTIVE_PUMP_CHANNELS`/`VALID_ROLLER_COUNTS`/`DEFAULT_ROLLER_COUNT`** moved from
`domain/pump_plan.py` to `reglo_icc.py` alongside this (same treatment as 1.3.1's
`DEFAULT_SETTINGS_FILENAME` reasoning) - these are Reglo ICC pump-hardware facts (the
manual's own roller-count/channel-count specs), not plan-execution facts, and
`reglo_icc.py` needed them internally once it moved. `pump_plan.py` now imports them back
(many GUI files still import these three names from `pump_plan`, not `reglo_icc`, so that
had to keep working) - `ACTIVE_PUMP_CHANNELS` is used inside `pump_plan.py` itself, the
other two are pure re-exports for other modules (pyflakes correctly flagged this; silenced
with a one-line `_ = (...)` reference, the same idiom used elsewhere in this codebase for
deliberately-unused parameters, since bare `pyflakes` - unlike `ruff` - doesn't honor
`# noqa` comments).

**A real bug caught by the test suite, not by inspection**: `DeviceLifecycleReport`'s
rename (`spectrometer` field -> `primary_instrument` field + `spectrometer` property)
broke the one place that constructs the dataclass directly with a `spectrometer=` keyword
argument (`tests/integration/test_main_window_flow_panel_parenting.py` - a synthetic
report for a `main_window` hardware-init-finished handler test). Properties aren't
constructor parameters, so this raised `TypeError: unexpected keyword argument
'spectrometer'` the moment the full suite ran. Grepped every `DeviceLifecycleReport(`
call site first (exactly one, this test) before deciding: fixed the test to use
`primary_instrument=None` rather than reverting the field rename, since the whole point of
generalizing this shared dataclass's naming was for it to stop being spectrometer-specific,
and only one call site needed updating.

**Five test files needed patch-target fixes, all the same root cause as 1.3.1/1.3.5**:
running the full suite (not assuming shims are transparent to `unittest.mock.patch`)
surfaced that `test_device_lifecycle.py` (37 tests), `test_port_assignments.py`,
`test_amf_mswitch.py`, and `test_hardware_inventory.py` all patch module-level names
(`get_port_assignment`, `is_probable_reglo_port`, `RegloICCClient.probe_port`,
`load_enabled_devices`, `amfTools`, `load_app_setting`/`save_app_setting`, and
`test_port_assignments.py`'s direct access to the private `_assignment_cache` global) that
now live in `lspr_acq_shell`'s modules, not the sLSPR acq shims that merely re-export
them - patching the shim's copy doesn't affect the real module's internal calls to its own
name. Fixed by repointing `test_device_lifecycle.py` and `test_port_assignments.py` at
`lspr_acq_shell.device_lifecycle`/`lspr_acq_shell.port_assignments` directly (moved from
`apps/sLSPR/acq/tests` colocation to `tests/unit`, matching the "test the real owner"
convention from every prior 1.3.x item), and updating the four `patch("lspr_app.device.X...")`
string targets in the other three files to their `lspr_acq_shell.X` equivalents.
`test_device_lifecycle.py` additionally needed a deliberate side-effect import of the sLSPR
acq shim (`import lspr_app.device.device_lifecycle as _slspr_device_lifecycle_shim`) so the
spectrometer-stage registration actually runs before tests that expect a "spectrometer"
event in `run_full_cycle()`'s output - without it, nothing in the test file's own import
chain would trigger that registration, since `lspr_acq_shell.device_lifecycle` alone
(the real owner, now `dl` in this test) doesn't know about spectrometers at all.
`patch("lspr_app.device.ocean.OceanSpectrometer", ...)` (3 occurrences) needed no change -
the registered stage function still does a fresh local import from `lspr_app.device.ocean`
on every call, exactly like the original method did, so patching that path still works.

**A scary-looking but unrelated false alarm, investigated rather than dismissed**: the
first full-suite run after this change printed `Windows fatal exception: code
0x8001010d` (`RPC_E_CANTCALLOUT_ININPUTSYNCCALL`) partway through, with a full C-stack
dump, right before `test_titlebar_double_click_maximize.py`'s second test - alarming
given this item's real hardware-I/O/COM-adjacent surface (pyserial's Windows port
enumeration uses WMI/COM; `amf_mswitch.py`'s `_suppress_console_output` does raw
`os.dup2` fd manipulation). Investigated properly rather than assuming it was
unrelated: `git stash`-ed every uncommitted submodule change (reverting the device/ tree
to its pre-extraction original) and reran the exact same test in isolation - it printed
the identical fatal-exception message at the identical point, with the same 3-passed
result, against completely unmodified original code. Confirms this is a pre-existing
Qt/Windows environment quirk (most likely triggered by `mapToGlobal()`'s native window
calls interacting with COM state during `QApplication.processEvents()`, unrelated to
anything in this device-layer move) rather than a regression - popped the stash back
immediately after confirming.

**Verified**: full umbrella suite, 862/862 (every test green this run, including the
usually-separately-reconfirmed Windows temp-dir flake - didn't trigger this time).
`pyflakes` clean on all 12 new `lspr_acq_shell` files, all 12 sLSPR acq shims, and every
touched test file (one pre-existing `# noqa`-annotated line in the untouched
`hardware_inventory.py` doesn't count - bare pyflakes doesn't honor `# noqa`, not a
regression). sLSPR acq launched twice - Simulation profile (10s) and, since this is
specifically hardware-discovery code, **Full profile** too (20s, real
`run_full_cycle()` executing the spectrometer stage + pump/switch/selector port
scanning against a machine with none of that hardware attached) - both clean, no
errors, no orphaned processes, 16GB+ free memory after each.

**Phase 1 (1.3.1-1.3.6) is now complete** on the test-suite-equivalence basis stated in
§4.3's acceptance criterion. **Not done, and explicitly the maintainer's to do, not
something achievable from this environment**: real pump/valve/selector hardware
re-verification - connect/disconnect, port scanning, the selector's homing post-connect
hook, actual command dispatch - against physical devices, before relying on any of this
for a real experiment. This caveat has now been carried forward, unresolved, across three
separate build-log entries (2026-08-06 registry generalization, and now this one) -
worth prioritizing before the next real experiment, not just noting again.

## 2026-08-08 (continued): Phase 1 item 1.3.5 — V49's real scope found, only the ready piece moved

Picked up 1.3.5 next. This one was wrong in the same direction as the very first version
of item 1.3.5's own text overclaimed ("largely already scoped") - by roughly an order of
magnitude.

**Scale check, not assumed**: `find` + `wc -l` across every `experiment_control_*.py` file
in `apps/sLSPR/acq/src/lspr_app/gui/`: **11,510 lines across 15 files**.
`experiment_control_window.py` alone is 6,165 lines - bigger than everything moved in
1.3.1-1.3.4 combined. Read V49's own planning doc
(`apps/sLSPR/acq/docs/experiment-control/CODEX_EXPERIMENT_CONTROL_REUSE_SPLIT_V49.md`,
191 lines) in full: it opens with "This is a planning and documentation file only. Do not
treat it as an implementation patch," and lays out a 9-task migration (shared visualization
panel, a real window-decoupled controller with its own state machine, an IO module,
capability-flag-driven visibility replacing "private main-window reach-through") with its
own acceptance criteria. This is a design document for future work, not a record of what's
already built.

**What's actually ready today**, matching what §4.1 called "already in progress":
- `experiment_control_capabilities.py` (38 lines) - `ExperimentControlCapabilities`, a
  plain frozen dataclass with `.acquisition()`/`.evaluation()` presets. Zero coupling.
- `experiment_control_backend.py` (114 lines) - `ExperimentControlBackend` (a
  `runtime_checkable` `Protocol`), `NullExperimentControlBackend`, and
  `ExperimentControlDeviceState`. Also zero coupling. `AcquisitionExperimentControlBackend`,
  in the same file, is the concrete sLSPR implementation (wraps an `ExperimentControlWindow`,
  calls several of its private methods) - explicitly the thing that does NOT move, per the
  plan's own instruction.

**What's not ready**: `experiment_control_controller.py` (58 lines) is a thin `QObject`
whose methods (`toggle_run_hold`, `stop`, `move_relative`, ...) mostly just forward to
`window._toggle_experiment_control_run_hold()` / `window._stop_experiment_control()` /
`window._move_to_relative_experiment_control_step()` etc. - real window reach-through,
not the "small public API... should not depend on the main window" V49 specifies. Moving
it as-is today would only be "shared" in name - a second app's window would need to
implement these exact private method names for it to do anything. Left in sLSPR acq.
The remaining ~11,300 lines (`_editing.py`, `_timeline.py`, `_import.py`, `_dialogs.py`,
`_widgets.py`, `_table.py`, `_plan_view.py`, `_step_runner.py`, `_runtime.py`,
`_builders.py`, `_export.py`, and `experiment_control_window.py` itself) are the actual
panel/state-machine/IO split - none of it exists in split form yet.

**Confirmed with the maintainer before implementing**: presented the real scale, explained
this isn't a "one design decision, then execute" item like 1.3.3/1.3.4 - the true V49
migration is a multi-session project on its own. Agreed scope: extract only the ~150 lines
that are genuinely ready now; track the rest as its own future effort rather than force it
into this checklist item.

**Bug found and fixed while touching this code, not left unexamined**: the plan doc's own
§4.3 item 5 had already flagged a specific concern -
`AcquisitionExperimentControlBackend.device_states()` iterates literal keys `("pump",
"valve", "mswitch")`, but the canonical device-family keys (post-V51 registry
generalization, 2026-08-06 entry above) are `PUMP`/`SWITCH`/`SELECTOR`. Traced the actual
call chain to confirm rather than guess: `device_states()` → `self._window
._service_device_connected(key)`/`_device_label_for(key)` →
`device_lifecycle.device_label_for(device_key)` → `_device_family(device_key)` →
`_DEVICE_FAMILIES.get(device_key)` - a **direct dict lookup with no alias normalization**.
`_normalize_device_type()` (the function that *does* map `"valve"→"switch"`,
`"mswitch"→"selector"`) lives in `device_manager.py` and is used for a completely
different purpose (device-profile type strings), never called anywhere in this path. So
`device_label_for("valve")` finds no registered family under that literal key and falls
back to a fabricated `f"{key}_main"` label (e.g. `"valve_main"`) that no real device is
ever registered under - the device-status lookup for the switch/selector would silently
report "not connected" regardless of actual hardware state. **Checked blast radius before
fixing**: grepped every call site of `.device_states()` - `experiment_control_controller.py`
forwards to `self.backend.device_states()`, but nothing anywhere in the app calls
`controller.device_states()` itself. This is unwired V49-anticipatory infrastructure, not
something the running app currently exercises - a latent bug, not a live one. Fixed anyway
(iterate `PUMP`/`SWITCH`/`SELECTOR` from `device_types.py`, matching every other call site
in `experiment_control_window.py`) since resolving it was explicitly asked for and the fix
is a one-line change with a clear before/after.

**Mechanics**: `lspr_acq_shell.experiment_control_capabilities` and
`.experiment_control_backend` get the two ready pieces verbatim (the backend module imports
the capabilities module from within `lspr_acq_shell`, mirroring the original cross-file
import). sLSPR acq's two files become shims - `experiment_control_capabilities.py` a pure
re-export; `experiment_control_backend.py` re-exports the Protocol/Null-backend/device-state
dataclass and keeps `AcquisitionExperimentControlBackend` defined locally (with the
`PUMP`/`SWITCH`/`SELECTOR` fix). No call site elsewhere in the app
(`experiment_control_controller.py`, `experiment_control_window.py`) needed any change
beyond what already existed.

**Verified**: full umbrella suite, 861/862 (same pre-existing Windows temp-dir flake,
reconfirmed in isolation), pyflakes clean on all 5 touched files (caught and fixed one
real mistake first - an edit accidentally dropped the `states: list[...] = []`
initializer line, pyflakes flagged `undefined name 'states'` immediately, fixed before
re-running), sLSPR acq launched (Simulation profile, 10s) with no startup errors, no
orphaned processes, 16.1GB free memory afterward.

**Not done**: nothing from today committed yet, pending the maintainer's go-ahead. The
real V49 migration (shared panel/controller/IO split across ~11,300 remaining lines) is
explicitly NOT started and NOT tracked as a remaining Phase 1 sub-item - it needs its own
dedicated scoping as a future project, the same way the sensorgram/ROI panel rewrites do.

## 2026-08-08 (continued): Phase 2 started - `apps/LSPRi/acq` scaffold, device ABCs, domain model

Phase 1 confirmed complete (test-suite basis), maintainer asked to start Phase 2.

**Repo/submodule setup**: unlike every other app in this suite, `apps/LSPRi/acq` didn't
exist as a repo yet. `gh` CLI isn't installed in this environment, so couldn't create the
GitHub repo directly - asked the maintainer to create an empty
`lednicky-t/LSPRimaging-Acquisition` repo (matching the naming pattern of the other three:
`SingleSpotLSPR-Acquisition`, `SingleSpotLSPR-Evaluation`, `LSPRimaging-Evaluation`).
Maintainer created it; added as a git submodule at `apps/LSPRi/acq`
(`git submodule add https://github.com/lednicky-t/LSPRimaging-Acquisition.git apps/LSPRi/acq`).

**Scaffold built** (plan section 5's package layout): `pyproject.toml` (name
`lspri-acquisition`, entry point `lspri-acquisition = "lspri_acq_app.app:main"`, depends on
`lspr-core`/`lspr-io`/`lspr-ui`/`lspr-acq-shell` plus `pypylon` as a real dependency - Basler
is the primary camera per the Phase 0 conclusion, not optional like `AMFTools`/`pyueye`),
`run.py` (mirrors `apps/sLSPR/acq/run.py`, adds `packages/lspr_acq_shell/src` to
`bootstrap_app_environment`'s `extra_src_dirs` since the shared bootstrap helper's own
`SHARED_SRC_DIRS` only covers `lspr_ui`/`lspr_core`/`lspr_io`), `src/main.py`, and the
`lspri_acq_app` package (`__init__.py`/`_version.py`/`version.py` following the exact
`APP_NAME`/`APP_VERSION` + derived-`__version__` split `lspr_app` uses).

**Camera/IlluminationSource ABCs** (`device/camera_base.py`, `device/illumination_base.py`)
built to the plan's section 6 shapes. One deliberate deviation from the plan's own snippet:
`Camera.capabilities()` and `IlluminationSource.settle_time_ms()` are `@abstractmethod` here,
not given a default the way `lspr_acq_shell.Spectrometer.capabilities()` has one - checked
the precedent before copying it and it doesn't transfer: `SpectrometerCapabilities()`'s
all-flags-off default is a genuinely meaningful "no optional features" answer, but a
default `CameraCapabilities(0, 0)` would silently misrepresent real sensor dimensions, and a
default `settle_time_ms()` (e.g. 0) would let a real sweep grab a frame before the LCTF/LEDs
had actually settled - silently corrupting real data rather than just being uninformative.
Every concrete backend must state its own real number for these two.

**SimulatedCamera / SimulatedIllumination** (`device/simulated_camera.py`,
`device/simulated_illumination.py`) built per section 11 - mirror `SimulatedSpectrometer`'s
role. `SimulatedCamera` renders a configurable set of Gaussian spots plus Gaussian noise onto
a fixed-size sensor; `SimulatedIllumination` is instant-tune, zero settle time, and validates
against a configured `wavelength_range_nm`. 9 unit tests
(`apps/LSPRi/acq/tests/test_devices.py`) cover open-before-use guards, frame shape/metadata,
spot placement, capability reporting, range validation, and settle time.

**Domain model** (`domain/models.py`) built to section 7's exact shapes: `Frame`,
`SpectralCube`, `ImagingAcquisitionSettings`, `AbsorbanceSpectrumResult`. `Frame.metadata`
given a `default_factory=dict` (the plan's snippet doesn't specify a default; every real
construction site wants an empty dict, and slots-dataclass fields don't share mutable
defaults across instances - confirmed by a unit test).  `Frame.wavelength_nm` initialized to
`float("nan")` by `SimulatedCamera.acquire_frame()` and documented as "filled in by the
sweep controller after the fact" - the camera itself has no way to know what wavelength the
illumination source was set to; that's the sweep loop's job once the real one exists
(section 8, not built yet).

**ROI types ported** (`domain/roi.py`) - `AreaRoi`/`AreaRoiGroup` copied field-for-field from
`apps/LSPRi/eva/src/lspr_imaging_app/domain/models.py`, current names only (no
`DetectedSpot`/`SpotGroup` aliases), per the plan's explicit instruction. Kept the
auto-detection-scoring fields (`score`, `support_*`, `quality_score`, `inferred`) even
though v1 doesn't do auto-detection (manual placement only) - the plan says "ported
verbatim," and dropping fields now would just have to be reconciled again if/when
auto-detection is ever added here.

**Minimal main window** (`gui/main_window.py`, `gui/app.py`) - deliberately just a title/
version/status label, not a feature. Exists to prove the app boots and wires
`lspr_core`/`lspr_io`/`lspr_ui` correctly (via `apply_base_app_theme`/`app_icon`), not to be
feature-complete - the real GUI panels (image view, ROI panel, experiment control) are
separate, later milestones. No splash screen / lock file / launch-profile plumbing yet
(unlike `lspr_app.app`) - those are real design decisions that need an actual hardware
discovery flow to hang off of, which doesn't exist yet; adding them now would be
speculative complexity ahead of a real need.

**Launcher wiring corrected, not yet enabled**: `apps/suite_launcher/.../targets.py`'s
`lspri_acq` `AppTarget` had placeholder values that didn't match reality (`address`/`script`
pointed at a nonexistent `app.py` at the app root, `extra_paths` was missing `lspr_ui` and
`lspr_acq_shell`, no `github_repo`/`version_file`). Fixed to match the pattern
`slspr_acq` uses (`src/main.py` entry point, `python_candidates` from the suite venv, all
four shared packages' `src/` dirs, `github_repo="lednicky-t/LSPRimaging-Acquisition"`,
`version_file="src/lspri_acq_app/version.py"`). **Left `enabled=False`** - the plan's own
section 5 says flip it once the app has a working entry point, but "working" here should
mean something a user would actually want to open, not just an importable module; revisit
once the GUI has real acquisition content.

**Verified**: `pip install -e apps/LSPRi/acq` succeeds cleanly (all four shared packages
resolve as already-installed editable deps). `apps/LSPRi/acq/tests/` - 15/15 passed
(9 device tests, 6 domain-model tests). Full umbrella suite - 861/862, the one failure the
same pre-existing Windows temp-dir-cleanup race documented in every prior entry
(`test_async_writer_reports_failure_via_on_error_callback`), unrelated to this change -
confirms today's targets.py/requirements.txt edits didn't regress anything else. `pyflakes`
clean on every new file. App launched via `python apps/LSPRi/acq/src/main.py` and
screenshotted (`pywinauto`, real window capture, not just "process didn't crash") - dark
suite theme applied correctly, title/version/status text rendered as expected, no
tracebacks in process output.

**Not done, deliberately deferred rather than half-built**: registering `CAMERA`/
`ILLUMINATION` as new device families into `lspr_acq_shell`'s generalized registry
(section 6.1, the next milestone in section 12's checklist) needs a real
`discover_and_connect` callback design decision (what does "discover" even mean before a
real Basler/VariSpec driver exists - always synthesize a simulated device? gate on an env
var?) - judged that wiring this now, ahead of any real driver, risked exactly the kind of
speculative/guessed design this project has repeatedly avoided elsewhere (e.g. section 8's
"don't build the more complex version speculatively" for the queue-transport choice).
Left for the next session, alongside the real Basler/VariSpec drivers it's meant to serve.
Also not done: nothing from today committed yet, pending the maintainer's go-ahead
(new submodule content + umbrella `requirements.txt`/`targets.py` changes +
`.gitmodules`/submodule-pointer addition).

**Committed and pushed** *(2026-08-08, same day)*: maintainer reviewed and approved.
Submodule commit `cd5c5ff` ("Scaffold LSPRimaging Acquisition (Phase 2)...") pushed to
`lednicky-t/LSPRimaging-Acquisition`; umbrella commit `7abddf1` ("Add LSPRimaging
Acquisition submodule; start Phase 2...") pushed to `lednicky-t/LSPR-Suite` `develop`.

## 2026-08-08 (continued): CAMERA device family registered; real (unverified) Basler driver built

Maintainer asked to continue. Picked up where the previous entry left off - registering
`Camera`/`IlluminationSource` as device families (section 6.1).

**Real gap found between the plan's assumption and the actual code, before writing
anything**: section 6.1 says Camera/Illumination "plug into lifecycle management... for
free" via `register_device_family()`. Traced `DeviceCommunicationService._connect_impl()`
(`device_manager.py`) to confirm this holds, and it doesn't fully: the 2026-08-06 registry
generalization (this file's own earlier entry) only generalized *discovery* dispatch in
`device_lifecycle.py` - `_connect_impl()`'s *construction* step was never touched, and is
still a hardcoded three-way branch (`profile.driver == "reglo_icc"` → `RegloICCClient`,
`"amf-mswitch"` → `AMFSwitchController`, else-if-non-empty-driver → `detect_valve_controller`).
A family registered via `register_device_family()` alone would discover fine and then fail
the moment `connect()` tried to build a connection object for it. Also noted a real shape
mismatch: `send_command()`/`DeviceCommand` is sized for small discrete commands (pump
"stop", switch "move") with a string/dict response - a poor fit for `Camera.acquire_frame()`
returning a full image; the existing selector post-connect hook already works around this
for reads by fetching `service.connection(label)` directly rather than going through
`send_command()`, which is the same escape hatch a Camera/IlluminationSource's real work
(not just connect/disconnect) will use.

**Presented two options to the maintainer rather than guessing**: (a) extend
`DeviceCommunicationService` with a small additive driver-construction registry, matching
the "register instead of hardcode a branch" idiom `register_device_family()` already
established, so Camera/Illumination get full lifecycle management (BUSY state, labels,
connect/disconnect) like pump/valve/selector; (b) give Camera/Illumination their own,
separate lifecycle path entirely, touching `device_manager.py` not at all. Maintainer chose
(a).

**Implemented**: `register_driver_connect_factory(driver_key, factory)` in
`device_manager.py` - `factory(endpoint)` constructs the driver, connects it itself, and
returns `(connection, identity)`; `connection` must expose `._claim_owner`/`.is_connected()`/
`.close()`, the same surface `RegloICCClient`/`AMFSwitchController`/valve controllers already
implement and this service already calls generically via `getattr()` in
`disconnect()`/`status()`/`is_connected()` - confirmed by reading those three methods first:
only `_connect_impl()`'s construction step was ever hardcoded, the rest of the lifecycle
(disconnect, status, command dispatch, is_connected) was already fully generic. The registry
lookup is inserted in `_connect_impl()` **before** the existing valve catch-all branch
(`profile.type in {"switch","valve"} or profile.driver not in {"auto","unknown",""}`) - that
branch matches on "any non-empty/non-auto/non-unknown driver string," so a new driver key
registered *after* it in source order would have been silently routed into
`detect_valve_controller()` instead, a real landmine caught by reading the branch's actual
condition rather than assuming append-only was safe. Registered nowhere near the fluidics
branches themselves, which are byte-for-byte untouched. Exported from `lspr_acq_shell`'s
`__init__.py`.

**Verified**: 3 new unit tests
(`tests/unit/test_device_manager_driver_registry.py`, testing the real owner, not a shim) -
a registered driver key connects/disconnects/reports status generically; an unregistered
driver key still falls through to the pre-existing "unresolved" error, proving the new
check doesn't swallow a case it has no business handling. Full umbrella suite unaffected
(880 passed alone this run - the usual pre-existing Windows temp-dir flake didn't trigger
this particular run, consistent with it being intermittent per every prior entry
mentioning it). `pyflakes` clean.

**Camera device family registered** (`apps/LSPRi/acq/src/lspri_acq_app/device/registry.py`):
`register_device_family(CAMERA, ...)` with a real `discover_and_connect_camera` callback
(enumerates real Basler devices via `pypylon.pylon.TlFactory.EnumerateDevices()`, connects
the first one found via the new driver-factory registry) and
`register_driver_connect_factory(BASLER_DRIVER, ...)`. Calls the private
`controller._connect_and_setup()`/`controller._service` directly, matching exactly how the
three built-in families (`_pump_discover_and_connect` etc.) do it in `device_lifecycle.py` -
deliberate, not an oversight: the public `request_connect()` adds a busy-guard meant for the
manual on-demand "Connect" button path, which `run_full_cycle()`'s startup scan doesn't use
for any other family either, so using it here would make Camera behave inconsistently under
concurrent access compared to pump/switch/selector during their own full-cycle runs.
**PUMP/SWITCH/SELECTOR reuse deliberately NOT wired in yet** - the plan flags this as an
open question ("confirm this against your actual setup"), not a settled fact; guessing at it
would misrepresent a real rig decision. **ILLUMINATION not registered yet** - no real driver
exists for it.

**Real test-isolation bug found and fixed while writing this module's own tests**:
`register_device_family()` mutates process-global state in `lspr_acq_shell.device_lifecycle`
(`_DEVICE_FAMILIES`/`_DEVICE_FAMILY_ORDER`) with no unregister mechanism. First attempt at
`apps/LSPRi/acq/tests/test_device_registry.py` ran `python -m pytest tests/
apps/LSPRi/acq/tests/` together to double-check nothing broke - `tests/unit/
test_device_lifecycle.py`'s exact-3-built-in-families assertions failed, because importing
`lspri_acq_app.device.registry` (a side effect of test collection) had registered a 4th
family into the same global dict for the rest of that one Python process. Confirmed both
suites pass cleanly run **separately** (`apps/LSPRi/acq/tests/` 23/23, `tests/unit/
test_device_lifecycle.py` 37/37 alone, full `tests/` 865/865 alone) - this is not a bug in
the registration mechanism itself (real apps are separate processes, so this never happens
at runtime), only a test-suite combination hazard. Documented prominently in
`registry.py`'s own module docstring and here: **`apps/LSPRi/acq/tests/` must be run as its
own separate `pytest` invocation, never combined with the umbrella `tests/` suite** -
matches the existing, established precedent of `apps/sLSPR/acq/tests/` also being a
separate suite from the umbrella one, just newly load-bearing now that a second app
registers into the same shared global registry.

**Basler camera driver built** (`apps/LSPRi/acq/src/lspri_acq_app/device/basler_camera.py`)
- software-triggered single-frame acquisition per the plan's v1 scope
(`TriggerSelector=FrameStart`/`TriggerMode=On`/`TriggerSource=Software`, then
`StartGrabbing`/`ExecuteSoftwareTrigger`/`RetrieveResult`/`StopGrabbing` per frame, not
continuous streaming). Pixel-format/binning/exposure node calls
(`PixelFormat.Symbolics`/`SetValue`, `BinningHorizontal`/`BinningVertical` with `Average`
mode, `ExposureTime.Min`/`.Max` clamping) mirror
`spikes/lspri_acq_phase0/benchmark_ui.py`'s `PylonBackend` exactly - the one part of this
driver that IS real-hardware-verified (three real Basler-family cameras, Phase 0). Verified
the installed `pypylon` API surface directly (`dir(pylon.TlFactory.GetInstance())`,
`dir(pylon.DeviceInfo())`, `dir(pylon.InstantCamera)`) before writing any of this, rather
than assuming method names from memory - confirmed `CreateDevice`/`DeviceInfo.
SetSerialNumber`/`InstantCamera.Open`/`.GrabOne`/etc. all exist as documented.

**Explicitly NOT verified against real hardware** - no Basler camera was attached in this
environment (confirmed for real: `pylon.TlFactory.GetInstance().EnumerateDevices()` returned
0 devices at the time of writing). What IS verified for real, without needing a camera
physically present: `discover_basler_cameras()` genuinely returns `[]` right now (not
mocked); constructing `BaslerCamera` with an unknown serial number and calling `open()`
genuinely raises (confirmed manually first: `pylon.TlFactory.CreateDevice()` with a
nonexistent serial number raises a real pylon `RuntimeException`, "No device is available or
no device contains the provided device info properties" - `BaslerCamera.open()` wraps this
in `CameraError` with the same message). The software-trigger node sequence and single-shot
`GrabOne`-style acquisition are standard, well-documented GenICam patterns but have never
been exercised against a physical camera - **verify open/configure/acquire_frame/close
end-to-end against real hardware before relying on this for a real experiment**, matching
every other "built but not yet hardware-verified" caveat already carried in this log (pump/
valve/selector re-verification, 2026-08-06/08-08 entries).

**Verified**: 5 new `BaslerCamera` unit tests + 1 `discover_basler_cameras()` test
(`apps/LSPRi/acq/tests/test_basler_camera.py`) - all exercise real pypylon calls (no mocking
of pypylon itself), just against the real "no camera attached" state rather than a real
device. 2 new registry tests (`test_device_registry.py`) - deliberately avoid constructing a
real `DeviceLifecycleController`/`DeviceCommunicationService` (would touch real settings
files and, via `run_full_cycle()`'s PUMP/SWITCH/SELECTOR scan, real serial ports on this
machine - out of scope and risky for a device-registration unit test), instead calling
`_discover_and_connect_camera` directly, which is safe because the real "no camera found"
path returns before ever touching the controller argument. App's own suite: 23/23
(all of the above, run alone). Full umbrella suite: 864-865/865 across two runs (the one
intermittent failure both times was the same pre-existing Windows temp-dir race, not a new
one - confirmed by diffing which test failed against every prior entry mentioning it).
`pyflakes` clean on every new/touched file (one real finding along the way: the deliberate
side-effect import of `device/registry.py` in `app.py` needed the established `_ = (...)`
idiom, not `# noqa: F401` - bare `pyflakes` doesn't honor `# noqa`, same as every prior
entry that's hit this). App launched (`python apps/LSPRi/acq/src/main.py`, 10s), no
tracebacks.

**Not done**: `IlluminationSource`/VariSpec driver and family registration (next), Lori LED
driver, `ImagingExperimentControlBackend`, the sweep pipeline, HDF5 schema extension, GUI
panels - see section 12's checklist. Nothing from this entry committed yet.
