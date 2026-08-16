# VariSpec LCTF passband centroid shift vs. set wavelength

Empirical analysis of `illumination_probe.py`'s tab 4 (optical spectral sweep)
results, run against the real VariSpec VIS filter (400-720nm, serial 52366) on
2026-08-07. Answers: when the filter is commanded to a wavelength, where does
the spectrometer actually see the passband center, and by how much (if at all)
does that shift depend on wavelength?

**Interactive report (charts + full data table):** [lctf_passband_centroid_shift_report.html](lctf_passband_centroid_shift_report.html)
— self-contained, open directly in any browser, no server or internet needed.
A copy is also published at https://claude.ai/code/artifact/85b3d003-c081-4bb2-9da5-0f4826c5517a
(private by default — share it from the artifact page if others need the link).

## Data

`spectral_sweep_(5ms, 200avg spectra, dark removal, corrections on)_20260807_154808.csv`
— 61 points, 420-720nm in 5nm steps, 5ms integration / 200 averages /
dark+nonlinearity correction on, plus a separately-captured lamp-off spectrum
subtracted from every row. Preserved locally in
`spikes/lspri_acq_phase0/illumination_probe_results/` (gitignored, not
committed - raw sweep CSVs are treated as local scratch data, same as the
settle-time sweep files). The derived per-point results table is committed
here as [lctf_shift_table.csv](lctf_shift_table.csv) so the numbers below are
checkable without the raw file.

## Method

For each set wavelength: located the passband peak by argmax within ±30nm of
the set point, then computed an intensity-weighted centroid over the
contiguous run of samples above 50% of local peak amplitude (baseline = median
of the outer edges of the ±30nm search window). Half-max centroid is a
standard, noise-robust way to locate a passband center - less sensitive to
low-amplitude noise in the wings than a plain argmax or a full-window centroid
would be. All 61 points had a resolvable peak.

## Finding 1 — small, roughly constant offset, not a scale error

| Statistic | Value |
|---|---|
| Mean shift | −0.31 nm |
| Median shift | −0.33 nm |
| Std dev | 0.51 nm |
| RMS | 0.60 nm |
| Range | −1.45 nm to +0.88 nm |
| Linear fit slope (shift vs. wavelength) | −0.00026 nm/nm (negligible) |

The near-zero fit slope means this isn't a scale/dispersion calibration
error - the filter doesn't drift further off-target as you go from 420 to
720nm. It behaves more like a small, roughly constant offset (~0.3nm blue of
the commanded value on average) plus point-to-point scatter.

## Finding 2 — the scatter is not uniform; it tracks throughput

The largest deviations (up to −1.45nm and +0.88nm) cluster in two bands:

- **560-585nm**, where measured peak counts drop to their sweep minimum
  (~2,500-5,400 counts vs. 20,000-60,000+ typical elsewhere) - this is this
  filter's known transmission dip near the middle of its range.
- **705-720nm**, at the top edge of the swept range, where throughput is also
  falling off and the raw dark-subtracted baseline itself climbs (up to ~110
  counts vs. near-zero at 450nm), eating into the peak's effective
  signal-to-noise.

Low signal-to-noise makes the half-max centroid noisier to estimate - the
larger shifts in these bands are more likely measurement noise than a real
optical mistuning. In the 450-550nm band, where throughput is highest, shifts
are consistently small (roughly ±0.1-0.6nm).

## Finding 3 — passband width grows with wavelength (expected)

FWHM grows from ~3nm at 420nm to ~17nm at 720nm. This is expected LCTF
behavior (bandwidth widens with wavelength for a fixed-retardance Lyot-type
stage stack), not a measurement artifact.

## Offset correction table

[`../lctf_wavelength_offset_calibration.csv`](../lctf_wavelength_offset_calibration.csv)
(spike root, next to `illumination_probe.py`) - the per-point table above,
reshaped for correcting future commanded wavelengths rather than for analysis.
Columns:

| Column | Meaning |
|---|---|
| `set_nm` | wavelength commanded in this sweep |
| `measured_centroid_nm` | half-max centroid actually measured there |
| `offset_nm` | `measured_centroid_nm - set_nm` (same as `shift_nm` above) |
| `corrected_command_nm` | `set_nm - offset_nm` - **command this instead of `set_nm` if you want the passband to actually center at `set_nm`** |
| `fwhm_nm` | passband width at that point, for context on confidence (wider/noisier bands = less trustworthy offset) |

Only defined at the 61 measured points (420-720nm, 5nm steps); interpolate
between neighboring `set_nm` rows for in-between targets. Per Finding 2 above,
treat offsets in the 560-585nm and 705-720nm bands as less reliable (low
signal-to-noise) than the rest of the range.

## Caveats

- The dark spectrum subtracted from every row has an oddly wide range
  (min −2621, max 24815 counts) - likely edge-pixel/electronic effects rather
  than real optical stray light, worth a look if that dark spectrum is reused
  elsewhere.
- Two `settle_ms` values in the raw sweep (560nm: 10.3ms, 620nm: 8.1ms) are
  well below the ~24ms typical for every other point - looks like a
  busy-check read anomaly rather than a real fast settle. Doesn't affect this
  analysis (which doesn't use `settle_ms`), but flagged in case that column is
  used elsewhere.
- Single run, single filter unit, one ambient temperature (30.78-30.81°C
  across the whole sweep per the filter's own sensor) - no repeat-run or
  thermal-drift data yet to say whether the ~0.3nm offset is stable over time
  or specific to this session.
