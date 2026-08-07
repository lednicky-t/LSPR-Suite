# Camera backend generalization, IDS uEye evaluation, and sweep-cycle throughput

Follow-on to the Basler-only capture/ROI/disk-write results already in `benchmark_ui.py`'s
"Phase 0 results" section of
[`lspri_acq_architecture_and_shared_shell_plan.md`](../../../docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md).
This covers: generalizing the spike tool to more than one camera vendor, evaluating a
third camera model against the two Baslers already tested, and closing the "full
spectral-cube sweep-cycle rate" question that section explicitly left open — not with
one true end-to-end run (LCTF + camera together), but with both halves independently
measured on real hardware, which is enough to answer it.

## 1. `CameraBackend` — generalizing `benchmark_ui.py` beyond one vendor

`benchmark_ui.py`'s `CameraGrabThread` originally called `pypylon` directly. It now
drives cameras through a small `CameraBackend` ABC (open, native_size,
apply_pixel_format, apply_binning, apply_exposure_us, maximize_throughput,
start_grabbing, retrieve_frame, stop_grabbing, close) with two implementations:

- `PylonBackend` — the original Basler/pypylon logic, unchanged in behavior.
- `UeyeBackend` — new, for IDS uEye cameras via `pyueye`.

Both backends validate pixel format, binning, and exposure against whatever camera
actually connects (falling back and logging rather than failing outright), instead of
assuming one model's capabilities. A "Camera:" dropdown (Auto-detect / Basler / IDS)
picks the backend; auto-detect probes both without opening either and prefers Basler if
both are present.

**Important asymmetry to know about**: `pypylon` can be imported even with no camera
attached. `pyueye`'s `ueye` submodule loads IDS's own `ueye_api.dll` at *import* time
and raises immediately if the IDS Software Suite driver isn't installed - confirmed on
this machine (the camera showed "Error" status in Windows Device Manager, and
`from pyueye import ueye` failed, until the driver was installed mid-session). Because
of this, every `pyueye` import in `UeyeBackend` is lazy (inside methods, not at module
load) so the tool still runs and reports 0 IDS cameras rather than crashing when the
driver isn't present. `pyueye` itself is `pip install`-able; the driver is not - it's a
separate installer from ids-imaging.com. Documented as an optional dependency in the
umbrella repo's `requirements.txt`, same pattern as `AMFTools`.

## 2. Camera comparison — three real models now

| | **a2A3840-45umBAS** (tested, Phase 0 §3) | **acA5472-17um** (tested) | **UI-3160CP-M-GL Rev.2.1** (tested) |
|---|---|---|---|
| Sensor | Sony IMX334, 1/1.8" | Sony IMX183, 1" | onsemi PYTHON2000, 2/3" |
| Resolution | 3840×2160 (8.3 MP) | 5472×3648 (~20 MP) | 1920×1200 (2.3 MP) |
| Pixel size | 2.0 µm | 2.4 µm | 4.8 µm |
| Max frame rate | ~45 fps | ~17 fps | ~165 fps (datasheet, Mono8) |
| Shutter | Rolling | Rolling | **Global** |
| Interface | USB3 | USB3 | USB3 |

Maintainer's working decision: stay on the a2A3840-45umBAS as primary, evaluate the IDS
camera as a documented alternative rather than switch. Two points worth remembering if
this gets revisited:

- **Global shutter is specifically relevant here**, not just generically nice to have.
  Every other camera tested is rolling-shutter, which means different rows of one frame
  are exposed at slightly different times - if a frame is captured while the LCTF is
  still mid-transition, different rows can see different wavelengths in the same image.
  Global shutter can't do that; every pixel integrates the same window.
- The IDS camera's much larger pixels (4.8µm vs 2.0-2.4µm, ~4-6× the area) trade
  resolution (2.3MP vs 8.3/20MP) for per-pixel light-gathering/dynamic range - a real
  trade, not a strict downgrade, and orthogonal to the shutter question.
- Maintainer's own assessment of the LED-PWM/rolling-shutter striping risk: not a big
  issue in practice, since exposure time can be lengthened (at reduced LED intensity) to
  span multiple PWM cycles and average out the banding - global shutter would remove the
  *spatial* (row-to-row) version of this artifact structurally, but doesn't remove
  frame-to-frame flicker if the PWM period beats against the frame period.

## 3. IDS UI-3160CP-M-GL Rev.2.1 — verified specs and a real bug found and fixed

Confirmed live against the actual camera (not just datasheet): model reports as
`IDS uEye (UI316xCP-M)`, native resolution 1920×1200. Mono8/Mono10/Mono12 all capture
correctly with exactly the expected value range (Mono10 ≤1023, Mono12 ≤4095 - confirms
both are delivered in a 16-bit container). 2×2 binning produces exactly 960×600 frames.

**Bug found: default pixel clock + frame-rate cap silently limits throughput to ~25fps.**
First timed-benchmark run at Mono12/native/1×1 measured **25.03 fps** - and, tellingly,
**2×2 binning made no difference**, which is the signal that this isn't a bandwidth/data-
volume limit (binning would have helped a real bandwidth cap) but something else. Root
cause, confirmed via `is_PixelClock`/`is_GetFrameTimeRange`: a freshly-initialized uEye
camera defaults to a conservative pixel clock (200MHz here, out of a 120-400MHz range)
*and* a frame-rate cap that settles at ~25fps regardless of exposure or binning - neither
is a real sensor/USB limit.

**Fix**: `UeyeBackend.maximize_throughput()` (called after binning, before exposure is
applied) explicitly raises the pixel clock to its max and requests the fastest legal
frame rate via `is_SetFrameRate`. Verified before/after on the bench:

| Setting | Before fix | After fix |
|---|---|---|
| Mono8, native, 1×1 | 25 fps (implied by the bug) | **~117 fps** |
| Mono12, native, 1×1 (maintainer's real settings) | 25.03 fps (measured, `benchmark_ui.py` run) | **85.07 fps** (measured, `benchmark_ui.py` run: 2552 frames/30s, 0 late frames, ROI extraction avg 0.461ms/max 2.130ms for 10 ROIs) |

Datasheet ceiling is ~165fps at Mono8, so there's likely still some headroom
(exposure/queue-depth tuning) beyond the ~117fps measured here, but the fix alone was a
~3.4-4.7× improvement and removed an artificial floor that had nothing to do with real
hardware limits.

**Also fixed**: `is_WaitForNextImage`/`is_InitImageQueue` are marked deprecated in this
pyueye release (`pyueye==4.96.952`, no bundled replacement for the queue-capture pattern
IDS's own official example uses - confirmed still functionally correct). First
suppression attempt (filtering by `module="pyueye"`) silently didn't work: pyueye's
`deprecated()` wrapper passes `stacklevel=2`, which attributes the warning to whichever
code *called* the deprecated function (i.e. `benchmark_ui`/`__main__`), not to `pyueye`
itself - a module-based filter never matches. Fixed by filtering on the warning's
message text instead, which is immune to stacklevel attribution. Verified with
`warnings.simplefilter("always")` forcing everything else to show: zero of the targeted
warnings leaked through across a real multi-second capture run.

## 4. Software-trigger latency — the piece the sweep-cycle question actually needed

The maintainer's stated acquisition pattern: **set wavelength → wait for settle →
acquire one frame → repeat**, restarting from the first wavelength after a full sweep;
sweep direction doesn't matter functionally (ascending measured faster, per the
settle-time analysis below). This is software-triggered single-shot capture, not the
continuous free-running mode `benchmark_ui.py`/`UeyeBackend` currently implements (that
mode answers a different question: "what's the fastest sustained streaming rate", not
"how long from trigger to one frame in hand"). Measured separately, standalone, using
the uEye SDK's actual single-shot mechanism (`is_SetExternalTrigger(IS_SET_TRIGGER_SOFTWARE)`
+ blocking `is_FreezeVideo(IS_WAIT)`), n=200 reps per setting:

| Setting | Exposure | Median round-trip | Stdev (jitter) | p99 | Max |
|---|---|---|---|---|---|
| Mono8, native 1920×1200 | 0.95ms | 12.8ms | 0.34ms | 13.4ms | 16.5ms |
| Mono8, native 1920×1200 | 5.0ms | 16.7ms | 0.21ms | 17.9ms | 18.0ms |
| **Mono12, native 1920×1200 (real settings)** | 1.15ms | **17.0ms** | 0.38ms | 18.4ms | 20.6ms |
| Mono8, 2×2 binned (960×600) | 0.95ms | 7.8ms | 0.34ms | 8.8ms | 11.1ms |

Round-trip time is mostly readout/transfer, not fixed USB overhead (binning to a quarter
the pixels roughly halves the non-exposure overhead; Mono12's 2× the bytes of Mono8 adds
~4ms). Jitter is tight (~0.2-0.4ms stdev under normal conditions, occasional outliers to
+3-4ms) - low enough to be schedulable in principle, but see the conclusion below for why
that turned out not to be necessary.

**This measurement used the classic uEye single-shot software-trigger path
(`is_SetExternalTrigger`/`is_FreezeVideo`), which is NOT yet implemented in
`UeyeBackend`** (which still only does continuous free-run capture via
`is_CaptureVideo`/`is_WaitForNextImage`). Adding a triggered single-shot mode to
`UeyeBackend` was discussed and deliberately deferred - the maintainer will implement it
directly in the real app rather than in this throwaway spike tool.

## 5. LCTF settle time and passband calibration (prior session, folded in here)

Two more pieces of the "what actually limits sweep-cycle rate" answer, measured in a
separate session against the real VariSpec VIS filter (400-720nm, serial 52366) using
`illumination_probe.py` (same `spikes/lspri_acq_phase0/` tool family). Full detail and
methodology in their own docs, not reproduced here - headline numbers only:

**[`settle_time_analysis.md`](settle_time_analysis.md)** - 792 optically-measured
transitions, up/down, five step sizes:

| Tier | p99 | max observed | suggested margin |
|---|---|---|---|
| Small step (5-40nm), ascending | 28.0ms | 32.0ms | ~35-40ms |
| Small step (5-40nm), descending | 57.0ms | 72.0ms | ~80ms |
| Full-range jump (320nm), ascending | 47.2ms | 48.0ms | ~55ms |
| Full-range jump (320nm), descending | 28.1ms | 28.1ms | ~35ms |
| Single global constant (covers everything) | 48.0ms | 72.0ms | ~80-90ms |

Direction matters more than step size; a direction-aware two-tier margin captures most
of the achievable benefit over one flat constant.

**[`lctf_passband_centroid_shift.md`](lctf_passband_centroid_shift.md)** - 61-point
optical spectral sweep (420-720nm, 5nm steps): mean measured-vs-commanded wavelength
shift **-0.31nm** (std 0.51nm, range -1.45 to +0.88nm), no wavelength-dependent trend
(a roughly constant small offset, not a scale/calibration error). Largest deviations
cluster in the filter's known 560-585nm low-throughput band and at the 705-720nm range
edge - lower signal-to-noise there, not necessarily worse optical tuning. An offset
correction table (`../lctf_wavelength_offset_calibration.csv`) is available for Phase 1
if per-point wavelength correction is wanted.

## 6. Conclusion: the "full spectral-cube sweep-cycle rate" question

The architecture plan's Phase 0 section left this explicitly open: *"the real v1
design's actual rate-limiter (set wavelength → settle → grab, repeated per wavelength
step) is illumination settle time × step count, a different metric entirely from camera
fps... untested and needs the real LCTF/LED driver to measure."*

No single combined end-to-end run (LCTF + camera together, one process) was performed -
that's still a fair thing to do once the real app's acquisition loop exists. But both
halves are now independently measured on real hardware, which is enough to answer the
question that mattered:

- **LCTF settle time dominates the per-step budget, not camera latency.** At the
  maintainer's real settings (Mono12, native resolution), one software-triggered
  capture takes ~17ms (p99 18.4ms) - call it ~20ms with margin. The LCTF's own settle
  time is ~35-40ms even in its *best* case (small ascending steps) and up to ~80-90ms
  in its worst (descending, or a single safety margin covering all directions). Camera
  latency is well under half the filter's own settle time in every case, and under a
  quarter of it in the worst case.
- **Practical implication for Phase 1's acquisition loop**: trigger the capture right
  after the LCTF reports settled (or after a fixed conservative wait) - there is no
  benefit to pre-arming the camera trigger before settling completes to shave off
  latency, because the camera was never going to be what limits sweep rate. Budget
  per-wavelength-step time as `LCTF settle margin (35-90ms depending on direction/step
  size, see §5 table) + ~20ms camera capture`, dominated by the first term.
  Whole-sweep time is then that per-step budget × number of wavelength points.
- Camera choice for sweep-cycle *speed* is therefore close to moot between the two
  Baslers and the IDS camera - all three are fast enough that none would be the
  bottleneck. The decision between them is a science/imaging-quality call (resolution,
  pixel size/SNR, shutter type - see §2), not a throughput one.

## Caveats

- The trigger-latency numbers (§4) were measured standalone, without the LCTF in the
  loop - real end-to-end per-step time could differ slightly (e.g. if the real app adds
  its own overhead between "settle confirmed" and "trigger sent"), but not enough to
  change the conclusion given the margin involved.
- IDS backend's Mono10/Mono12 16-bit-container assumption and the binning/AOI buffer
  sizing were verified correct on this specific camera/SDK version
  (`pyueye==4.96.952`) - reasonable to expect on other uEye classic-API cameras, not
  independently confirmed on a different model.
- The `maximize_throughput()` fix pushes pixel clock to its absolute max, which
  increases sensor/USB load and heat - fine for a benchmark run, worth a sanity check
  under sustained real-experiment duration (hours) rather than assuming the 30s
  benchmark generalizes indefinitely.
