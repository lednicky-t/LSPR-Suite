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
