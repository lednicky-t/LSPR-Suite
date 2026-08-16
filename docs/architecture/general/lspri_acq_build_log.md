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

**Committed and pushed** *(2026-08-08, same day)*: submodule commit `10a6435` ("Add Basler
camera driver and register the CAMERA device family") pushed to
`lednicky-t/LSPRimaging-Acquisition`; umbrella commit `1420a74` ("Generalize
DeviceCommunicationService connect() for new driver types; bump LSPRi/acq") pushed to
`lednicky-t/LSPR-Suite` `develop`.

## 2026-08-08 (continued): VariSpec LCTF driver built, from the real manual

Continued into `IlluminationSource`/VariSpec, the next item. Read the actual manual
(`apps/LSPRi/acq/docs/manuals/cri-varispec-lctf-manual.pdf`, now in the repo) directly -
Chapter 3, "Controlling VariSpec Filters with Direct Serial Commands" - rather than relying
on the architecture plan's own paraphrase of it (§6.2), since a driver built from a
paraphrase risks re-introducing exactly the kind of subtle protocol mistake a primary
source would catch.

**Two things the plan's paraphrase got wrong or oversimplified, found by reading the
primary source**:
1. §6.2 cites "50ms VIS-range... 150ms NIR-range, per the manual's operating specifications
   table" - no such VIS/NIR-specific table exists in the manual. The real text (Appendix,
   glossary "Response Time" entry) is a single generic sentence: "Typically, this time is 50
   ms to 150 ms" - a broad range covering the whole VariSpec product family, not a
   per-model table. The Phase 0 spike's own empirical measurement against the real connected
   VIS unit (792 optically-measured transitions, direction-aware:
   `settle_time_analysis.md`) is both more specific to this actual hardware and captures a
   real effect (direction matters more than step size) the manual's generic figure says
   nothing about - used that as `settle_time_ms()`'s real basis instead (~40ms ascending,
   ~80ms descending/first-move-unknown).
2. The manual's "Command Nomenclature" section states plainly: "[normal/brief mode] only
   reply to queries" - a plain SET command (e.g. `W 550.000<c/r>`, not `W ?<c/r>`) produces
   **no reply at all**, only the echo every command gets (confirmed separately in the
   "Sleep" section: "Characters are echoed, even if asleep"). This directly matters for
   framing: a naive read-until-terminator-twice pattern for every command would hang for a
   full timeout on every single `set_wavelength()` call (called once per sweep step),
   waiting for a second terminator that will never arrive for a plain set.

**Real prior art used rather than re-derived**: the auto-memory note on this exact device
(`lspri-lctf-settle-time-measurement`, from the session that built the Phase 0 spike)
documents a real bug found against the real hardware: a fire-and-forget `W <nm>` write left
its echo unread in the RX buffer, which desynced the *next* command's echo/reply split
(`get_wavelength()` returned the literal text `"W ?"` instead of a number - the stale
unread echo, not a real reply). `illumination_probe.py`'s `VariSpecClient` (the spike code
that measured settle times against the real unit) already encodes the fix and the real
serial parameters (baudrate 115200, 8N1, `time.sleep(0.2)` + `reset_input_buffer()` after
opening "to let the virtual COM port settle before first write") - read that code directly
and adapted its framing logic rather than inventing a new one, since it's the one part of
this whole area that's actually been run against the physical unit.

**Design decision made while adapting, not copied verbatim**: the spike's `fire_wavelength()`/
`drain_echo()` split exists only because that code needed to stamp a timing measurement's
t=0 as precisely as possible (draining the echo costs ~15-20ms, the same FTDI
virtual-COM-port latency-timer floor documented for busy-check polling) - a real production
driver's `set_wavelength()` doesn't have that same hyper-precise-timing requirement (it's
followed by a 40-80ms settle sleep regardless), so `VariSpecLctf.set_wavelength()` drains
its own echo synchronously in one call, simpler than the spike's split, without
reintroducing the bug the split was built to work around (every command here always reads
its own echo before returning, never leaving one unread for a future call to trip over).

**A second correctness issue found by reading the manual carefully, not just skimmed**: the
manual states an error code "is stored until the error is cleared using the 'R' command, or
until another error occurs" - and explicitly recommends "first retrieving the Error Code and
then clearing the error condition before proceeding." An earlier draft of this driver read
`R ?` after every `set_wavelength()` to detect a rejected wavelength but never cleared it -
which would have made every subsequent *successful* step's `R ?` check see the same stale
error code and incorrectly raise again. Fixed before this was ever run: `_read_and_clear_error()`
clears (`R 1`) immediately after reading a nonzero code. Caught by a dedicated regression
test, not just by re-reading the manual a second time (see Verified, below) - also confirmed
the manual's own documented recovery behavior for an out-of-range `W`: the filter "stays at
the last legal wavelength" rather than moving, plus the `"*"` sentinel `W ?` can return
(Table 5's footnote, and a real bug the Phase 0 spike hit for real per its own
`get_wavelength()` docstring) - both handled: on error, `set_wavelength()` queries `W ?` for
the real current value (parsing `"*"` as "unknown," not crashing) rather than assuming the
rejected request took effect.

**Built** (`apps/LSPRi/acq/src/lspri_acq_app/device/variSpec_lctf.py`): `VariSpecLctf`
against the `IlluminationSource` ABC - `open()` sets Brief format (`B 1`, "cuts per-command
overhead," per the manual - matches the plan's own recommendation), reads firmware
revision/wavelength range/serial number via `V ?`, and only forces re-initialization (`I 1`)
if `I ?` reports not-initialized, rather than unconditionally re-initializing on every
open() (the manual notes older units can take 30s+ for this). `set_wavelength()` tracks
move direction (comparing against the previous wavelength) to pick `settle_time_ms()`'s
margin; the very first move (no previous wavelength yet) conservatively uses the
worst-case (descending) margin.

**Verified**: 13 unit tests
(`apps/LSPRi/acq/tests/test_variSpec_lctf.py`) against a fake serial port modeling the
real echo-then-reply framing (adapted from the existing `_FakeSerial` pattern already used
for `RegloICCClient` testing - `tests/unit/test_reglo_icc_calibration.py` - extended with
the query-vs-set echo distinction VariSpec's protocol has and Reglo's doesn't). One real bug
in the *test double itself* caught and fixed before any test passed: the first draft
attached a queued reply to whatever `write()` happened next regardless of whether it was a
query, so a reply meant for the `R ?` check after `set_wavelength()`'s `W` write got
consumed by that `W` write's own echo instead - fixed by only attaching a queued reply when
the written command contains `"?"`, matching the real device's actual behavior. Tests cover:
open()/close() against a real (nonexistent) COM port (genuinely raises/no-ops, not mocked);
successful and rejected `set_wavelength()` calls including the exact "error must be cleared
or it leaks into the next step" regression; direction-aware settle-time selection (first
move, ascending, descending); brief-mode `V ?` parsing. App's own suite: 36/36 (all of the
above plus everything from the prior two entries, run alone). Full umbrella suite: 864-865
across runs (same intermittent pre-existing flake as every prior entry, not a new one).
`pyflakes` clean.

**Not done, deliberately deferred rather than guessed at**: registering `ILLUMINATION` as a
device family. Traced `DeviceCommunicationService.refresh_device_ports()` before attempting
this and found a third instance of the same "only generalized for discovery, not fully"
pattern already hit twice this session (device family registration, 2026-08-06; connection
construction, earlier this entry's session): `ports_by_family` is still hardcoded to exactly
`PUMP`/`SWITCH`/`SELECTOR` (`RegloICCClient.list_ports()`/`SerialController.list_ports()`/
`detect_amf_selector_devices()`), so `ports.ports_for("illumination")` would always return
`[]` regardless of what's actually attached. CAMERA's `discover_and_connect` callback
already sidesteps this by ignoring `candidates` and doing its own `pypylon` enumeration
directly - the same approach works for ILLUMINATION (ignore `candidates`, scan serial ports
directly) without needing another `device_manager.py` change. What's genuinely unresolved,
and different from CAMERA's case: pypylon's `EnumerateDevices()` is a vendor-SDK call that
can only ever find real Basler cameras, so it's safe to call unconditionally; a serial LCTF
looks identical to *any other* "USB Serial Device" at the OS level, so a safe discovery
scan needs the same kind of port-safety heuristics pump/valve discovery already has
(`get_port_assignment`/`should_probe_port_for_role`, avoiding ports already assigned to a
different role) - guessing at a simplified version of that risked sending VariSpec-specific
probe commands (`V ?`) to a port that's actually the pump or selector. Left for a dedicated
pass rather than rushed.

**Committed and pushed** *(2026-08-08, same day)*: submodule commit `b0e7966` ("Add VariSpec
LCTF driver, built from the real protocol manual") pushed to
`lednicky-t/LSPRimaging-Acquisition`; umbrella commit `e1c46ff` ("Bump LSPRi/acq submodule:
VariSpec LCTF driver") pushed to `lednicky-t/LSPR-Suite` `develop`.

## 2026-08-08 (continued): ROI extraction, extinction math, and the three-thread sweep pipeline

Maintainer chose to continue into the sweep pipeline + domain math (over finishing
ILLUMINATION's device-family registration, or `ImagingExperimentControlBackend`) - the
load-bearing piece everything else (GUI, HDF5 writer) hangs off of, and fully testable
against the existing `SimulatedCamera`/`SimulatedIllumination` with no hardware or open
design questions blocking it.

**`processing/roi_extraction.py`**: bounding-box-cropped `RoiMaskSet`/`build_roi_mask_set`/
`extract_roi_means`/`RoiMaskCache`, following the exact performance pattern Phase 0 measured
and fixed (`spikes/lspri_acq_phase0/benchmark_ui.py`'s `RoiMasks`/`extract_roi_means` -
~60x faster than a full-image mask). Checked LSPRimaging Evaluation's own
`processing/roi.py` first, expecting to port it (per the architecture plan's general framing
of ROI code as portable) - found it still uses `np.indices()` over the *full image shape*
for every ROI, the exact O(image-size) bug Phase 0 found and fixed, and it extracts against
`RoiDefinition` (a different type entirely - generic rectangle/ellipse with a padding+width
background ring), not `AreaRoi`'s sample-disk + reference-annulus geometry already ported
into this app. Built fresh instead of porting: `AreaRoi`-shaped (disk + annulus) masks, with
the bounding-box-cropped approach.

**`domain/extinction.py`**: `absorbance_from_means` (`-log10(sample/reference)`, NaN where
either mean isn't positive) deliberately mirrors singleLSPR Acquisition's own
`compute_absorbance()` (`apps/sLSPR/acq/src/lspr_app/domain/session.py`) formula and
validity-gate convention for cross-app consistency - checked that function first rather than
inventing a different one, even though the *geometry* it applies to is different (sLSPR
divides a sequential sample spectrum by a separately-acquired reference spectrum; this app
divides two regions of the *same* frame at each swept wavelength). Documented as a
deliberate v1 simplification, not an oversight: no camera dark-current/bias subtraction step
- the architecture plan's own section 8 pipeline goes straight from cube to per-ROI means to
absorbance, with no dark-frame concept for imaging acquisition. `peak_absorbance` (simple
argmax over finite points) and `centroid_wavelength` (intensity-weighted centroid, baseline-
referenced) are a deliberately simpler reimplementation of the *idea* behind sLSPR acq's
`centroid_from_curve()` (`domain/processing.py`) - not a port - since that function's fuller
parameter set (`threshold_fraction`, a legacy no-threshold mode) is real complexity this app
doesn't need yet. No Gaussian/polynomial curve-fit metric built - the plan's own pseudocode
lists "centroid / peak / fit" as options, not all three required day one; left for a later,
explicitly-scoped pass rather than adding fitting complexity that wasn't asked for.

**`processing/cube_processing.py`**: `process_cube_for_rois(cube, rois, mask_cache,
on_result=...)` - the section 8 per-cube loop ("for each ROI: extract → build absorbance
spectrum → compute a metric"), ties the two modules above together. Reports results via a
callback instead of owning a sensorgram data structure itself - no sensorgram GUI panel
exists yet (section 10), so this module has no business deciding what
`sensorgram.append_point` means; a future panel supplies the callback.

**`acquisition/sweep_pipeline.py`**: `SweepController` (drives one wavelength sweep at a
time: `illumination.set_wavelength()` → settle → `camera.acquire_frame()` → repeat, building
a `SpectralCube`, then fans it out to a lossless save queue and a latest-only processing
queue), `SaveWriterThread` (dedicated, drains the lossless queue, `write_cube` is the
injection point a future HDF5 writer plugs into), `ProcessingThread` (drains the latest-only
queue, `process_cube` is the injection point - `process_cube_for_rois` typically), and
`build_sweep_pipeline()` (wires all three together with the right queue shapes). `queue.Queue`
+ `threading.Thread` throughout, not `multiprocessing.Queue` - per the plan's own section 8
reasoning (image-sized payloads make repeated pickling through `mp.Queue` expensive, unlike
sLSPR acq's spectrum-sized live-acquisition worker). `_queue_put_latest` (the
"replace-pending-item, don't enqueue behind it" idiom) is adapted from sLSPR acq's
`gui/workers.py::_queue_put_latest` - identical `put_nowait`/`get_nowait`/`Full`/`Empty` logic,
just retargeted from `multiprocessing.Queue` to `queue.Queue` (same API shape, no behavior
change).

**A real bug found and fixed before this was ever exercised under a real failure**: the first
draft's error path (`_run_one_sweep()` returns `None` on any exception, `_run()`'s loop just
calls it again immediately) would have spun into a tight retry loop against a persistently
failing camera - no delay between attempts, hammering the hardware and flooding logs
indefinitely. Fixed with a `_SWEEP_ERROR_BACKOFF_S = 0.5` backoff using
`self._stop_event.wait(...)` (not `time.sleep()`, so `stop()` still interrupts it promptly
rather than waiting out a full backoff period) - caught by writing the failure-path test
deliberately, not by inspection alone; the test asserts both that a persistently-failing
camera produces only 1-2 errors over ~0.6s (not dozens, proving the backoff exists) and that
`stop()` returns in under 0.5s during an active backoff (proving `stop()` still interrupts
it).

**Verified**: 32 new unit tests (`test_roi_extraction.py`, `test_extinction.py`) - masks are
genuinely bounding-box-sized not full-image-sized (asserted directly, not just "the code
looks right"), reference-annulus and no-annulus cases, cache identity/invalidation on
geometry change, absorbance validity gate, peak/centroid edge cases (all-NaN, flat curve,
too few points). 5 more (`test_sweep_pipeline.py`) targeting the pipeline mechanics directly
(latest-only queue replacement, the error-backoff regression above, and a test proving the
processing queue genuinely never exceeds 1 item even when deliberately never drained). 1
golden-path smoke test (`test_sweep_pipeline_smoke.py`, section 11/12's explicit requirement)
- 2 ROIs against `SimulatedCamera`/`SimulatedIllumination`, exercising save + processing
concurrently (the exact concurrency shape that would have caught a Lori-SW-style bug), both
ROIs produce a finite (non-NaN) sensorgram point, multiple cubes saved losslessly. App's own
suite run **three times in a row** given the threading involved (not just once) - 61/61 each
time, no flakes. Full umbrella suite (run separately, per the established convention) -
865/865, no regression. `pyflakes` clean. App launched (`python
apps/LSPRi/acq/src/main.py`, 10s) - unaffected by this entry (no GUI/app.py changes), no
tracebacks.

**Not done**: `storage/image_writer.py` (the real HDF5-backed `write_cube` - section 9/10),
GUI panels (image view, ROI panel), `ImagingExperimentControlBackend`, `ILLUMINATION` device
family registration (previous entry). Nothing from this entry committed yet.

## 2026-08-08 (continued): storage architecture decided - images are NOT in HDF5; both TIFF and OME-Zarr built, real-measured

Maintainer asked to go GUI-first next, but opened with a real architecture correction first:
images do not belong in HDF5 the way the original plan's section 9 described (matching sLSPR
acq's all-in-one-file convention) - HDF5 holds experimental data only (device status, spectra,
sensorgram, ROI definitions), images go to a **separate, user-selectable** TIFF-stack or
OME-Zarr store. Explicit design philosophy stated alongside this: "we are doing an
experimental app, so customization and flexibility is one of the main points, not setting
everything constant or hidden from user" - every storage knob (format, compression, shard
mode) must be user-choosable, not picked once by the app.

**Real benchmark run before any decision, not a guess**: maintainer asked two concrete
questions - relative write speed and space savings, specifically for OME-Zarr's ability to
keep up with a *live* camera stream (not eva's already-measured *batch* export case), plus
whether the camera's real 10-bit sensor resolution changes anything (TIFF only supports
16-bit; does pre-packing to 10 bits help). Built
`spikes/lspri_acq_storage_benchmark/benchmark_storage.py`, replicating eva's own hand-rolled
zarr v3 shard-write format (`_zarr_export_worker.py`'s `write_shard()` byte layout) but
**single-threaded** - matching how a live `SaveWriterThread` actually runs, not eva's
`ProcessPoolExecutor`-parallel batch exporter, which is a different claim than "119 MB/s" at
the same chunk size. Synthetic frames (Gaussian spots + Poisson-like shot noise, not random
bytes) at both Phase 0 configurations (full-res 3840x2160, 2x2-binned 1920x1080), both 10-bit
and 12-bit content ranges.

**Real findings, not assumptions** (full numbers in
`spikes/lspri_acq_storage_benchmark/storage_format_benchmark_findings.md`):
- eva's own lz4+bitshuffle setting is **borderline at full resolution on a single save
  thread** - per-cube write time (~495ms for 4 wavelengths) is close to *exceeding* the
  sweep's own per-cube pace (~440ms, from Phase 0's settle-time numbers) - the save queue
  would slowly grow over a long experiment. At 2x2 binning every compressed option
  comfortably keeps up - a second, independent reason (beyond Phase 0's capture-throughput
  finding) favoring binning as the default.
- Compression ratio depends entirely on codec/level, not "zarr vs. TIFF" as a category:
  lz4+bitshuffle (fast) only reached ~1.1-1.3x for this content, *worse* than TIFF's plain
  zlib (~2.0-2.4x); zstd-5 matched TIFF's ratio but was 4-5x too slow to write live at any
  tested resolution.
- Bit-packing to real 10-bit before compression doesn't meaningfully help throughput (the
  packing step's own CPU cost eats the savings) and isn't standard zarr-chunked layout -
  not worth pursuing; store as `uint16`, let the codec handle it.

Presented these findings and asked how to handle the full-res/lz4 tradeoff.
**Maintainer's answer reframed the ask**: keep *all* options (TIFF and OME-Zarr both, every
compression/shard setting), and instead of picking one, build the mechanism to *measure* how
each performs live, on the user's actual machine/camera/setup, since that varies rig to rig -
"these analyses are giving us some good default values and starting points," not fixed
policy. Also asked for setup guidance: exclude the save folder from antivirus scanning, and
set the correct shard grouping.

**Built** (`apps/LSPRi/acq/src/lspri_acq_app/storage/image_writer.py`):
- `StorageSettings` - the full user-choosable surface (format/compression/
  compression_level/shard_mode/chunk_size_px), defaults set from the benchmark's findings
  as starting points, not hard policy.
- `TiffCubeWriter` - one file per frame, named `WL<wavelength>Frame<cube_index>.tif` - the
  *exact* filename convention eva's reader (`IMAGE_PATTERN` regex,
  `apps/LSPRi/eva/src/lspr_imaging_app/io/dataset.py`) already parses. Verified for real:
  wrote frames, read them back with `tifffile.imread`, confirmed pixel-perfect (uncompressed
  and zlib).
- `OmeZarrCubeWriter` - grows a real zarr v3 array one cube at a time. Array/metadata
  structure (shape, chunks, shards, compressor declaration) goes through zarr's own
  `create_array`/`.resize()` API, so the result is a standard, valid zarr v3 dataset any
  zarr-v3-compliant reader can open; the actual per-cube pixel bytes are written with the
  same hand-rolled shard/index/CRC32C format the benchmark validated (bypassing zarr's slow
  async chunk-write path). Also writes the same `lspr` attrs group
  (`spectral_cube_indices`/`wavelengths_nm`/`chunk_size_px`/`shard_mode`/`compression`/
  `dtype`) eva's batch exporter writes, updated after *every* cube (not just at the end) -
  deliberate, so a dataset from an interrupted/crashed experiment stays readable for
  whatever cubes did complete, and eva's fast-read path
  (`_ome_zarr_fast_read_metadata`) works against it. `shard_mode="per_spectral_cube"` is the
  default (one file per cube) rather than eva's own `"per_image"` default (one file per
  wavelength per cube) - deliberately different, chosen for live writing specifically
  (fewer files as an open-ended experiment runs); `"per_image"` is still a supported option.
  Both `TiffCubeWriter`/`OmeZarrCubeWriter` implement a shared `ImageCubeWriter` protocol
  (`write_cube(cube) -> int` bytes written, `close()`), and `build_image_writer(settings,
  destination, ...)` is the factory a future settings UI will drive.

**Real cross-app compatibility proof, not just "valid zarr"** - the actual bar, since eva's
reader is what a scientist will use to analyze this app's output:
`tests/integration/test_lspri_acq_zarr_compat.py` (umbrella-level, not either app's own
suite, since it's specifically about the integration point between two otherwise decoupled
apps) imports `lspr_imaging_app.io.dataset.load_ome_zarr_dataset()` and `load_image_array()`
**unmodified** and confirms they correctly read back pixel-perfect data written by the new
writer, for both shard modes and with/without compression. 3/3 passed.

**`SaveWriterThread` (acquisition/sweep_pipeline.py) now tracks live save-lag metrics** -
queue depth (current + max seen), write latency (rolling 200-sample window, avg/max), bytes
written, cubes written - the exact pattern already validated in the Phase 0 spike's own
`SaveWriterThread` (`benchmark_ui.py`), generalized from per-frame to per-cube. This directly
answers the maintainer's "implement the metrics" ask: a running experiment can now show,
live, whether the chosen format/compression/resolution is actually keeping up on *this*
machine - the exact scenario the benchmark found borderline for lz4 at full resolution.
`SaveWriterThread`'s constructor now takes a `writer: ImageCubeWriter` object (not a bare
`write_cube` callable) so it can also call `.close()` on stop and read back bytes-written per
cube for the metrics - a small, contained interface change; updated the one existing test
that used the old callable form.

**Setup guidance written** (`apps/LSPRi/acq/docs/storage_setup.md`): antivirus exclusion for
the save destination folder (eva's own `TODO.md` already documents this exact problem class
for naive zarr chunking on Windows - real precedent, not a hypothetical), why
`shard_mode="per_spectral_cube"` is the live-write default vs. eva's `"per_image"` batch
default, and the uncompressed-live-then-recompress-after fallback (reusing eva's already-
parallelized batch exporter) if live compression doesn't keep up on a given machine.

**Verified**: 18 new writer unit tests (round-trip read-back for both formats, both shard
modes, with/without compression, error paths) + 3 new cross-app compatibility tests +
existing pipeline tests updated for the writer-object interface change. App's own suite run
three times given the threading involved - 90/90 each time, no flakes. Full umbrella suite -
868/868 (865 + 3 new), no regression. `pyflakes` clean.

**Plan doc corrected in two places while writing this up** (section 9 rewritten for the
real storage split; section 10's ROI-panel item's claim that eva's `roi_editor_tools.py` is
"reusable as-is" corrected - checked before assuming, found it's built entirely around a
different ROI type (`RoiDefinition`, not this app's `AreaRoi`), fresh `AreaRoi`-shaped
helpers built instead - `domain/roi_editor_tools.py`, not wired into a panel yet since that's
the next, deferred piece of work).

**Not done**: the GUI work this entry's session was originally asked to start with - image
view panel, ROI panel, a settings UI to actually drive `StorageSettings`, and a live display
of `SaveWriterThread.stats()`. `domain/roi_editor_tools.py` (AreaRoi-shaped geometry helpers)
was built and tested in isolation but not yet wired into a panel. Recompression-after-
acquisition (the uncompressed-live fallback) is designed but not implemented - no code reuses
eva's batch exporter yet.

**Committed and pushed** *(2026-08-08, same day)*: submodule commit `f96f4ee` ("Add TIFF and
OME-Zarr image writers with live save-lag metrics") pushed to
`lednicky-t/LSPRimaging-Acquisition`; umbrella commit `2c1cc89` ("Storage architecture: images
separate from HDF5, TIFF/OME-Zarr benchmark") pushed to `lednicky-t/LSPR-Suite` `develop`.

## 2026-08-08 (continued): image view + ROI panel built, screenshot-verified, one real crash found and fixed

Continued into the GUI work this session was originally asked to start with -
`domain/roi_editor_tools.py` (AreaRoi-shaped geometry helpers) was already built in the
previous entry; picked up from there.

**`gui/image_view_panel.py`**: wraps `pyqtgraph.ImageView`, hiding the line-profile ROI
button and settings/export menu (not part of v1's scope - manual sample/reference ROI
placement lives in `roi_panel.py` instead), matching the same hide-these-two pattern already
validated in the Phase 0 spike. `show_frame(image)` calls `setImage(image.T, ...)` - the
`.T` matters: pyqtgraph's native (x, y) plot axes are otherwise swapped relative to a numpy
`(height, width)` array's natural row/column order, and `AreaRoi.center_x`/`center_y` (and
`processing/roi_extraction.py`) already assume conventional image (column, row) coordinates -
confirmed this was the exact reason the Phase 0 spike did the same transpose, not a new
finding, but re-verified it actually holds here (screenshot below shows correctly-oriented,
non-mirrored spots).

**`gui/roi_panel.py`**: one draggable/resizable `pg.CircleROI` per `AreaRoi`'s sample disk
(`sigRegionChangeFinished` synced back to the model via `move_roi()`/`roi_outer_radius_px()`
from the previous entry - clamping uses the *outer* radius, i.e. the reference annulus's
edge if configured, not just the sample disk, so a large reference ring can't be dragged
half off-image while its small sample disk still looks "in bounds"), plus two static
(non-interactive, `setAcceptedMouseButtons(NoButton)`) circles marking the reference
annulus, repositioned to track the sample item automatically. A side list (add/select/
delete, numeric reference-inner/outer-diameter spin boxes for the selected ROI, outer
diameter clamped to never go below inner) - deliberately not a full drag-resizable reference
ring for v1 (numeric editing is simpler and sufficient for "manual placement/editing only,"
the plan's own v1 bar). Checked eva's `ImageInteractionController`/`OverlayManager` first
rather than assuming a rewrite was needed - confirmed (per the existing, already-corrected
section 10 note) they're genuinely Qt-coupled to eva's specific `MainWindow` with no
reusable seam; this is a fresh, deliberately much smaller implementation, not a port.

**Wired into `main_window.py`**: replaced the placeholder status label with a real
`RoiPanel`, populated at startup with one frame from a `SimulatedCamera` (already-tested v1
code path, not a shortcut around real device wiring) and two example ROIs - proves the
image view + ROI overlay work end to end against real frame data, without yet needing the
not-built sweep-pipeline-to-GUI wiring or experiment-control flow. Deliberately does not
start a live sweep loop.

**A real crash found and root-caused, not dismissed as a fluke**: the first test run of
`test_roi_panel.py` hit `Windows fatal exception: access violation` during Python's cyclic
garbage collection, at an unpredictable point a few tests into the file (not the same test
each run). Investigated properly before assuming it was the known unrelated Qt/Windows COM
quirk documented elsewhere in this log (2026-08-08, fluidics device-layer entry) - that one
was confirmed via `git stash` to reproduce against *unmodified* code; this one only started
happening once `RoiPanel` tests existed, so re-using that explanation without checking would
have been exactly the kind of unverified assumption this project avoids. Root cause: each
test's `setUp()` created an unparented `RoiPanel` (a `QWidget` holding `pg.CircleROI` items
added directly to a `ViewBox`'s scene graph, not plain Qt child widgets) and never explicitly
destroyed it - Python's GC would eventually collect several accumulated panels at once,
whenever the cyclic collector happened to run, and pyqtgraph's ViewBox/GraphicsItem C++
teardown order under that circumstance triggered the access violation. Fixed by adding
explicit, deterministic cleanup (`widget.close()` + `deleteLater()` + `QApplication.
processEvents()`) via `self.addCleanup(...)` in every test's `setUp()`, right after
constructing the panel - confirmed the fix by running the full file 5 times in a row with no
crash (it had reproduced on both of the first two attempts, at different tests each time,
before the fix).

**Verified**: 14 new Qt widget tests (`test_roi_panel.py`) - add/remove, next-id assignment,
overlay item creation/removal, simulated drag-and-resize (moves the underlying `CircleROI`
directly via `setPos`/`setSize` then invokes the same `sigRegionChangeFinished` handler a
real drag triggers, rather than a full mouse-event harness) confirming model sync,
edge-clamping, reference-overlay repositioning, the `on_rois_changed` callback, and
reference-diameter editing with the outer-below-inner clamp. Real screenshot taken via
`pywinauto` (same convention as the earlier scaffold screenshot) - two simulated Gaussian
spots, correctly oriented, with orange sample-disk and blue dashed reference-annulus overlays
positioned exactly on the spots, ROI list showing correct coordinates/radii. App's own suite
run three times given the Qt/pyqtgraph object lifecycle work - 104/104 each time, no crashes,
no flakes. `pyflakes` clean.

**Not done**: image-processing panel (crop/rotate/background-flatten), a live sweep feeding
this view (currently one static startup preview frame only), experiment-control panel reuse,
`ILLUMINATION` device family registration, `ImagingExperimentControlBackend`.

**Committed and pushed** *(2026-08-08, same day)*: submodule commit `b2dee0c` ("Add image view
+ ROI panel, wired into the main window") pushed to `lednicky-t/LSPRimaging-Acquisition`;
umbrella commit `dcffef3` ("Bump LSPRi/acq submodule: image view + ROI panel") pushed to
`lednicky-t/LSPR-Suite` `develop`. Maintainer asked to defer further image-panel work (image
processing panel, live sweep wiring) and continue with `ILLUMINATION` device family
registration instead.

## 2026-08-08 (continued): ILLUMINATION device family registered, with a real safe-discovery mechanism

Picked up the item deferred twice already (§6.1's first pass, and again when the VariSpec
driver itself was built) - registering `IlluminationSource`/VariSpec as a device family
needed a safe port-discovery strategy, not a guess.

**Checked the obvious existing safety mechanism before reusing it - and it doesn't do what
its name suggests for a new caller.** `should_probe_port_for_role(port, role)`
(`lspr_acq_shell.port_assignments`) looks exactly like the function to call here. Traced its
actual body: `if role_name not in {"pump", "switch"}: return True` - for any role name
outside that hardcoded pair, including `"illumination"`, it unconditionally returns `True`.
That's a silent no-op, not "no restriction needed" - calling it with `role="illumination"`
would have *looked* like a safety check while providing none: a port the user manually
pinned to `"pump"` in Preferences would still get probed with VariSpec-specific ASCII
commands. This is the third instance this session of the same underlying pattern (discovery
dispatch, then connection construction, now port-assignment safety) - something in
`lspr_acq_shell` generalized for pump/switch/selector specifically, not generically, in a way
that silently doesn't protect a new caller unless actually read first.

**Built a narrower, correct check instead of extending the shared function** - deliberately
*not* a `lspr_acq_shell` change this time (unlike the driver-connect-factory addition, this
doesn't need to be shared/generic - it's specific to "which ports may this app's illumination
discovery safely try"). `_candidate_illumination_ports()` (`device/registry.py`) checks
`get_port_assignment(port) != "auto"` directly (any manual assignment at all means "not this
one" - there's no "illumination" assignment for a user to set today, so any assignment
present must be pump/switch) plus `port_owners(port)` (`lspr_acq_shell.connection_registry` -
confirmed genuinely generic, no hardcoded role names, unlike `should_probe_port_for_role`) to
also skip anything currently claimed by a live connection, even if never manually pinned.

**`discover_varispec_port()` (`variSpec_lctf.py`) - discovery needs stricter validation than
`open()` alone provides.** Checked `open()`'s own behavior first: a garbled/foreign reply
during its `V ?` handshake doesn't raise - `_read_info()` just leaves `wavelength_range()` as
`None`, matching every other driver's "don't be overly strict on connect" philosophy (correct
for a user-initiated connect to a port they already chose, wrong for discovery scanning an
unknown port). So `discover_varispec_port()` opens each candidate and only accepts one where
`wavelength_range() is not None` afterward - "didn't raise" alone isn't enough, since an
unrelated device on some other candidate port could open a serial connection successfully
without being a VariSpec at all. Opens and closes a throwaway driver instance per candidate;
the real, final connection happens separately via the driver connect factory once a port is
chosen - discovery and connection stay two distinct steps, matching the CAMERA family's own
shape (`discover_basler_cameras()` doesn't hold an open camera handle either).

**Registered** (`device/registry.py`): `_illumination_driver_connect_factory` (constructs +
opens a real `VariSpecLctf`, reports `model`/`wavelength_range_nm` identity) via
`register_driver_connect_factory(VARISPEC_DRIVER, ...)`; `_discover_and_connect_illumination`
(filters candidates, calls `discover_varispec_port()`, then `ensure_device_profile()` +
`controller._connect_and_setup()` - same pattern as `_discover_and_connect_camera`, same
reasoning for using the private method over `request_connect()`) via
`register_device_family(ILLUMINATION, ...)`. Canonical label `illumination_1`, matching the
fixed-label convention every other family uses (never resolved by fingerprint search - see
incident #31 in `DEVICE_LAYER_AUDIT_2026.md`, the precedent this rule exists to prevent a
recurrence of).

**Verified**: 4 new tests in `test_variSpec_lctf.py` (`discover_varispec_port()` - no
candidates, a real nonexistent-port rejection, and, against the fake serial port since no
physical VariSpec exists in this environment, both "finds a port with a valid identity" and
"rejects a port with no plausible identity"); 8 tests in `test_device_registry.py` (family
registration, missing-candidates and no-VariSpec-found paths, and three
`_candidate_illumination_ports()` filtering tests - manually-assigned ports excluded,
actively-claimed ports excluded, unassigned/unclaimed ports included - each with fake
`comports()`/`get_port_assignment`/`port_owners` so no real hardware or settings file is
touched). App's own suite run three times - 114/114 each time, no flakes. Full umbrella suite
- 868/868 (no regression, and this run's usual intermittent Windows temp-dir flake didn't
trigger). `pyflakes` clean.

**Not done**: `ImagingExperimentControlBackend`, image-processing panel, live sweep wiring
into the GUI, Lori LED driver.

**Committed and pushed** *(2026-08-08, same day)*: submodule commit `a189614` ("Register
ILLUMINATION device family with a safe port-discovery strategy") pushed to
`lednicky-t/LSPRimaging-Acquisition`; umbrella commit `1b10697` ("Bump LSPRi/acq submodule:
ILLUMINATION device family registered") pushed to `lednicky-t/LSPR-Suite` `develop`.

## 2026-08-09: `ImagingExperimentControlBackend` renamed and scoped; Tier 0 experiment-control extraction landed

Maintainer asked what `ImagingExperimentControlBackend` actually was (explained: the
concrete adapter LSPRi acq needs to write against `lspr_acq_shell`'s
`ExperimentControlBackend` `Protocol`, mirroring sLSPR acq's own
`AcquisitionExperimentControlBackend`), then gave two real answers that reframe this item:
(1) **confirmed** this app does drive the same pump/valve/selector fluidics system as sLSPR
acq (resolving §6.1's "confirm this against your actual setup" open question), and wants
the *same* experiment-control panel with full functionality, reused rather than rebuilt;
(2) flagged the name itself as unclear - "Imaging" doesn't say what the class does, and is
now ambiguous anyway since both acquisition apps are "imaging" in the broad sense. Renamed
to `LspriAcqExperimentControlBackend` (matches how the codebase already names things -
`lspri_acq_app`, "LSPRi acq" throughout the docs) - not implemented yet, just renamed in
the plan doc, since the maintainer's other point (reuse the *same* panel) reframes this
from "write an adapter against an existing shared backend" to "the backend the panel needs
doesn't fully exist as shared code yet either."

**Real research before deciding extract-vs-rewrite, not a guess**: the experiment-control
panel (`experiment_control_window.py` + 14 satellite files) is ~11,459 lines total,
including real safety-critical logic (it decides what commands actually get sent to real
pump/valve/selector hardware). Dispatched a thorough investigation - per-file line counts,
grepped real `window.`/`self._window` reach-through counts (not descriptions, mirroring
the plot_controller.py precedent from Phase 1's 1.3.4 entry), what's Qt-coupled-but-
already-decoupled vs. genuinely window-entangled, existing test coverage, and V49's own
proposed module split compared against what's actually built. Full findings: this session's
conversation; summary in the plan doc's now-updated `LspriAcqExperimentControlBackend`
checklist item.

**4-tier plan presented and approved before touching any code**: Tier 0 (~1,218 lines, zero
window coupling, already tested - plan import/export, run/hold/pause/stop state naming, the
step-command hardware-dispatch mechanism) - move to `lspr_acq_shell` now, low risk. Tier 1
(~1,077 lines, Qt-heavy but already self-contained widgets - the plan timeline and table
views) - move next, still low risk. Tier 2 (~1,287 lines - `_plan_step_commands`, the
actual safety-critical "what to send" logic, plus the run/hold/pause/stop timer loop) -
needs real redesign (currently reads live widget values directly, e.g. a spinbox's
`.value()`, instead of explicit parameters) before either app can share it - flagged as
needing the maintainer's real-hardware sign-off before either app relies on a refactored
version. Tier 3 (~2,417 lines - dialogs, cell-selection/drag-fill editing) - a rewrite
candidate, not a port, matching this project's own established precedent with LSPRi eva's
`OverlayManager`. Maintainer approved starting with Tier 0 this session.

**Tier 0, executed**: found before moving anything that `pump_plan.py` (the `PumpPlanStep`
domain model `experiment_control_import.py` and `_step_runner.py` both depend on) wasn't
actually in `lspr_acq_shell` yet either - not originally named as part of "Tier 0," but a
real, necessary dependency, traced rather than assumed. `pump_plan.py` itself was 249 lines
with exactly one app-specific dependency: `to_core_experiment_plan()` imported
`APP_VERSION` directly from `lspr_app.version` and stamped it into the returned plan's
identity metadata - a backward dependency a shared package must not have (the same problem
class already fixed once this project, for `device_lifecycle.py`'s spectrometer-stage
import). Fixed by making `app_version` a required keyword argument on the shared version;
sLSPR acq's own `domain/pump_plan.py` (now a shim) preserves the *exact* original call
signature (`steps, *, app_name="LSPR Acquisition"`) by supplying `app_version=APP_VERSION`
itself, so none of the 5 existing call sites in `experiment_control_window.py`/
`flow_plan_model.py`/`main_window_state.py`/`storage/hdf5_export.py` needed to change -
verified directly (constructed a plan through the shim, confirmed `app_version` came out
as sLSPR acq's real `'0.4.0'`, not a placeholder). The two purely-internal
`to_core_experiment_plan()` calls inside `pump_plan.py` itself
(`recompute_plan_timing`/`steps_to_hdf5_rows`) use a placeholder `app_version="n/a"` -
confirmed safe by tracing both call chains: neither ever reads the returned plan's
`.identity`, only `.steps` or the row table built from them.

**PyYAML was an implicit, undeclared dependency** - `experiment_control_import.py`/
`_export.py` both import `yaml`, but `lspr_acq_shell`'s own `pyproject.toml` didn't list
it; this only worked because sLSPR acq's `pyproject.toml` does, and both packages end up
installed in the same environment. Fixed by adding `PyYAML` to `lspr_acq_shell`'s own
declared dependencies - a shared package should be self-sufficient for what it exposes, not
implicitly rely on a sibling app happening to have installed something.

**Moved** (all four Tier 0 GUI files, verbatim except the one import-path change from
`lspr_app.domain.pump_plan` to `lspr_acq_shell.pump_plan`): `experiment_control_runtime.py`
(132 lines, confirmed zero coupling - moved with no changes at all),
`experiment_control_export.py` (97 lines, zero coupling), `experiment_control_import.py`
(847 lines - the CSV/TSV/native-YAML/HDF5 plan parsers, effectively zero coupling per the
research pass), `experiment_control_step_runner.py` (142 lines - the actual hardware-command
dispatch mechanism, zero coupling, already calling into `DeviceCommunicationService` which
Phase 1 already shared). sLSPR acq's five originals (`pump_plan.py` +the four GUI files)
are now thin re-export shims, following the exact convention established across every prior
Phase 1 extraction.

**Tests repointed to the real owner, not left on the shim** - matching the convention from
every Phase 1 extraction: `tests/unit/test_experiment_control_runtime.py` (already at
umbrella level, just repointed) and `apps/sLSPR/acq/tests/test_experiment_plan_import.py`
(**moved** to `tests/unit/`, matching the 1.3.6 precedent of relocating a test file once its
subject module leaves the app - not just repointed in place) both now import from
`lspr_acq_shell` directly. Two more tests (`tests/integration/test_acq_hdf5.py`,
`tests/unit/test_experiment_control_step_apply_overlap.py`) had exactly one import line each
repointed - their actual subject is other code (`AsyncHDF5MeasurementWriter`,
`ExperimentControlWindow`'s overlap-safety logic) that merely uses a moved class/function as
a fixture, so only that one line needed fixing, not a full relocation.

**Verified**: full umbrella suite 894/894 (868 baseline + 27 relocated
`test_experiment_plan_import.py` tests - the one usual intermittent Windows temp-dir flake
didn't trigger this run, consistent with every prior entry mentioning it as intermittent).
sLSPR acq's own app-level suite: 14/22 passed *before* this change too (verified for real via
`git stash` - the same 8 failures, byte-identical, reproduce against completely unmodified
code; a pre-existing, unrelated issue in `apps/sLSPR/acq/tests/test_device_manager_locking.py`/
`test_archive_backed_rolling_cache.py` patching a module-level name that no longer lives
where they expect, the same class of stale-patch-target issue documented multiple times in
this file for other files during Phase 1 - not something this change introduced or is
responsible for fixing). `pyflakes` clean on every new/touched file. sLSPR acq launched
twice (Simulation profile, `LSPR_FORCE_SIMULATOR=1`) - once headless (no tracebacks in the
log) and once screenshotted via `pywinauto` - startup splash reached 100%/"Ready." with the
correct real version (`ver. 0.4.0`, confirming the `APP_VERSION` shim chain resolved
correctly at actual app startup, not just in isolated tests).

**Not done**: Tiers 1-3 of the experiment-control extraction/rewrite plan (shared timeline/
table widgets; the safety-critical step-command-decision redesign; the dialogs/editing
rewrite), `LspriAcqExperimentControlBackend` itself (still blocked on Tier 2's redesign - the
Protocol's `device_states()` needs real device-family keys the window's state machine
produces, which isn't reusable yet), image-processing panel, live sweep wiring into the GUI,
Lori LED driver. Nothing from this entry committed yet.

## 2026-08-09: Tier 1 experiment-control extraction landed (shared timeline/table widgets)

Continued the 4-tier plan approved the same day (see the entry above) into Tier 1: the plan
timeline and table-view widgets, estimated at ~1,077 lines and flagged as "Qt-heavy but
already self-contained."

**Traced real coupling before moving anything**, the same discipline used for Tier 0's
`pump_plan.py` finding: `experiment_control_timeline.py` (792 lines, `PumpPlanTimelineWidget`
- a custom-painted zoom/pan/drag-reorder timeline) turned out to depend only on
`PumpPlanStep`/`recompute_plan_timing` (`pump_plan.py`, already in `lspr_acq_shell` since
Tier 0) and `DeviceLifecycleController`/`SELECTOR` (`device_lifecycle.py`/`device_types.py`,
already shared since Phase 1) - no live reference to `ExperimentControlWindow` anywhere in
the file. `experiment_control_widgets.py` (287 lines - `ExperimentControlTableView`,
`PlanColorDelegate`, `TubeDiameterComboBox`, `_NoFocusItemDelegate`,
`_make_frameless_icon_button`) depends only on `pump_plan.py` constants
(`DEFAULT_TUBE_MM`/`TUBE_DIAMETER_OPTIONS`/`nearest_tube_diameter_option`). Both files'
docstrings already claimed self-containedness; confirmed it for real via grep on
`window.`/`self._window` reach-through rather than trusting the claim, same as every prior
tier.

**Moved** both files verbatim to `lspr_acq_shell`, with only the import path changed from
`lspr_app.domain.pump_plan`/`lspr_app.device.device_lifecycle`/`device_types` to their
`lspr_acq_shell` equivalents (plus two docstring references in `experiment_control_timeline.py`
that named the old module path in prose, not just imports). sLSPR acq's two originals are now
thin re-export shims, following the same convention as every prior extraction.

**Tests repointed to the real owner**: `tests/unit/test_experiment_control_step_overlay_label_mode.py`
imports `PumpPlanTimelineWidget` directly (to exercise `_step_label_text` against a bare
`SimpleNamespace` stand-in) - repointed to `lspr_acq_shell.experiment_control_timeline`.
Confirmed via grep this is the only test importing either moved module directly;
`test_experiment_control_timeline_font.py` imports `PumpPlanTimelineWidget` indirectly
through `experiment_control_window.py`'s own re-import chain and needed no change.

**Umbrella `pyflakes` pre-commit hook caught a real pre-existing dead-code line** that the
submodule's own git history had been carrying silently: `experiment_control_timeline.py`
line 380 computed a local `progress_s` inside an already-documented-unreachable branch (the
parent never sets `_plan_active_row`) and never read it again - marked `# noqa: F841` in the
original, but the umbrella's hook runs bare `python -m pyflakes` (not `ruff`), which doesn't
honor `noqa` at all, so it only ever went unnoticed because this file lived solely in the
submodule before Tier 1. Confirmed the assignment was truly dead (nothing downstream in the
branch reads it) and deleted the line - zero behavior change, since nothing consumed the
value; the branch itself is still unreachable and still intentionally kept, per the adjacent
comment.

**Verified**: full umbrella suite 895/895 (894 baseline + the one newly-added driver-registry
test from earlier this session), `pyflakes` clean on both new files and both shims after the
dead-code fix above. sLSPR acq's own app-level suite: same 14/22
with the same 8 pre-existing, unrelated failures as the Tier 0 entry documented, no new
failures. Because this tier moved actual custom-painting/rendering code (not just data
plumbing), a visual check mattered beyond passing tests: launched sLSPR acq
(`LSPR_FORCE_SIMULATOR=1`) and screenshotted the running window - the Experiment Control
table (`ExperimentControlTableView`/`PlanColorDelegate`) and the timeline bar both render
correctly, no visual glitches or missing widgets.

**Not done**: Tiers 2-3 of the extraction/rewrite plan (the safety-critical step-command-
decision redesign and run/hold/pause/stop state machine; the dialogs/editing rewrite),
`LspriAcqExperimentControlBackend` itself (still blocked on Tier 2), image-processing panel,
live sweep wiring into the GUI, Lori LED driver.

## 2026-08-09: Tier 2 scoping + characterization tests (before any restructuring)

Started Tier 2 (the safety-critical decision logic + run/hold/pause/stop state machine).
Traced real coupling before proposing anything, same discipline as every prior tier: Tier 0
already moved `_StepApplyRunnable`/`_PlannedCommand` (the actual hardware-dispatch
mechanism), so the only piece still deciding *what* commands go to the pump/valve/selector
is `_plan_step_commands()` (~160 lines) - which turned out to have almost no window
coupling (one live widget read, `manual_tube_spins[i].value()` for tube diameter - a
per-channel setup value, not plan data). The run/hold/pause/stop timer loop is a different
story: genuinely entangled with recording, the timeline widget, table row selection, and
the status bar - and its guard flags (`_plan_running`/`_plan_holding`/`_plan_paused`) are
read at 250+ other sites across the 6,165-line file (mostly editing-lock guard conditions
elsewhere, not the state machine itself).

Presented this to the maintainer as a scope choice - share only the decision function
(lower risk, leaves sLSPR acq's tested run loop untouched) vs. also sharing the state
machine behind an abstract host interface (more reuse, touches tested code, LSPRi acq's own
run loop will need sweep-pipeline hooks sLSPR acq never had anyway). **Maintainer chose the
full-scope option** - share the state machine too.

Before writing any restructuring code, flagged a second real finding: only 10 tests
(`test_experiment_control_step_navigation.py`) directly exercise this state machine, thin
for logic that decides when real hardware commands fire. Given the choice was made without
that number in view, went back rather than silently proceeding - maintainer chose to write
thorough characterization tests against the *current, unmodified* code first, then
restructure with those tests as the safety net.

**Characterization tests written** (`tests/unit/test_experiment_control_run_loop_characterization.py`,
53 tests, all against unmodified `experiment_control_window.py`): every state transition
(idle->running->holding->paused->stopped, including no-op guards for invalid transitions
like holding a stopped plan), the auto-advance timer callback (`_advance_experiment_control_progress`
- step-apply-in-flight retry-at-50ms, mid-step elapsed updates, step-to-step advance,
finish-on-last-step), `_schedule_plan_timer`'s exact interval selection (150ms poll cadence
for hold/pause/not-yet-started, remaining-time-clamped-to-[1,150]ms while actively running),
manual step jump/apply behavior differing by current state (idle: select only; running:
reset clock and re-apply in place; holding/paused: resume the plan at the new row), and the
HOLD-vs-PAUSE distinction (`_enter_hold_state` sends no hardware command at all; `_enter_pause_state`
applies a configurable pause-template step via `_apply_step_to_pump_async(..., start=False)`).
Followed the existing bare-`__new__` + stubbed-collaborator pattern from
`test_experiment_control_step_navigation.py` (no real Qt window construction); every stub
point doubles as a first draft of the `PlanRunHost` Protocol boundary the actual extraction
will need. Found and fixed 3 test-authoring mistakes of my own by running against real code
(a `monotonic()` mock with a stray second value never consumed; two tests that had
over-stubbed `_set_experiment_control_runtime_row` instead of letting its real
pass-through implementation run, so they weren't actually exercising the call chain they
claimed to). **Mutation-tested the suite for real, not just run it**: temporarily deleted
one state-clearing line from `_enter_hold_state` (`self._plan_started_monotonic = None`) and
confirmed a test failed with the exact expected assertion, then reverted - concrete evidence
the tests catch real regressions, not just passing trivially.

**Verified**: full umbrella suite 948/948 (895 baseline + 53 new), `pyflakes` clean.

**Not done**: the actual `PlanRunHost` Protocol design and `_plan_step_commands`/state-
machine extraction into `lspr_acq_shell` - next step, with this test file as the safety net.
Nothing from this entry committed yet.

## 2026-08-09: Tier 2, step 1 - `_plan_step_commands` extracted as a pure, shared function

With the characterization-test safety net in place, started the actual extraction. Did the
narrow, already-fully-scoped half first (the decision function that determines what hardware
commands a step transition requires), saving the harder state-machine-sharing half for next.

**One more real dependency found while extracting** (same discipline as every prior tier -
trace, don't assume): `_plan_step_commands` also called `normalized_pump_direction` from
`lspr_app.gui.flow_plan_model` - a small, genuinely pure function (`"CCW" if ... else "CW"`)
but living in an app module the shared package can't import from. Moved it, plus its two
natural siblings (`normalized_valve_state`, `clamped_switch_position` - the same "one raw
step-field value -> validated field" family, per `flow_plan_model.py`'s own docstring), into
`lspr_acq_shell.pump_plan`. `flow_plan_model.py` now re-exports all three under their
original names, so its own 2 other call sites and `experiment_control_window.py`'s 2 other
call sites needed no changes.

**New module**: `lspr_acq_shell/experiment_control_step_decision.py` - `plan_step_commands()`,
a pure function (no Qt, no `self`, no device I/O) taking the step, the previous step, and a
new `StepCommandContext` dataclass bundling every real dependency traced out of the original
(device-connection flags, device labels, tube diameter per channel, pump backsteps/roller-
count settings, the pump-display-enabled flag, `wait_for_mswitch_first`) - moved verbatim
otherwise, including its exact command-ordering logic and the OFF-direction-with-nonzero-
flow fix's comment. Kept the original's single combined log line exactly byte-for-byte
(had to route `_service_connection_detail(SWITCH)` - window-specific, since it queries the
live device connection object - through two new context fields,
`switch_controller_type`/`switch_port`, rather than logging it separately in the window
wrapper, after a first draft accidentally split it into two log lines).

**`experiment_control_window.py`'s `_plan_step_commands` is now a thin wrapper**: gathers
the explicit inputs (including `[spin.value() for spin in self.manual_tube_spins]` - the one
live widget read) into a `StepCommandContext` and delegates. sLSPR acq gets a new shim,
`gui/experiment_control_step_decision.py`, following the established convention.

**Verified**: full umbrella suite 963/963 (948 baseline + 15 new direct unit tests for the
pure function in `tests/unit/test_experiment_control_step_decision.py` - valve/switch command
generation, disconnected-device status messages, `wait_for_mswitch_first` ordering, the
pump-display command, tube-diameter passthrough, and the OFF-direction-with-nonzero-flow
regression re-proven directly against the pure function, not just through the window). The
one flaky test this run (`test_async_writer_reports_failure_via_on_error_callback` - unrelated
file, an async-writer error-callback timing test) failed once in the full-suite run and passed
both in isolation and on a full-suite re-run - confirmed intermittent, not a regression, per
the pattern documented in prior entries. `tests/integration/test_experiment_control_pump_dispatch.py`
(a real `ExperimentControlWindow`, unchanged, exercising `_plan_step_commands` through the new
shim) still passes unchanged - direct proof the wrapper preserves behavior. sLSPR acq's own
suite: same 14/22 with the same 8 pre-existing unrelated failures. `pyflakes` clean on every
touched/new file. Launched sLSPR acq headless in simulation mode - no errors/tracebacks in the
log.

**Not done**: the state-machine half of Tier 2 (run/hold/pause/stop, the auto-advance timer
loop) - the harder, riskier half, still to come, with the 53-test characterization suite as
its safety net.

## 2026-08-09: Tier 2, step 2 - run/hold/pause/stop state machine shared as a mixin

Finished Tier 2. The design sketched earlier (a separate `PlanRunController` object owning
the runtime state, with the window's `_plan_running`/`_plan_holding`/etc. becoming properties
delegating to it) turned out to be the wrong shape once actually worked through: those flags
are read directly at 250+ sites elsewhere in the 6,165-line file (mostly editing-lock guard
conditions unrelated to the state machine itself), and every existing test in this area -
the pre-existing `test_experiment_control_step_navigation.py` and this session's own 53-test
characterization suite - constructs `ExperimentControlWindow.__new__(...)` and sets state
directly as plain attributes. Composition would have meant either touching all 250+ read
sites, or giving the properties a dual-mode fallback for when `__init__` never ran (a
backwards-compatibility shim CLAUDE.md explicitly warns against) - and either way, every one
of those existing tests would have needed rewriting just to keep testing the same behavior.

**Switched to a mixin instead**, before writing any of it, once this was clear: moved all 30
state-machine methods verbatim into `lspr_acq_shell.experiment_control_run_loop.PlanRunLoopMixin`
(a plain Python class, not a `QObject` - avoids any PyQt metaclass complication when combined
with `QWidget`), and made `ExperimentControlWindow(PlanRunLoopMixin, QWidget)` inherit them.
`_plan_running`, `_plan_active_row`, and every other piece of runtime state stay exactly what
they always were - plain instance attributes on the window itself, still initialized in
`__init__` exactly as before (zero changes there). Python resolves `ExperimentControlWindow._enter_hold_state`
through the MRO whether the method lives directly on the class or on this mixin, so every
external read site and every existing test needed zero changes - this was verified directly,
not assumed (see below). The mixin's docstring documents the ~15-method "host" contract a
concrete window class must provide (`_read_experiment_control_steps`,
`_apply_step_to_pump_async`, `_sync_experiment_control_timeline`, etc.) as prose, not a formal
`Protocol` class - this project doesn't use one for GUI wiring elsewhere, and the contract is
small enough that documentation is clearer than machinery for it. This is genuinely what
"share the state machine" means now for LSPRi acq: its own experiment-control window inherits
the same mixin and provides the same host methods (which it needs to build anyway), rather
than reimplementing run/hold/pause/stop from scratch.

**Moved** (all 30 methods, byte-identical bodies - traced and read fresh from the file
post-step-1's line-number shift before copying, not from memory): the runtime clock/flag
primitives (`_set_plan_runtime_flags`, `_capture_plan_elapsed_from_clock`,
`_reset_plan_runtime_counters`, `_ensure_measurement_started`, `_experiment_runtime_snapshot`,
`_timeline_progress_for_display`, `_plan_runtime_for_display`, `_step_runtime_for_display`,
`_apply_pause_state`), the step-transition internals (`_resume_experiment_plan`,
`_begin_experiment_plan_run`, `_begin_paused_experiment_plan_run`,
`_resume_experiment_control_after_manual_step_change`,
`_queue_experiment_control_start_after_recording`,
`_run_pending_experiment_control_start_after_recording`, `_enter_hold_state`,
`_enter_pause_state`, `_stop_experiment_plan`), manual row navigation
(`_set_experiment_control_runtime_row`, `_jump_to_experiment_control_step`,
`_apply_selected_experiment_control_step`, `_move_to_relative_experiment_control_step` - found
while tracing direct-assignment sites for the state attributes, not originally scoped as part
of Tier 2 until traced, same pattern as Tier 0's `pump_plan.py` discovery), and the core loop
itself (`_run_experiment_control`, `_start_or_resume_experiment_control`,
`_hold_experiment_control`, `_pause_experiment_control`, `_stop_experiment_control`,
`_schedule_plan_timer`, `_advance_experiment_control_progress`,
`_activate_experiment_control_step_for_elapsed`). Left on the window (genuinely window-
specific "host" methods, not state-machine logic): `_sync_experiment_control_timeline`,
`_ensure_experiment_control_plan_row_visible`, `_set_experiment_control_runtime_row_property`,
`_apply_step_to_pump_async`, `_stop_all_channels`, `_pause_row_step`, and the widget/recording-
controller readouts.

**Verified, and this time genuinely proves the move preserved behavior rather than just "the
tests still pass"**: ran the pre-existing `test_experiment_control_step_navigation.py` (10
tests) completely unmodified against the moved code - all 10 passed with zero changes, direct
evidence the mixin's method resolution is transparent. This session's own 53-test
characterization suite needed exactly one change: the `monotonic()` mock-patch target moved
from `lspr_app.gui.experiment_control_window` to `lspr_acq_shell.experiment_control_run_loop`,
since that's genuinely where the function is called from now (not a workaround - the real
owner of that call moved, so the real owner of the patch target moved with it). Full umbrella
suite 963/963 (the one flaky `test_async_writer_reports_failure_via_on_error_callback` failure
this run, unrelated file, passed on immediate re-run - same intermittent pattern documented
in every recent entry). sLSPR acq's own suite: same 14/22 with the same 8 pre-existing
unrelated failures. `pyflakes` clean on every touched file (also removed now-unused `monotonic`
import and the `experiment_runtime_snapshot`/`ExperimentRuntimeSnapshot` import from the
window, both fully moved to the mixin). Launched sLSPR acq headless in simulation mode - no
errors/tracebacks. Screenshotted the running window - renders correctly, "Hardware
initialization complete." status, no crash.

**Not done**: `LspriAcqExperimentControlBackend` itself - LSPRi acq's own experiment-control
window (which will inherit `PlanRunLoopMixin` and implement its host-method contract) hasn't
been built yet; that's the next real step toward an actual working control panel in LSPRi
acq. Tier 3 (dialogs/cell-editing, a rewrite candidate) also not started.

## 2026-08-09: `LspriAcqExperimentControlBackend` built - LSPRi acq gets a real, working experiment-control panel

Asked the maintainer how to scope this: build a working core panel now using everything
shared so far (table/timeline reuse, run/hold/pause/stop, step-command decision - full
functionality minus the polish dialogs), do the polish-dialog extraction (Tier 3) first for
literal "all functions" parity, or pause. Maintainer chose to build the core panel now.

**Two real findings before writing any GUI code, same discipline as every prior tier**:

1. PUMP/SWITCH/SELECTOR device connectivity needed **no new wiring at all**, contrary to
   `apps/LSPRi/acq/src/lspri_acq_app/device/registry.py`'s own docstring ("PUMP/SWITCH/
   SELECTOR reuse is deliberately NOT wired here yet"). Traced it: `lspr_acq_shell.device_lifecycle`
   registers all three families unconditionally at *module import time*
   (`register_device_family(PUMP, ...)` etc. at the bottom of that file), not behind an app-
   specific opt-in the way CAMERA/ILLUMINATION were (those needed the new
   `register_driver_connect_factory()` mechanism built earlier this session). Any app that
   imports `lspr_acq_shell` at all already has PUMP/SWITCH/SELECTOR discoverable and
   connectable - that docstring predates the maintainer's later confirmation that this app
   drives the same fluidics hardware, and was accurate caution at the time it was written, not
   a real remaining blocker now.
2. sLSPR acq's plan-table *cell-editing* layer (`gui/flow_plan_model.py`, 1,123 lines -
   `ExperimentPlanTableModel` plus 8 delegate classes for valve/switch/color/duration cells)
   was never in scope for Tiers 0-2 (those covered the timeline/table *view* and the
   run/hold/pause/stop *logic*, not the model feeding the view) and turned out to be just as
   window-entangled as Tier 2's state machine was - every delegate holds a `self._window`
   reference and calls back into it (`_theme_palette()`, `_populate_color_combo()`,
   `_duration_display_decimals()`, etc.) for theme-aware popup rendering. Given the
   maintainer's "build fast" choice, this became the basis for the plan below rather than
   another characterize-first extraction.

**Built, not extracted, per the maintainer's choice**: `apps/LSPRi/acq/src/lspri_acq_app/gui/plan_table_model.py`
- a new, deliberately lean `QAbstractTableModel` over `list[PumpPlanStep]` (14 columns: step
number, duration, valve, switch, 4 channel flows, 4 channel directions, color, comment), plain
Qt text editing, no custom dropdown-picker delegates. Pairs directly with the already-shared
`lspr_acq_shell.experiment_control_widgets.ExperimentControlTableView`/`PlanColorDelegate`
(Tier 1) - those only ever needed the standard `QAbstractTableModel` API plus a few view-level
methods `ExperimentControlTableView` already provides, confirmed by inspection rather than
assumed.

**`apps/LSPRi/acq/src/lspri_acq_app/gui/experiment_control_window.py`** - the actual
`LspriAcqExperimentControlBackend`: `ExperimentControlWindow(PlanRunLoopMixin, QWidget)`,
implementing the mixin's full host contract. Real, working pieces: `_apply_step_to_pump_async`
(built from the shared `plan_step_commands`/`StepCommandContext`, `_StepApplyRunnable`, and
the newly-shared `device_io_pool()` - see below), `_service_device_connected`/
`_service_connection_detail` (via `DeviceCommunicationService.shared()`, the same process-wide
singleton CAMERA/ILLUMINATION already use), `_stop_all_channels`, toolbar-driven add/duplicate/
delete/reorder (the last via `ExperimentControlTableView`'s existing `step_move_requested`
signal, already shared since Tier 1). Documented, deliberate simplifications (all noted in the
new file's own module docstring, not silently dropped): a fixed default tube diameter per
channel (`DEFAULT_TUBE_MM` for all 4, no manual spinbox row yet), a fixed pause-row template
(not user-configurable yet), and no session-recording/HDF5 integration
(`_request_recording_control` always succeeds, `_emit_experimental_control_state` only logs) -
that's the separate, not-yet-built sweep-pipeline milestone. Running the plan today drives
real pump/valve/selector hardware; it does not yet write a session file.

**`device_io_pool()` moved to `lspr_acq_shell.device_io_pool`** (found needed while wiring
`_apply_step_to_pump_async`): a 5-line process-global `QThreadPool(maxThreadCount=1)`
singleton accessor, zero window coupling, previously living only in sLSPR acq's
`gui/device_lifecycle_task.py`. sLSPR acq's own file now re-exports it (not called internally
there, so pyflakes needed the same `_ = device_io_pool` re-export marker `pump_plan.py`
already established as this project's convention for that situation).

**Embedded** in `MainWindow` (`main_window.py`) next to the existing ROI panel via a
`QSplitter`, replacing the "later milestone" placeholder comment that file already had.

**Verified**: 13 new tests in `apps/LSPRi/acq/tests/test_experiment_control_window.py` -
construction, add/duplicate/delete-while-running-is-ignored, and a full Run/Hold/Pause/Stop
integration suite that drives the *real* inherited state machine against the *real* (no
hardware attached) device service, including waiting for the real async dispatch onto
`device_io_pool()` to complete (`waitForDone()` + `processEvents()`), not a mock of
`_apply_step_to_pump_async` - confirms Run doesn't crash or hang with nothing connected, and
that the status line correctly reports "not connected" rather than silently succeeding.
LSPRi acq's full own suite: 127/127 (114 baseline + 13 new). Full umbrella suite: 962-963/963
across three runs - one pre-existing, unrelated, order-dependent flaky test failed per run
(a different one each time: `test_async_writer_reports_failure_via_on_error_callback`, then
`test_live_acquisition_worker_relays_child_logs`), each confirmed to pass cleanly in
isolation - not a regression. `pyflakes` clean on every new/touched file. Launched LSPRi acq
(headless, no errors) and screenshotted the running window - the experiment-control panel
renders correctly next to the ROI panel: toolbar, a default Step 1 row, and the timeline
showing "Step 1" with Run enabled and Hold/Pause/Stop correctly disabled while idle.

**Incident, not a code issue**: mid-session, discovered two of this session's own earlier
`pytest tests/ -q` invocations, backgrounded after exceeding the tool's timeout, had never
actually been terminated - they (plus their orphaned `multiprocessing` worker children) were
still running concurrently with later test runs, causing inflated run times (110-145s instead
of the ~55s baseline) and contributing to the flaky-test noise above. Found via `Get-CimInstance
Win32_Process` (command-line inspection, not just process names) and terminated. Worth
watching for in any session using backgrounded long-running test commands.

**Not done**: manual tube-diameter-per-channel control, an editable pause-row template,
session-recording/HDF5 integration and the sweep-pipeline sync between the pump plan and
camera/illumination acquisition (main_window.py currently runs the ROI panel and the
experiment-control panel as two independent panels), Tier 3 (sLSPR acq's plan-table
cell-editing delegates - not shared or ported), the image-processing panel (crop/rotate/
background-flatten, explicitly deferred earlier), Lori LED reference driver.

## 2026-08-09: LSPRi acq's experiment-control panel gets tube-diameter control and an editable pause template

Maintainer asked why tube-diameter control and the pause template were listed as "not
implemented," specifically whether there was a real technical problem, and asked for both to
be built the same as sLSPR acq's, one at a time.

**Tube diameter**: no real problem - `TubeDiameterComboBox` (the exact widget sLSPR acq uses,
restricted to the pump's 26 real supported tube sizes) was already shared into
`lspr_acq_shell.experiment_control_widgets` back in Tier 1; it just hadn't been added to
LSPRi acq's window when the MVP was built. Added `self.tube_diameter_spins` - one
`TubeDiameterComboBox` per channel, shown in a labeled row above the plan table - and wired
`_apply_step_to_pump_async`'s `tube_mm_by_channel` to read live values from them instead of a
fixed `[DEFAULT_TUBE_MM] * ACTIVE_PUMP_CHANNELS`. One deliberate simplification kept: no
"uniform" toggle that drives all four channels from a single control (sLSPR acq's
`manual_uniform_button`) - always independent per-channel here, since that toggle is UI
convenience, not core functionality.

**Pause template**: genuinely different from tube diameter - sLSPR acq edits it via
`ExperimentControlDialogs.edit_pause_state`, a dedicated, fully themed `QDialog` with its own
styled table (`experiment_control_dialogs.py`), part of the Tier-3 dialog layer that was
deliberately not shared (traced earlier as comparably window-entangled to Tier 2's state
machine). Rather than port that dialog, reused what already exists: the pause template is
just another `PumpPlanStep`, and the window already has a fully working, editable
`PlanTableModel`/`ExperimentControlTableView`/`PlanColorDelegate` combination for the main
plan table - so `self.pause_template_table`/`self._pause_template_model` is a second, tiny
one-row instance of exactly the same machinery, not a new dialog. `_pause_row_step()` now
returns `deepcopy(self._pause_template_model.steps()[0])` instead of a fixed all-stop
constant. `duration_s` is stored but unused (the pause step applies once via
`_apply_step_to_pump_async`, never runs through the timer) - noted in the module docstring so
it isn't mistaken for a bug later.

**Verified**: 7 new tests (20 total in `test_experiment_control_window.py`, up from 13) -
tube-diameter widget count/defaults, and (via a spy that wraps the real `plan_step_commands`
to capture what it was called with, not a mock of it) that a changed tube-diameter value and
an edited pause template both actually reach the dispatched hardware command, not just the
widgets themselves; also that editing the pause template doesn't leak into the main plan
table, and that `_pause_row_step()` returns a real deepcopy (mutating the returned step
doesn't affect the template). One test-authoring mistake caught by running it for real: an
isolation test compared against `"Open"`, which is also the main table's own default valve
value, giving a false pass regardless of real isolation - fixed by asserting against a value
that starts different on each table (the comment field, which starts empty on the main step).
LSPRi acq's own full suite: 134/134 (127 baseline + 7 new). Full umbrella suite: 963/963,
clean, no flakes this run. `pyflakes` clean. Screenshotted the running window - both new rows
render and are visibly populated (tube diameter combos default to 0.25mm, pause-state table
shows its own header row beneath the main plan table).

**Not done**: session-recording/HDF5 integration, the sweep-pipeline sync between the pump
plan and camera/illumination acquisition, Tier 3 itself (the dialog/delegate layer - still
not shared, though its two most-needed pieces for this app now have lean equivalents), the
image-processing panel, Lori LED reference driver.

## 2026-08-09: Visual-parity effort started - theme and real icon toolbar

Maintainer asked why LSPRi acq's panel didn't look like sLSPR acq's, and asked for a real
match - "rewrite the whole part in sLSPR acq and then port it or copy it, I dont care, but I
want it to look the same and behave the same." Investigated the actual scope before writing
anything (same discipline as every prior tier): the un-shared visual/behavioral layer totals
roughly 4,000+ lines (`experiment_control_dialogs.py` 1,605, `flow_plan_model.py` 1,123,
`experiment_control_editing.py` 739, plus ~650 more across smaller satellite files, plus the
genuinely visual-layout slice of the 5,552-line window itself) - comparable in size to
everything landed this session combined. Presented this honestly rather than silently
narrowing scope or silently taking on an unverifiable multi-thousand-line diff; maintainer
chose to split it into a dedicated, staged, multi-session effort, then chose (when the manual
editor row turned out to partly depend on the not-yet-built dialog layer) to push through
building it anyway with the dialog-dependent buttons present but inert for now.

**This session's slice**: theme (`_theme_palette`/`_apply_style`, ported verbatim from sLSPR
acq - a static hex-color dict and a ~260-line QSS stylesheet, not a theme *engine*, so far
less risky than the name suggested) and the real icon toolbar - `add_step_button`/
`apply_step_button` (edit-mode toggle)/`duplicate_step_button`/`remove_step_button`/
`import_plan_button`/`export_plan_button`, plus the run-control row
(`run_button`/`hold_button`/`pause_button`/`stop_button`/`previous_step_button`/
`next_step_button`) - using the exact same icons, tint colors, tooltips, and Qt object names
sLSPR acq's does. `previous_step_button`/`next_step_button` are real, not stubs -
`_move_to_relative_experiment_control_step` was already ported in Tier 2, just never wired to
a button in this app until now. Import/export buttons are present with matching icons but are
NOT wired to real file I/O yet (clearly stated in their tooltips and in a status message if
clicked) - that needs `ExperimentPlanImportTask`/`ExperimentPlanExportTask` (async QRunnable
file I/O, file dialogs), out of scope for this slice.

**New shared module**: `lspr_acq_shell.experiment_control_builders` -
`create_flow_step_action_button` (pure) plus `create_direction_button`/`set_direction_button`/
`set_step_valve_button_state_for_button` (duck-typed on a `window` with `_theme_palette()`/
`_valve_state_label()`, same pattern as `PlanRunLoopMixin`'s host contract) - moved from sLSPR
acq's `gui/experiment_control_builders.py` verbatim; sLSPR acq keeps a re-export shim.

**A real diagnostic detour**: after landing the code, screenshots kept showing the *old*
plain-text buttons even though the source file was confirmed correct
(`python -c "import lspri_acq_app...; print(m.__file__)"` pointed at the right path). Traced
it to a stale/ghost window from an earlier launch still being matched by the screenshot
script's title-regex search - killing every LSPRi-acq-related python process first, then
relaunching, produced the correct screenshot. Separately, the full umbrella suite hung twice
(11+ minutes, then ~5 minutes) before finishing - isolated with `--deselect` bisection to
`tests/unit/test_live_processing_worker.py`, a real subprocess-spawning multiprocessing test
unrelated to anything touched this session; with it excluded, the suite ran cleanly in 26s
(unit only) and 165s (full). Recorded here as a known, pre-existing environmental flake, not
something to silently paper over or claim was fixed.

**Verified**: LSPRi acq's own suite 134/134 (unchanged count - this was a visual/wiring
change, no new tests added yet for the toolbar itself beyond what already exercises
`_move_to_relative_experiment_control_step` indirectly). Full umbrella suite 957/957 with
`test_live_processing_worker.py` excluded (963 baseline minus its 6 tests) - zero regressions
from this change. `pyflakes` clean on every touched/new file. Screenshot-confirmed the icon
toolbar now visually matches sLSPR acq's real toolbar (same icons, same colors, same order).

**Not done**: the manual single-step editor row (Duration/CHs/Dir/Tube/Flow/CH1-4/Valve/
Color/Comment, including the uniform/per-channel toggle and switch-solution combo), the real
`flow_plan_model.ExperimentPlanTableModel` + delegates (still using the lean
`PlanTableModel`), the Tier-3 dialog layer, real import/export file I/O. Next session's slice,
per the staged plan.

## 2026-08-09: Manual single-step editor row - second slice of visual-parity effort

Continued the staged visual-parity effort with the manual single-step editor row (Duration/
Valve/Color/Switch/Comment plus per-channel Flow/Direction) - the fields that actually
compose a `PumpPlanStep`. Traced sLSPR acq's real field mapping first (`_current_editor_step`/
`_add_experiment_control_step_from_editor`), not guessed: confirmed tube diameter is
correctly *not* part of this row's data model at all (`PumpPlanStep`/`PumpChannelStep` have
no `tube_mm` field) - it only looks like part of the same row in sLSPR acq for layout
compactness, so LSPRi acq's existing separate `tube_diameter_spins` row was already
architecturally correct and needed no change.

**New shared constant**: `lspr_acq_shell.pump_plan.PLAN_COLOR_OPTIONS` - the default 8-color
step palette, moved from sLSPR acq's `ExperimentControlWindow.PLAN_COLOR_OPTIONS` (a pure
class-level list, zero coupling). sLSPR acq's own attribute is now
`list(_SHARED_PLAN_COLOR_OPTIONS)` - kept as a list (not the shared tuple) since call sites
expect list semantics.

**Built**: `step_duration_spin`, per-channel `manual_flow_spins`/`manual_direction_buttons`
(using the shared `create_direction_button`/`set_direction_button`, with click-to-toggle
wiring added per button - `create_direction_button` itself only sets initial state, sLSPR
acq wires each call site's `.clicked` separately, easy to miss and initially was: caught by
a real test failure, `test_direction_button_toggles_between_cw_and_ccw`, not by inspection),
`step_valve_button` (using shared `set_step_valve_button_state_for_button`), `step_color_combo`
(populated from `PLAN_COLOR_OPTIONS`), `step_switch_spin`, `step_comment_edit`, plus four
inert settings-gear buttons (valve/color/switch/comment) with icons matching sLSPR acq's but
tooltips stating "Not yet wired in this app" - they need the not-yet-built dialog layer.
`_current_editor_step()`/`_add_experiment_control_step_from_editor()` port sLSPR acq's exact
logic (compose a `PumpPlanStep` from the row's live values, insert after the selected row);
`add_step_button` (the toolbar icon) now calls this instead of inserting a bare default step
- sLSPR acq only has one "add" mechanism, not two, so no separate button was added for this.

**`PlanTableModel` gained `insert_step(row, step)`** (a real step, not just `insert_step_after`'s
hardcoded default) - `insert_step_after` is now a thin wrapper calling it with a default step,
avoiding the awkward "insert a placeholder then overwrite it" approach an earlier draft used.

**Verified**: 7 new tests in `ManualEditorRowTests` (27 total in `test_experiment_control_window.py`,
up from 20) - editor values reach the composed step (duration/comment/switch/flow/valve),
the selected color's hex value is used, direction-button toggling works (caught the missing
click-wiring bug above), valve toggling, and the color combo is populated from the shared
palette. Plus one more in `StepEditingTests` confirming insert position (after the selected
row, not always at the end). LSPRi acq's own full suite: 141/141. Full umbrella suite: 957/957
(same known-flaky file excluded), this run took 70s - back to the healthy baseline now that
no stray background processes were left running. sLSPR acq's own suite: same 14/22 baseline,
no new failures. `pyflakes` clean. Screenshot-confirmed the row renders correctly and closely
matches sLSPR acq's real layout (Duration/Valve+gear/Color+gear/Switch+gear/Comment+gear
header row, CH1-4 flow/direction columns).

**Not done**: the real `flow_plan_model.ExperimentPlanTableModel` + its 8 delegates (still
the lean `PlanTableModel`), the Tier-3 dialog layer (what the four gear buttons will
eventually open), real import/export file I/O, the time-unit toggle, the "CHs" uniform/
per-channel direction toggle, the switch-solution combo.

## 2026-08-09: Valve-label and color-palette dialogs - third slice of visual-parity effort

Continued the staged effort by wiring two of the four inert settings-gear buttons from the
manual editor row: `step_valve_settings_button` and `color_palette_button`. Chose these two
first because they're the most self-contained of the four (the switch-solution editor needs
the solution-mode-toggle machinery already deferred; the pump-display dialog is tied to a
device-display feature not wired in this app at all).

**Checked scope before building**: sLSPR acq's `edit_valve_labels` (~185 lines) and
`edit_color_palette_entries` (~300 lines) in `experiment_control_dialogs.py` are both fully
custom `QDialog`s - frameless windows with gradient/colored borders, their own themed table
widgets, custom title bars. Given the pattern already established for the plan table itself
(lean `PlanTableModel` instead of porting `ExperimentPlanTableModel`'s window-coupled
delegates), built lean equivalents instead of porting the custom chrome: standard `QDialog`s
with the same editable data (a `QFormLayout` of line-edit + color-picker-button rows for
valve labels; a `QTableWidget` with add/remove-row buttons for the palette) using
`QColorDialog.getColor()` for color picking rather than a custom swatch widget.

**Real state, not stubs**: `self._valve_state_labels`/`self._valve_state_colors` (dicts,
defaulting to the same values sLSPR acq starts with) and `self._color_palette_entries`
(list, defaulting to the shared `PLAN_COLOR_OPTIONS`) are genuine instance state now -
`_valve_state_label()` and `_populate_color_combo()`/`_default_experiment_control_color()`
read from them instead of the fixed defaults they read before. Neither persists across app
restarts yet - this app has no settings-persistence story at all yet, a separate,
not-yet-scoped piece of work (sLSPR acq saves both to its UI-state JSON file).

**Verified**: 8 new tests (35 total in `test_experiment_control_window.py`, up from 27) -
driven by patching `QDialog.exec` to inspect/mutate the dialog's real, already-constructed
child widgets (`findChildren`) before returning Accepted/Rejected, simulating a user
editing fields then clicking OK/Cancel, rather than mocking the dialog methods themselves.
Covers: accept applies edits (including that the valve button's displayed text updates
immediately), cancel leaves state untouched, a blank valve label falls back to the raw
state name, removing a palette row shrinks the combo, renaming a palette entry is reflected
in the combo. LSPRi acq's own full suite: 149/149. Full umbrella suite: 956/956 (957
baseline minus one flaky, unrelated test - `test_async_writer_reports_failure_via_on_error_callback`,
a *different* known pre-existing flake than the multiprocessing one, confirmed passing in
isolation, same pattern documented in earlier entries). sLSPR acq's own suite: same 14/22
baseline. `pyflakes` clean. Screenshot-confirmed no construction-time regression.

**Not done**: the switch-solution and pump-display dialogs (their gear buttons are still
inert), settings persistence for the two new pieces of state, the real
`flow_plan_model.ExperimentPlanTableModel` + delegates, real import/export file I/O.

## 2026-08-09: Switch-solution dialog, pump-display dialog, and real settings persistence

Maintainer asked to keep going through the remaining gaps one by one, and to keep matching
sLSPR acq's real behavior, not an approximation of it. Closed out the last two inert gear
buttons and, separately, gave the whole app real settings persistence for the first time.

**Switch-solution dialog - traced sLSPR acq's *actual current* behavior, not its field
names**: `step_switch_spin` (a raw 1-12 spinbox) was removed entirely from this app, replaced
with `step_switch_combo` ("N: solution name"). Found by reading `_set_switch_solution_mode`
directly: it unconditionally does `self._switch_solution_mode = False`,
`step_switch_mode_button.setVisible(False)`, `step_switch_spin.setVisible(False)`,
`step_switch_combo.setVisible(True)` regardless of its own `enabled` argument or the stored
`_switch_solution_mode` setting - the raw-spin/mode-toggle code path is dead in sLSPR acq
right now, so building it here would have "matched" a name that no longer reflects real
behavior. `_edit_switch_solution_labels` is a lean 12-row `QTableWidget` dialog (Solution
column only - sLSPR acq's own dialog also has Concentration/Unit/Notes columns, left out
since nothing in this app reads them).

**Pump-display dialog - the first of the four dialogs wired to a setting with a real
hardware effect**: `StepCommandContext.pump_display_enabled` was hardcoded `False` since
Tier 2; `_edit_pump_display_settings` (a checkbox + live 16-character preview, using the
already-shared `PUMP_DISPLAY_MAX_LENGTH`) now actually controls whether a step's comment
gets sent to the pump's own display when that step is applied to real hardware.

**Real settings persistence, not another simplification**: found `lspr_acq_shell.settings_store`
already has a complete, generic JSON settings engine (atomic writes, corruption quarantine,
an `app`/`ui_state` convention) - its own module docstring gives the *exact* usage pattern
for a second app (`save_app_setting(key, value, path=user_profile.current_config_path("lspri_acq_settings.json"))`),
so this was a direct use of existing shared infrastructure, not new plumbing. One blob under
an `"experiment_control"` key: valve labels/colors, the color palette, switch-solution
labels, the pump-display setting, and tube diameters (all loaded once in `__init__`, saved
again after each dialog's Accept and after any tube-diameter change). The plan itself is
deliberately not included - project/session state, not a UI setting, matching the
distinction CLAUDE.md documents for `lspr_settings.json`.

**Real test-isolation bug caught before it shipped**: the first draft had every test in the
file constructing `ExperimentControlWindow()` directly, meaning every dialog-accept path's
new `_save_experiment_control_settings()` call would have written to the *real* per-user
`lspri_acq_settings.json` and cross-polluted every other test's fresh window in the same
run. Fixed by adding a `_make_window(testcase, ...)` factory that patches `_settings_path`
to an isolated `tempfile.TemporaryDirectory()` per test, then swept it across all 10
existing window-construction sites in the test file, not just the new tests.

**Verified**: 50 tests total (up from 44) - 6 for the switch-solution dialog, 4 for pump
display (including that enabling it really does flow into `StepCommandContext`), and a
dedicated `SettingsPersistenceTests` class with 6 tests proving actual cross-instance
survival (construct a window, change a setting, construct a *second* window sharing the
same settings path, assert the second one loaded what the first saved) - not just "the save
call doesn't raise." LSPRi acq's own full suite: 164/164. Full umbrella suite: 956/956 (one
different pre-existing flaky test this run - `test_async_writer_reports_failure_via_on_error_callback`
- confirmed passing in isolation). sLSPR acq's own suite: unchanged, same 14/22 baseline
(untouched this round). `pyflakes` clean.

**Screenshot verification could not be completed this round** - two consecutive automated
screenshot attempts captured unrelated video content instead of the app window (the desktop
had PotPlayer windows open on a secondary monitor; `Desktop(backend="uia").windows(title_re=...)`
matched something unexpected rather than the real LSPRi acq window, confirmed via a direct
window enumeration that found no window with "LSPRi" in its title at either attempt). Per
this project's own GUI-testing scope boundary (only touch the target app via automation,
nothing else on the desktop), stopped after the second wrong capture rather than keep
retrying. Correctness was instead confirmed via a headless launch (ran cleanly for 10s, no
exception) plus the test suite above, which is now strong enough (real cross-instance
persistence tests, real dispatch-context assertions) to stand on its own without a visual
check for this particular change.

**Not done**: the real `flow_plan_model.ExperimentPlanTableModel` + its 8 delegates (still
the lean `PlanTableModel`), real import/export file I/O.

## 2026-08-09: Real import/export file I/O

Wired the last two placeholder buttons - `import_plan_button`/`export_plan_button` - to real
file I/O, using `lspr_acq_shell.experiment_control_import`/`_export` directly. Both were
already fully shared since Tier 0 with effectively zero window coupling, so this was
building the window-side glue (file dialogs, payload construction, signal handling), not new
shared infrastructure.

**Scoped to native YAML export + universal import**: sLSPR acq's export path also supports
two legacy compat CSV/TXT formats (a 25-column layout for external-tool interop, including
one with a preserved typo in a header - "Descritption") - deliberately not built here, since
native YAML is this app's own primary format too and the compat formats exist for interop
this app doesn't need yet. `_build_native_experiment_plan_document` matches sLSPR acq's own
document schema field-for-field, so a plan exported from either app opens correctly in the
other. Import accepts native YAML, CSV/TSV, *and* HDF5 - unlike export, this needed no extra
scoping decision: `ExperimentPlanImportTask` already dispatches by file suffix internally, so
supporting HDF5 import cost nothing beyond what YAML/CSV already required.

**Deliberately simplified on the import side**: imported colors and tube diameters are
merged into this app's (now-persisted) state; imported valve-label/switch-solution overrides
are not, since sLSPR acq's own import merge path is meant for pairing with its HDF5
measurement-file import flow, which has no equivalent in this app yet (no recording/HDF5
export here at all).

**Verified**: 8 new tests (58 total, up from 50) - including a genuine round-trip test
(export a plan with distinct values, construct a *second* window, import the exported file
into it, assert the values match) using the real `QThreadPool.globalInstance()` dispatch,
not a mock of the import/export tasks. Also covers: no-steps export is a no-op (no file
written), a nonexistent import path reports status without raising, cancelling either
dialog does nothing, and that importing a plan with new colors/tube diameters actually
updates this window's palette and tube-diameter controls. LSPRi acq's own full suite:
172/172. Full umbrella suite: 956/956 (the same `test_async_writer_reports_failure_via_on_error_callback`
flake as earlier today, already confirmed unrelated and order-dependent). sLSPR acq's own
suite: unchanged, same 14/22 baseline. `pyflakes` clean. Screenshot verification skipped
again this round for the same reason as the previous entry (window-capture tooling issue,
not an app problem) - the round-trip file-I/O tests are direct, strong evidence on their own.

**This closes every item from the "do all one by one" list except the biggest one**: the
real `flow_plan_model.ExperimentPlanTableModel` + its 8 delegates, replacing the lean
`PlanTableModel` still in place. That one was flagged from the start as comparable in size
to Tier 2's entire state machine and is the next, and last, piece of this visual-parity
effort.

## 2026-08-09: Real per-cell table delegates - last item of the visual-parity effort

Traced sLSPR acq's `flow_plan_model.py` before deciding how to close this out, same
discipline as every prior slice - and the finding reshaped the plan. The *model* class
(`ExperimentPlanTableModel`) takes no `window` reference at all; it's configured entirely
via plain setters (`set_theme_palette`/`set_valve_state_colors`/etc.), so it's genuinely
portable on its own. The complexity sits entirely in the 8 delegate classes, which each take
`window` and call back into it for theme colors, combo population, and editor-lifecycle
hooks (`installEventFilter`, wheel-scroll suppression, auto-opening popups on cell click,
exact popup-width calculations from font metrics).

**Decided against swapping in the real model**: this app's own `PlanTableModel` already has
58+ tests built around its own column layout (`Step, Duration, Valve, Switch, CH1-4 Flow,
CH1-4 Direction, Color, Comment` - different grouping from sLSPR acq's
`flow/direction/tube-per-channel` blocks). Swapping in the real model would have meant
reworking column indices and delegate wiring across already-working, tested code, for
benefit that's mostly cosmetic (the real model's `data()`/`flags()` logic is more elaborate
but not functionally different for what this app needs). Kept the existing model.

**Built 3 lean, real delegates instead of porting 8**: `ValveDelegate`, `SwitchSolutionDelegate`,
`DirectionDelegate` (new, in `gui/plan_table_model.py`) - real `QComboBox` editors plus a
`displayText()` override (the standard Qt mechanism for "show something different from the
raw stored value without changing what's stored") so cells render the window's custom valve
labels / switch-solution names / direction glyphs, while the model keeps storing the plain
"Open"/"Close", integer position, and "CW"/"CCW" values it always did. Built from pieces
already in this window from earlier slices today - `_valve_state_label`, `_switch_display_text`,
the shared `direction_glyph` - not new machinery. No custom popup-width calculation,
wheel-scroll suppression, or auto-opening popups (sLSPR acq's `_BaseFlowDelegate` has all
three); a plain combo editor is the simplification here. `PlanColorDelegate` (already shared
since Tier 1) was already wired to the color column - only the wiring call site changed
(a new `_install_plan_table_delegates` helper, applied to both `plan_table` and
`pause_template_table` since they share the same model/column layout).

**Small cleanup alongside**: renamed the model's column-index constants from `_COLUMN_*`
(module-private) to `COLUMN_*` (public), since the window now needs to import them for
delegate placement - previously the window recomputed `COLOR_COLUMN` by hand
(`4 + 2 * ACTIVE_PUMP_CHANNELS`), duplicating knowledge the model already had.

**Verified**: 12 new tests - 9 in a new `test_plan_table_model.py` (real editor
create/setEditorData/setModelData round-trips for all three delegates, plus a few
`PlanTableModel` basics that had no dedicated test file before) and 3 in
`test_experiment_control_window.py` confirming the *real* window's *real* tables (not a
bare model+delegate pair) actually got these delegates installed, including that editing a
valve label through the real dialog changes what the real installed delegate's
`displayText()` returns. LSPRi acq's own full suite: 184/184. Full umbrella suite: 956/956
(yet another different pre-existing flaky test this run -
`test_real_debounce_coalesces_a_burst_into_one_entry`, a timing-sensitive debounce test in
sLSPR acq unrelated to anything touched today, confirmed passing cleanly in isolation - the
fourth distinct flaky-test identity observed today, reinforcing that these are genuinely
environmental/order-dependent, not one specific broken test). sLSPR acq's own suite:
unchanged, same 14/22 baseline. `pyflakes` clean. Screenshot verification attempted once
more this round and hit the same window-capture tooling problem as the previous two
entries (confirmed reproducible, not a one-off) - stopped retrying per this project's GUI-
testing scope boundary and relied on a headless launch (ran cleanly, no exception) plus the
delegate-level test suite, which is strong, direct evidence (real Qt editor objects, real
round-trips) on its own.

**This closes the entire "do all one by one" punch list from this session**: theme, icon
toolbar, manual editor row, all four settings dialogs, real settings persistence, real
import/export, and now real table delegates. What's left for the experiment-control panel
specifically is genuinely cosmetic at this point (sLSPR acq's exact popup-width/wheel-
scroll/auto-open editor behavior) rather than functional. Bigger, separately-scoped work
still ahead for LSPRi acq as a whole: session-recording/HDF5 integration, the sweep-pipeline
sync between the pump plan and camera/illumination acquisition, the image-processing panel,
and the Lori LED reference driver.

---

## 2026-08-09: Session-recording / HDF5 design discussion — scope and schema decided before any code

Maintainer corrected the earlier screenshot-tooling failures (see the last several entries'
"stopped retrying" notes): the capture tool was pointed at an external monitor, not the
primary one, unrelated to the app itself — screenshot verification going forward should
launch/capture on the primary monitor.

Maintainer then asked to talk through the session-recording workflow before any
implementation, rather than jumping straight to code — matching `CLAUDE.md`'s "check in
first" rule for HDF5 schema changes. Described the intended user workflow in detail: start
app → load HW (now or later) → set up illumination parameters (wavelength list, per-
wavelength spectra — measured or from a default pre-measured file, settle times, maybe LED
current) → set up camera parameters (per-wavelength exposure, gain, binning, saving mode,
resolution, crop/ROI) → place sample + concentric-ring reference ROIs on the image, same
convention as LSPRimaging Evaluation → run. All of illumination/camera/ROI setup should be
recorded in HDF5 (camera *images* only once a measurement is actually started, not during
setup/preview) and should also be saveable/restorable as a session, ideally while staying
compatible with the pre-existing measurement schema (plan/valve/switch/color-palette
tables already exist there and already carry over).

Traced the actual current state before proposing anything (not guessing):

- `apps/sLSPR/acq/src/lspr_app/storage/hdf5_export.py`'s `_write_assignment_tables_metadata`/
  `_write_switch_solution_metadata`/`_write_plan_tables` — confirms the existing
  `metadata/assignment_tables/{switch_solution_map, switch_solution_details,
  valve_state_map, color_palette_entries}` tables are real, working, and follow one
  generic `_upsert_table(group, name, rows, columns)` pattern — a genuine, reusable
  blueprint for new tables.
- `apps/LSPRi/acq/src/lspri_acq_app/domain/roi.py` — `AreaRoi`/`AreaRoiGroup` already
  ported field-for-field from LSPRi eva (sample circle + concentric reference ring,
  scoring fields present but unused at v1). No new ROI design needed.
- `apps/LSPRi/acq/src/lspri_acq_app/domain/models.py`'s `ImagingAcquisitionSettings` and
  `device/camera_base.py`'s `CameraSettings` are both single global values
  (`exposure_us`/`gain`) for the whole sweep — no per-wavelength model exists yet, needed
  for the maintainer's "exposure varies per wavelength for good contrast" requirement.
- `device/illumination_base.py`'s `IlluminationSource` ABC has no concept of a spectrum
  (measured or default-file) or LED current at all yet.
- `acquisition/sweep_pipeline.py`'s `SweepPipeline.start()` unconditionally starts
  `SaveWriterThread` — every cube gets written to disk the instant the sweep runs, no
  live-preview-without-saving vs. armed/recording distinction, unlike sLSPR acq's own
  `recording_active` split (`main_window_runtime.py`/`acquisition_controller.py`). This is
  the actual gap behind "images aren't recorded until measurement is started" not yet
  being true.
- `lspr_io`'s `lspr_session` schema (v1) is a thin stub today — just
  `experiment_plan_steps`/`experiment_plan_total_duration_s` attrs, not a real state dump —
  so it isn't a ready-made "session file" answer on its own.
- The plan doc's own §9/§12 already had an unbuilt, unchecked milestone for exactly this
  ("HDF5 schema extension in `lspr_io` — experimental data only, minor version bump") with
  `/processed/roi_definitions`, `/processed/absorbance_spectra/{roi_id}`,
  `/processed/sensorgram/{roi_id}` sketched but not built — today's design keeps those
  names and adds the illumination/camera-settings and image-cube-manifest pieces that
  weren't in that earlier sketch.

Presented two schema-strategy options to the maintainer: a new schema name reusing the
shared assignment-tables plumbing, vs. extending `lspr_measurement` itself with a minor
version bump (6.3 → 6.4). **Maintainer chose to extend `lspr_measurement`** (one schema
name/reader family for both single-spectrum and imaging acquisitions). Consequence worked
out with the maintainer: since the new groups are additive/ignorable per the existing
compatibility policy, a "session" file and a "completed measurement" file can be the *same*
format — a session save is just a v6.4 file with the new setup groups populated and zero
raw rows; loading either a fresh setup snapshot or a previously recorded measurement uses
the same reader, matching the maintainer's own phrasing ("restoring new session, or loading
of those files"). No separate session schema needed.

**Agreed v6.4 group/dataset layout** (maintainer confirmed, "yes, I think it is ok"):

- `metadata/illumination_settings` — one row per wavelength: `wavelength_nm`,
  `settle_time_ms`, `current` (nullable), `spectrum_source` (`"measured"` /
  `"default_file"`), joined to `metadata/illumination_spectra/{wavelength_nm}` for the
  actual spectrum arrays (measured ones stored raw; default-file ones store a
  reference/hash to the source file, not a duplicate copy).
- `metadata/camera_settings` — one row per wavelength: `exposure_us`, `gain`, `binning`,
  `resolution_width_px`/`resolution_height_px`, `crop_x_px`/`crop_y_px`/`crop_width_px`/
  `crop_height_px`, `saving_mode` — joined to `illumination_settings` by `wavelength_nm`,
  same join convention as the existing `switch_solution_details`/`switch_solution_map`
  pair.
- `processed/roi_definitions` — kept as already sketched in §9; will carry the
  `AreaRoi`/`AreaRoiGroup` fields verbatim.
- `processed/absorbance_spectra/{roi_id}`, `processed/sensorgram/{roi_id}` — kept as
  already sketched in §9, unchanged.
- A new image-cube manifest table (`cube_index`, `timestamp_utc_ms`, `file_path`) so the
  HDF5 file can point at the separate TIFF/OME-Zarr files `image_writer.py` already writes,
  without duplicating pixel data into two places.
- An attr distinguishing a pure setup/session snapshot (no raw rows yet) from a file that
  has actually recorded data, so the reader knows whether to open it read-mostly
  (completed measurement) or editable (in-progress session).

Also agreed, independent of the schema question: `ImagingAcquisitionSettings`/
`CameraSettings` need to become per-wavelength tables in the domain model (not just in
HDF5) for the GUI workflow to be buildable at all, and `SweepPipeline` needs a
`recording_active`-style gate before `SaveWriterThread` starts, mirroring sLSPR acq's
existing pattern.

**Not yet implemented** — this entry records the design discussion and the maintainer's
decisions; the schema.py version bump, the domain model changes, the recording gate, and
the actual `lspri_acq_app` HDF5 writer module are the next, separately-scoped implementation
slices (see §9/§12 in the plan doc, updated alongside this entry).

---

## 2026-08-09: Session-recording implemented — schema bump, per-wavelength settings, recording gate, writer/reader, all tested

Maintainer confirmed the design from the entry above ("yes, I think it is ok, you can
continue") and the schema-strategy choice (extend `lspr_measurement` rather than a new
schema name). Implemented in five slices, each run against the full relevant test suite
before moving to the next - 214 tests passing across `lspr_io` + `lspri_acq_app` by the
end, 0 regressions in either app's or the umbrella's existing suites (957 umbrella tests,
211 `lspri_acq_app` tests, excluding the pre-existing known-hanging
`test_live_processing_worker.py`).

**1. `lspr_io` schema bump to 6.4** (`packages/lspr_io/src/lspr_io/schema.py`) - new
dataset/group/column constants for `metadata/illumination_settings`,
`metadata/illumination_spectra/{wavelength_nm}`, `metadata/camera_settings`,
`metadata/image_cube_manifest`, `processed/roi_definitions`,
`processed/absorbance_spectra/{roi_id}`, `processed/sensorgram/{roi_id}`, and a
`has_recorded_data` metadata attr - same dated-comment-block convention as 6.0-6.3.
`validate_measurement_metadata()` needed zero code changes (it's already generic against
the version constants, confirmed by reading it, not assumed) - only two pre-existing tests
had the old `"6.3"`/`3` literal hardcoded (`tests/integration/test_acq_hdf5.py`,
`tests/integration/test_io.py`), fixed to `"6.4"`/`4`.

**Also promoted to `lspr_io`, not just LSPRi acq's own module**: sLSPR acq's private
`HDF5MeasurementWriter._upsert_table` generalized into a public `lspr_io.upsert_table()`
(`packages/lspr_io/src/lspr_io/hdf5.py`) - LSPRi acq's new writer needed the identical
"small named string table, overwritten in place" pattern for its five new tables, and
re-deriving it would have been silent duplication of working, tested logic. sLSPR acq's own
copy is left untouched (not migrated to call the shared one) - no functional gain to justify
touching a working, heavily-tested file in a different submodule for this. Also promoted the
existing private `_read_string_table_dataset` to public `read_string_table_dataset` (same
file) - the natural read-side counterpart, needed by the new reader (see slice 4 below).

**2. Per-wavelength camera/illumination settings** (`apps/LSPRi/acq/src/lspri_acq_app/domain/models.py`,
`acquisition/sweep_pipeline.py`) - new `WavelengthCameraSettings`/
`WavelengthIlluminationSettings` dataclasses; `ImagingAcquisitionSettings` gained
`camera_settings_by_wavelength`/`illumination_settings_by_wavelength` override dicts,
empty by default so every existing caller's global-exposure behavior is unchanged.
`SweepController._run_one_sweep()` now calls `camera.configure()` once per wavelength
(previously once per sweep, before the wavelength loop even started) via a new
`_camera_settings_for()`/`_settle_time_ms_for()` pair that check the override dict first,
falling back to the existing global fields. Moving the configure() call also improved
resilience as a side effect - a configure failure now goes through the same per-step
error-and-backoff path as a `set_wavelength`/`acquire_frame` failure, instead of
permanently failing the whole controller before its first sweep. 6 new tests
(`test_domain_models.py`, `test_sweep_pipeline.py`), including one asserting the *actual*
`Camera.configure()` calls a real `SimulatedCamera` subclass recorded, not just that the
dataclass fields exist.

**3. Recording gate** (`acquisition/sweep_pipeline.py`) - `SweepController` gained a
`threading.Event`-backed `recording_active` flag (`set_recording_active()`/
`is_recording_active()`, also exposed on `SweepPipeline`), defaulting to **False**. The
gate lives at the single point a completed cube is handed off: `if recording_active:
save_queue.put(cube)` always runs; `_queue_put_latest(processing_queue, cube)` always
runs regardless, so live preview/ROI extraction/display keep working during setup. This is
the actual fix for "camera images aren't recorded until a measurement is started" - before
this, `SweepPipeline.start()` always saved every cube unconditionally, so that requirement
wasn't true yet despite being assumed in earlier planning. Two pre-existing tests
(`test_processing_queue_only_ever_holds_the_latest_cube`,
`SweepPipelineSmokeTest.test_sweep_produces_saved_cubes_and_sensorgram_points`) used
`save_queue` depth as a "how many sweeps completed" proxy and needed `recording_active=True`
added explicitly, now documented inline as intentional ("this test uses save_queue depth as
its N-sweeps-completed proxy" / "golden `recording is on` path"). 4 new dedicated gate
tests.

**4. `apps/LSPRi/acq/src/lspri_acq_app/storage/hdf5_export.py` built** - new file,
`ImagingMeasurementWriter` + `read_imaging_session()`/`ImagingSessionSnapshot`. Module
docstring states directly: **a "session" and a "measurement" are the same file format** - a
session save is just a v6.4 file with the setup groups populated and zero raw rows,
`has_recorded_data` is the only distinguishing attr, matching the maintainer's own framing
during the design discussion. Writer methods: `write_illumination_settings`/
`write_camera_settings` (derive one row per wavelength from `ImagingAcquisitionSettings`,
override dict first then global fallback), `write_illumination_spectrum` (one measured or
default-file spectrum per wavelength), `write_roi_definitions` (`AreaRoi`/`AreaRoiGroup` ->
`processed/roi_definitions`, group membership joined in via `group_id`),
`write_valve_state_labels`/`write_color_palette_entries`/`write_switch_solution_labels`
(same `assignment_tables` shape sLSPR acq already writes, for the maintainer's requested
plan/valve/switch/color-palette compatibility), `append_image_cube_manifest_row`
(re-upserts a growing in-memory list - acceptable at expected imaging-experiment cube
counts, not a specialized append-only string table), `append_sensorgram_point`/
`append_absorbance_spectrum` (real growable HDF5 datasets, resize-and-append, one group per
ROI, wavelength axis stored once), `mark_recording_started()`. Explicitly does NOT write
image pixel data (that's `image_writer.py`'s job - this only records a manifest pointing at
it) or the experiment-control plan table (already covered by
`lspr_acq_shell.experiment_control_export`'s existing HDF5 path via
`lspr_io.build_experiment_plan_row_table` - wiring that into a live session file is a
GUI-integration follow-up, not a missing writer capability). `read_imaging_session()` is the
read-side counterpart - reconstructs a real `ImagingAcquisitionSettings` (with both
override dicts repopulated), `AreaRoi`/`AreaRoiGroup` lists, and the three assignment-table
dicts/lists from a file, proven via full write-then-read round-trip tests, not just
per-table isolation. 18 new tests (`tests/test_hdf5_export.py`).

**What's still not done** (all explicitly out of scope for this slice, not overlooked):
wiring any of this into the GUI - no "Save Session"/"Load Session" menu action exists yet,
`ExperimentControlWindow`'s already-persisted valve/switch/color-palette state isn't yet
piped into the writer, and `SweepPipeline`/image cube saving isn't yet connected to
`ImagingMeasurementWriter` at all (the sweep pipeline and this writer are still two
independently-tested, unconnected pieces). Illumination/camera settings GUI (the panel
where a user would actually set per-wavelength exposure) doesn't exist yet either -
`ImagingAcquisitionSettings` is buildable programmatically/by tests but nothing in the app
constructs one from user input yet. These are the natural next slices.

---

## 2026-08-09 (continued): Illumination/camera settings panel built - the first GUI piece

Maintainer asked to keep going; offered three roughly-equal-sized next slices (settings
panel, wiring experiment-control state + Save/Load Session into the writer, or wiring a
live sweep to the writer) rather than guessing given how disconnected the pieces still
were - maintainer picked the settings panel, matching the first step of their own workflow
description ("user will setup the illumination parameters... then user setup camera
parameters").

**Built**: `apps/LSPRi/acq/src/lspri_acq_app/gui/illumination_camera_settings_panel.py`
(new file), `IlluminationCameraSettingsPanel` - one `QTableWidget` row per swept
wavelength, each row always carrying an explicit `WavelengthCameraSettings`/
`WavelengthIlluminationSettings` override (not a "blank cell means use the global
default" table - traced that ambiguity would be a real UX trap, since a blank/zero settle
time is not the same thing as "ask illumination.settle_time_ms()"). Gain/Settle/Current use
`QDoubleSpinBox.setSpecialValueText()` at a sentinel minimum for "not set" -> `None`, the
standard Qt idiom for an optional numeric field, instead of a second checkbox widget per
field. `current_settings() -> ImagingAcquisitionSettings` builds a real settings object
from whatever's in the table right now; `load_settings()` is the inverse, for repopulating
the table after a session restore (`read_imaging_session()`, previous entry).

**v1-scoped, matching this app's established "lean panel, not everything at once"
pattern**: exposes wavelength/exposure/gain/binning/settle/current/spectrum-source;
resolution/crop/saving_mode (also part of `WavelengthCameraSettings` and the v6.4
`camera_settings` schema) aren't editable from this panel yet - noted in the module
docstring as a natural "advanced" dialog per row, not a wider default table.

Embedded in `MainWindow` as a third column in the existing horizontal splitter (was
roi_panel | experiment_control_window, now roi_panel | settings_panel |
experiment_control_window) - a reasonable v1 placement, not treated as a final layout
decision. Verified via a real headless launch (not just unit tests): constructed
`MainWindow`, added two wavelength rows through the actual embedded widget, read back a
real `ImagingAcquisitionSettings` with both wavelengths - `QT_QPA_PLATFORM=offscreen`,
confirmed working end to end, not just importable. Screenshot verification not attempted
this round (would need the primary-monitor correction from earlier in the session applied
first) - the headless launch plus 10 new real-widget unit tests
(`tests/test_illumination_camera_settings_panel.py`) is the evidence for this slice.

**Verified**: 231 tests in `lspri_acq_app`'s own suite (was 211), all passing. Full
umbrella suite: 956/957 (the one failure is `test_async_writer_reports_failure_via_on_error_callback`,
the same pre-existing Windows temp-file-cleanup flake documented multiple times earlier in
this log, confirmed reproducible in isolation and unrelated to anything touched here -
the umbrella suite doesn't even import this new panel). `pyflakes` clean.

**Still not done, same as the previous entry's list minus this item**: no Save/Load Session
menu action, `ExperimentControlWindow`'s valve/switch/color-palette state still isn't piped
into the writer, and `SweepPipeline` still isn't connected to `ImagingMeasurementWriter` -
the settings panel can build an `ImagingAcquisitionSettings` now, but nothing consumes one
yet (no "start sweep with these settings" button exists).

---

## 2026-08-09 (continued again): Save/Load Session wired into MainWindow

Maintainer said to keep going without specifying which of the two remaining slices
(experiment-control-state + Save/Load Session, or live-sweep wiring) - picked the smaller,
more bounded one first, consistent with how every other multi-slice effort this session was
sequenced (land the safer/smaller piece, then the riskier one).

**New public seams added** so `MainWindow` doesn't have to reach into either panel's
private state:
- `ExperimentControlWindow.assignment_table_state()` / `apply_assignment_table_state()`
  (`gui/experiment_control_window.py`) - read/write the window's valve-label, valve-color,
  color-palette, and switch-solution-label state. Deliberately separate from
  `_save_experiment_control_settings()`, which is this window's own independent per-user
  JSON persistence, not a session file - conflating the two would have made "restore this
  session" and "restore my UI preferences from last time I ran the app" the same operation
  when they're not. `apply_*` re-populates the color/switch combos immediately (not just the
  backing lists) so a restored session is visibly correct, not just correct on next read.
- `RoiPanel.load_rois()` (`gui/roi_panel.py`) - replaces every current ROI (with its real
  pyqtgraph overlay items, via the existing `_add_roi_object`) with a given list, the
  session-restore counterpart to `add_roi()`'s "one new default-shaped ROI" case.

**`MainWindow.save_session()`/`load_session()`** (`gui/main_window.py`) - plain methods
(not embedded in the button click handlers) so they're directly testable without driving a
real native file dialog. `save_session()` writes illumination/camera settings from the
settings panel, ROI definitions from the ROI panel, and valve/switch/color-palette state
from the experiment-control window into one `ImagingMeasurementWriter` file.
`load_session()` is the exact inverse via `read_imaging_session()`. Two "Save Session..."/
"Load Session..." buttons added to the header row (`QFileDialog` for the path) - `MainWindow`
is a plain `QWidget`, not `QMainWindow`, so no menu bar exists to hang a menu action off of;
buttons were the pragmatic v1 choice, not a considered rejection of a menu.

**Explicitly does NOT yet touch**: the experiment-control plan table itself (already has
its own import/export path per `hdf5_export.py`'s module docstring - wiring that into the
same file is a separate follow-up) or anything sweep/recording-related (still nothing
connects `SweepPipeline` to `ImagingMeasurementWriter`).

**Verified**: real round-trip test (`tests/test_main_window.py`, new file) - populates all
three panels through their real widgets, saves, loads into a *second* fresh `MainWindow`,
and asserts the restored settings/ROIs/valve-label/color-palette/switch-label state matches,
not just that individual writer/reader calls succeeded in isolation (that was already proven
in the earlier `test_hdf5_export.py` entry - this proves the GUI wiring on top of it). Also
a dedicated regression test that save-then-immediately-load doesn't hang/raise (catches the
class of bug where a writer is left open/locked on some path). Headless launch smoke test
(`QT_QPA_PLATFORM=offscreen`) confirms the same round trip through the real
`MainWindow.save_session()`/`load_session()` entry points, not just the test harness.
6 new tests in `test_experiment_control_window.py`, 4 in `test_roi_panel.py`, 4 in the new
`test_main_window.py`. `lspri_acq_app` suite: 221 -> 235 passing. Full umbrella suite:
957/957 clean this run. `pyflakes` clean (also fixed one small pre-existing unused-variable
warning in `test_sweep_pipeline.py`, left over from an earlier entry today, while in there).

**Still not done**: no live sweep wired anywhere in the GUI, `SweepPipeline` still isn't
connected to `ImagingMeasurementWriter`, and the plan table itself still isn't part of the
session file. The next natural slice is wiring a real (simulated-device) sweep to the image
view/ROI processing/recording gate - the last of the three options offered earlier today.

---

## 2026-08-09 (continued yet again): Found and fixed a real shared-code bug; full-scope analysis of "one shared experiment-control panel" requested and delivered

Maintainer hit a real runtime symptom while using LSPRi acq's experiment-control panel:
`Could not parse stylesheet of object QToolButton(..., name = "directionButton")`, twice.
Traced to `packages/lspr_acq_shell/src/lspr_acq_shell/experiment_control_builders.py`'s
`create_direction_button()`: Python's `%` (string substitution) binds tighter than `+`
(concatenation), so `A % theme + B + C % theme` parses as `(A % theme) + B + (C % theme)` -
the middle segment (the `:hover` stylesheet) never got its `%(button_hover)s`/
`%(border_hover)s` placeholders substituted, leaving literal `%(...)s` text that Qt's CSS
parser can't understand. Fixed by wrapping the whole template in one set of parens and
applying `%` once. Verified via a headless `ExperimentControlWindow()` construction with no
parse warnings. Since this file is shared, the bug silently affected sLSPR acq's direction
buttons too, not just LSPRi acq's.

Maintainer then asked to stop chasing visual parity piecemeal (this bug being a symptom of
exactly that - two independent implementations drifting) and instead scope consolidating
the experiment-control panel into **one real shared implementation**: *"I don't want to have
two models... the backend can be rewritten as much as possible for porting and better
modularity... simplification would be really great."* Explicitly asked for a full analysis
before any more implementation.

Started tracing the two pieces already in flight (`ExperimentPlanTableModel`/its 8
delegates, `ExperimentControlDialogs`) and found them **more portable than the earlier
"not worth sharing" judgment call assumed** - `ExperimentPlanTableModel.__init__` takes no
window reference at all (zero coupling), `ExperimentControlDialogs` is a small class taking
`parent`/`theme_palette`/`contrast_text_color`/`tint_icon` with clean parameter-in/return-out
`edit_*` methods (only 4 lines touch `self._parent` across 1,605 lines), and the 8 delegates
need only a ~7-hook duck-typed `window` contract, the same pattern already proven for
`PlanRunLoopMixin`. But also found a real complication: `ExperimentPlanTableModel` has no
`insert_step`/`remove_step`/`move_step`/`duplicate_step` methods at all - the *window* owns
the step list and pushes the whole thing back via `set_steps()` (full model reset), the
opposite of LSPRi acq's own model (which owns inserts/removes directly). Migrating LSPRi acq
onto the real model means rewriting how its window manages its step list, not swapping one
model class for another.

Given that, did the full-scope inventory requested rather than continuing to implement:
read `experiment_control_window.py`'s full method list (283 methods, 5,547 lines) plus
sized `experiment_control_dialogs.py` (1,605), `flow_plan_model.py` (1,123),
`experiment_control_editing.py` (739 - the copy/paste controller, not previously accounted
for in any tier's scoping), `undo_support.py` (75), `ui_helpers.py` (19) - **~9,100 lines
total still un-shared**, categorized by responsibility into what's already effectively
shared, what's genuinely portable but not yet moved (~120 methods: switch/color combo UI,
column persistence, theme/style - currently *duplicated* not shared, which is exactly what
produced today's bug - table population, drag-reorder), and several **standalone
subsystems each roughly tier-sized on their own**: plan CSV/HDF5 import-export UI (~35
methods, beyond Tier 0's background-task classes), pause-row template (~17 methods),
spreadsheet-style cell navigation/editing (~18 methods + a full separate 739-line copy/paste
controller - this is literally the "editing behaviour" asked about by name), view-mode
splitter-size memory (~30 methods), time-unit toggle, and progressive/lazy plan-row loading
for large saved plans. Full categorized breakdown, with a proposed staged Tier 3a/3b/3c+
sequence, written into §14 of the plan doc (`lspri_acq_architecture_and_shared_shell_plan.md`)
rather than kept only in chat - durable reference for whichever tier gets picked up next,
by this agent or a future one.

**Not implemented**: this entry and §14 are the analysis the maintainer asked for. Nothing
beyond the stylesheet bug fix was changed in this pass - next step is the maintainer
choosing where in the staged sequence to start (§14.4 lays out the recommendation: Tier 3a
first - the table model + dialogs, already the most-traced and lowest-risk of the pieces
identified).

---

## 2026-08-10: Tier 3a landed (sLSPR acq side) - table model, delegates, dialogs, undo support now real shared code

Maintainer picked Tier 3a. Traced two more real construction/wiring entry points before
moving anything - `gui/experiment_control_table.py` (`configure_experiment_control_plan_table`,
the actual `ExperimentPlanTableModel(...)` construction site - not in
`experiment_control_window.py` itself, confirming the maintainer's own read that "features
are spread across the code") and `gui/experiment_control_plan_view.py`
(`configure_experiment_control_plan_view`, the delegate-installation + model-setter wiring).
Both turned out to be **already portable as written** - `configure_experiment_control_plan_view`
duck-types on `window` via the exact same contract the delegates already establish, no
sLSPR-acq-specific state. Checked every remaining dependency before assuming portability:
`PUMP_DISPLAY_MAX_LENGTH`, `DeviceLifecycleController`, `SELECTOR`, `flow_tabler_icon`/
`tint_tabler_icon`, `make_compact_spinbox` were **all already available from `lspr_acq_shell`
or `lspr_ui`** - sLSPR acq's own `ui_helpers.py`/`icon_helpers.py`/`device/reglo_icc.py` are
themselves already thin shims. Only the plan-table's `ExperimentPlanTableModel` needed a real
generalization: `to_core_experiment_plan()` requires `app_name`/`app_version`, which a shared
package can't get from `lspr_app.version` - both `ExperimentPlanTableModel` and
`ExperimentControlDialogs` (the latter only for its pause-state dialog, which builds a model
too) now take both as required keyword-only constructor args, matching the "no default
because a wrong one would misrepresent the data" rule used elsewhere (`Camera.capabilities()`).

**Correction to the previous entry's "zero test coverage" claim**: that search only checked
the sLSPR acq submodule's own `tests/` directory. The umbrella repo's `tests/unit`/
`tests/integration` actually carry substantial real coverage for this exact code -
97 passing tests across `test_experiment_control_copy_paste.py`,
`test_experiment_control_pump_dispatch.py`, `test_experiment_control_selection_overlay.py`,
`test_experiment_control_timeline_font.py`, `test_experiment_control_undo.py`,
`test_pump_display_global_highlight.py`, `test_flow_plan_model_startup_popup.py`,
`test_experiment_control_live_editing.py`, `test_experiment_control_plan_table_extended_wheel.py`,
plus several more runtime/state-logging/navigation files - found by actually searching the
right place before concluding a from-scratch characterization pass was needed. This became
the real safety net for the move instead.

**Moved to `lspr_acq_shell`** (new files): `experiment_plan_table_model.py`
(`ExperimentPlanTableModel` + all 8 delegates + `safe_color_name`/`_contrast_text_color`/
`seconds_to_display_value`/`display_value_to_seconds`/`clamped_flow_ul_min`/
`_HighlightingCommentLineEdit`), `experiment_control_dialogs.py` (`ExperimentControlDialogs`
+ `PaletteTableWidget`/`PaletteNameDelegate`/`SwitchSolutionTableWidget`/`SwitchSolutionEdit`/
`ValveLabelEdit`/`ValveLabelTableWidget`/`PauseStateTableView`), `experiment_control_plan_view.py`,
`experiment_control_table.py`, `undo_support.py` (`SnapshotCommand`/`push_snapshot`, zero
coupling to begin with). sLSPR acq's own five files are now thin re-export shims (matching
the Tier 0/1/2 pattern exactly); `experiment_control_window.py`'s five `ExperimentControlDialogs(...)`
call sites and its one `configure_experiment_control_plan_table(...)` call site updated to
pass `app_name="LSPR Acquisition", app_version=APP_VERSION`.

**Verified**: the 97 tests above, re-run against the moved code - 3 failed on the first pass
(`unittest.mock.patch("lspr_app.gui.flow_plan_model.QComboBox", ...)` etc. - patch-by-string-path
targets that pointed at names no longer defined in the shim module's own namespace), fixed by
updating those patch targets to the real new location (`lspr_acq_shell.experiment_plan_table_model`),
the same category of fix Tier 2's own characterization file needed when `monotonic()`'s
patch target moved. Two direct `ExperimentPlanTableModel(...)` test call sites needed
`app_name`/`app_version` added. All 97 pass afterward. Full umbrella suite: 957/957. LSPRi
acq's own suite (untouched so far, but depends on `lspr_acq_shell.pump_plan`/
`experiment_control_builders`/`device_lifecycle` which didn't change): 235/235. `pyflakes`
clean (five underscore-prefixed re-exports needed adding to the shim's `__all__` - pyflakes
doesn't know `unittest.mock.patch`-by-string-path or a sibling test file's import is a real
use).

**Not done yet**: LSPRi acq is still on its own lean `PlanTableModel`/3 delegates/4 lean
dialogs - none of the code moved in this entry is used by LSPRi acq yet. That migration
(§14.4's step-list-ownership rewrite) is the next, separately-scoped piece.

---

## 2026-08-10 (continued): LSPRi acq migrated onto the real shared table model/delegates - Tier 3a complete

Maintainer said to keep going. Migrated `ExperimentControlWindow` from its own lean
`gui/plan_table_model.py` (`PlanTableModel` + `ValveDelegate`/`SwitchSolutionDelegate`/
`DirectionDelegate`) to the real shared `ExperimentPlanTableModel` + all 8 delegates from
the previous entry - `gui/plan_table_model.py` and its dedicated `tests/test_plan_table_model.py`
are **deleted**, not just superseded, per the maintainer's explicit "I don't want to have
two models."

**What the migration actually required**, traced by reading the full 1,484-line window and
the real dependency chain, not assumed from the earlier scoping note:

- **Duck-typed host contract additions**: `PLAN_COLUMNS` (a length-only placeholder list -
  `build_experiment_control_headers()` overwrites every entry), 7 column-index helpers
  (`_flow_rate_column`/`_direction_column`/`_tube_column`/`_valve_column`/`_switch_column`/
  `_color_column`/`_description_column`, matching the shared model's own `4 +
  ACTIVE_PUMP_CHANNELS*3 + ...` column arithmetic exactly), `_color_combo_popup_width`/
  `_update_color_combo_style` (ported from sLSPR acq's window - the color delegate's popup
  sizing/live-recolor logic, non-optional, called on every editor open), and a documented
  no-op `_install_table_wheel_scroll_filter` (the wheel-scroll-cycling subsystem itself is
  a separate, not-yet-shared piece - plan doc §14.3 - and this window has no `eventFilter`
  override for it to plug into anyway, so a no-op is honest, not a shortcut).
  `_populate_color_combo`/`_populate_switch_solution_combo`/`_switch_display_text` already
  existed with matching signatures from the earlier visual-parity work - no changes needed.
  `manual_tube_spins` is a plain alias for the existing `tube_diameter_spins` list (the
  shared wiring code's own attribute name), not a rename.
- **Step-list ownership rewrite**: `ExperimentPlanTableModel` has no
  `insert_step`/`duplicate_step`/`remove_step`/`move_step` - `_add_experiment_control_step_from_editor`/
  `_on_duplicate_step_clicked`/`_on_delete_step_clicked`/`_on_step_move_requested` were
  rewritten to read `self._table_model.steps()`, mutate a plain Python list the same way
  the old model methods did internally, and push it back via `set_steps()` (a full model
  reset) - the same ownership model sLSPR acq's own window already uses.
  `_read_experiment_control_steps()`/`_pause_row_step()` needed no changes at all - both
  already called `.steps()`, which the shared model implements identically.
- **Pause template**: switched from a second `PlanTableModel` instance to
  `build_experiment_control_pause_model()` (the exact function sLSPR acq's own popup pause
  dialog uses internally to build its one-row preview model) + `configure_experiment_control_plan_preview()`
  for the delegates - kept as an embedded table rather than switching to sLSPR acq's popup
  `QDialog` UX, a separate, still-standing simplification, now backed by the real model.
- **A real bug caught by testing, not by inspection**: the shared model caches
  valve-label/color-palette/switch-solution display state internally via explicit setters
  (`set_valve_state_labels()` etc.), unlike the retired lean delegates, which read
  `window._valve_state_label()` fresh on every paint. `_edit_valve_state_labels`/
  `_edit_color_palette_entries`/`_edit_switch_solution_labels`/`apply_assignment_table_state`
  all updated `self._valve_state_labels` etc. but never told either table model about it -
  so an edited valve label would show correctly on the button/combo widgets but silently
  keep displaying the *old* label in the actual plan table rows. Caught by rewriting
  `PlanTableDelegateWiringTests`' `displayText()`-based assertion (which no longer applies -
  the real `ExperimentPlanValveDelegate` has no `displayText()`; label formatting is the
  model's `data()`, not the delegate's) into a `model.data(...)`-based one, which failed
  until a new `_sync_table_models_display_state()` helper (pushes all four pieces of state
  into both the main and pause-template models) was added and wired into all four edit
  paths.
- **App identity**: `ExperimentPlanTableModel`/`build_experiment_control_pause_model` both
  now take `app_name="LSPRimaging Acquisition", app_version=APP_VERSION` (from
  `lspri_acq_app.version`), the same requirement Tier 3a's first half added for sLSPR acq.

**Verified**: full LSPRi acq suite 226/226 (235 minus the 9 retired `test_plan_table_model.py`
tests), full umbrella suite 957/957, `pyflakes` clean (two now-unused imports removed:
`PlanColorDelegate`, the unused `configure_experiment_control_plan_view` re-import). A real
headless end-to-end smoke test through `MainWindow` (not just unit tests) - add/duplicate a
step, edit a valve label and confirm the *table* (not just the button) shows it, save a
session, load it into a *second* `MainWindow`, confirm the relabel survived the round trip.

This closes Tier 3a in full: sLSPR acq and LSPRi acq now share one real
`ExperimentPlanTableModel` + 8 delegates + dialog layer implementation, not two. See the
plan doc's §14.4 for what's left of the broader "one shared panel" effort (Tier 3b theme
sharing, Tier 3c+ standalone subsystems - import/export UI, pause-row-as-dialog, spreadsheet
editing, view-mode memory, lazy row loading).

---

## 2026-08-10 (continued): Tier 3b - theme shared, not duplicated

Maintainer said to go for Tier 3b next. Read both apps' real `_theme_palette`/`_apply_style`
in full before moving anything (sLSPR acq's is ~310 lines, not the ~160 lines skimmed
earlier when scoping §14 - the earlier size estimate undercounted it). Found the two
weren't actually identical: sLSPR acq's dark/light palette dicts matched LSPRi acq's dark
one exactly (no color drift), but sLSPR acq's stylesheet has real rules LSPRi acq's lacks
(`QGroupBox`, `accentButton`/`dangerButton` object-name rules, `flowViewModeButton`,
`flowColorAddButton`/`RemoveButton`, `flowSwitchModeButton`/`SwitchSettingsButton`/
`ValveSettingsButton`/`CommentDisplayButton`, `flowHeaderLabel`, and finer-grained
`QComboBox::drop-down`/`::item`/`QDoubleSpinBox` button-suppression rules inside the plan
table) - while LSPRi acq's own copy has one real rule sLSPR acq's lacks
(`QToolButton#flowIconButton:checked`, used by its checkable hold/pause/edit-mode toggle
buttons). Merged rather than picked one side: the new shared template is the union - sLSPR
acq's fuller rule set plus LSPRi acq's `:checked` addition, which is harmless to sLSPR acq
(it never puts a `flowIconButton` into the checked state, so the rule simply never matches
there) and gives LSPRi acq's window a batch of "free" polish (group-box/accent/danger-button/
table-child-widget styling) it never had, for whatever it builds next that uses those object
names.

**New**: `lspr_acq_shell/experiment_control_theme.py` -
`experiment_control_theme_palette(mode)` (both dark/light dicts, moved verbatim - sLSPR acq
is the only current caller of light mode, but sharing the whole bidirectional palette costs
nothing) and `apply_experiment_control_style(widget, palette)` (the merged stylesheet
template). Both apps' `_theme_palette()`/`_apply_style()` are now two-line wrappers calling
into it.

**Verified**: new `tests/unit/test_experiment_control_theme.py` (9 tests - palette key
completeness for both modes, dark != light, unknown-mode fallback, fresh-copy-per-call,
stylesheet applies without raising, no leftover `%(...)s` placeholders, the merged
`:checked` rule is actually present, a missing palette key raises `KeyError` rather than
silently producing a broken stylesheet). Full sLSPR acq experiment-control suite: 113/113
(104 + the 9 new ones). Full LSPRi acq suite: 226/226 (unchanged - theme sharing touched no
step/table logic). Full umbrella suite: 966/966. `pyflakes` clean. Real headless
construction check on both windows confirmed the `:checked` rule is present in the actual
rendered stylesheet, not just the template string.

Tier 3b done. Remaining per §14.4: Tier 3c+ (plan import/export UI, pause-row-as-dialog,
spreadsheet-style cell navigation/editing, view-mode splitter memory, time-unit toggle,
progressive/lazy plan-row loading) - each roughly its own tier-sized project, prioritized
by what the maintainer actually wants LSPRi acq to have next.
