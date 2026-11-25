---
paths:
  - "apps/**/analysis/**"
  - "apps/**/processing/**"
  - "packages/lspr_core/**"
---

# Numerical and scientific code rules

- **Before/after comparison for any numeric change.** Any change that alters measured or derived scientific values needs a standalone before/after comparison against realistic data, not just unit tests. Flag it to the maintainer with the quantified impact, even when the impact is negligible.
- **Measure performance work.** Verify every performance fix with a real before/after measurement. Instrument new hot paths with cheap debug-level stage timing. Use the `performance-work` skill for the full method, especially when a change could shift computed values or fast-path vs reference-path tolerances.
- **Parallel work takes its worker count from `lspr_core.perf`**, tuned on measured throughput, and must give identical results at any count (full rules: `performance-work` skill; recipe and wiring: `docs/performance_tuning.md`).
- **Vectorize first.** Use vectorized numpy/scipy instead of Python loops on large arrays. Avoid unnecessary copies of large images, spectra, or time series. Add a short comment for any non-obvious optimization. Do not add complex optimization without a measured bottleneck, and do not optimize in ways that make scientific code hard to verify.
- **Library defaults:** numpy (arrays); scipy (fitting, signal processing, optimization, interpolation, statistics); pandas or polars (tabular metadata and exported summaries); xarray (labeled dimensions such as time, wavelength, x/y, channels); scikit-image or OpenCV (images); matplotlib or pyqtgraph (plots). Use numba, multiprocessing, or chunking only after a real bottleneck is identified.
- **Scientific code should:** preserve units and metadata; document assumptions, units, coordinate systems, and calibration steps; never change raw measured data in place; keep raw, processed, and derived results distinguishable; be reproducible from saved inputs and parameters.
- **Keep algorithms pure.** Scientific algorithms must be usable without launching the GUI. Accept explicit inputs and return explicit outputs.
- **Tests:** use small deterministic fixtures; cover edge cases (empty data, saturated images, missing metadata, invalid calibration); use tolerances for floating-point assertions. When adding an algorithm, include units, assumptions, and tests.
