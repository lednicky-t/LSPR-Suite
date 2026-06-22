from __future__ import annotations

import logging
import os
import warnings
from dataclasses import replace
from time import perf_counter, thread_time

import numpy as np
from scipy.signal import savgol_filter

from lspr_app.domain.models import ProcessingSettings, Spectrum


_PROCESSING_LOGGER = logging.getLogger("lspr_app.processing")
_DEFAULT_SLOW_PROCESSING_LOG_THRESHOLD_MS = 5.0
_PROCESSING_DEBUG_MODE_ENABLED = False


def set_processing_debug_mode_enabled(enabled: bool) -> None:
    global _PROCESSING_DEBUG_MODE_ENABLED
    _PROCESSING_DEBUG_MODE_ENABLED = bool(enabled)


def processing_debug_mode_enabled() -> bool:
    return _PROCESSING_DEBUG_MODE_ENABLED


def process_spectrum(spectrum: Spectrum | None, settings: ProcessingSettings) -> tuple[Spectrum | None, Spectrum | None]:
    if spectrum is None:
        return None, None

    started = perf_counter()
    started_cpu = thread_time()
    timings_ms: dict[str, float] = {}
    timings_cpu_ms: dict[str, float] = {}

    stage_started = perf_counter()
    stage_cpu_started = thread_time()
    mask = (spectrum.wavelengths_nm >= settings.wavelength_min_nm) & (
        spectrum.wavelengths_nm <= settings.wavelength_max_nm
    )
    timings_ms["mask"] = (perf_counter() - stage_started) * 1000.0
    timings_cpu_ms["mask"] = (thread_time() - stage_cpu_started) * 1000.0
    if not np.any(mask):
        return None, None

    stage_started = perf_counter()
    stage_cpu_started = thread_time()
    wavelengths = spectrum.wavelengths_nm[mask].copy()
    values = spectrum.values[mask].copy()
    timings_ms["slice"] = (perf_counter() - stage_started) * 1000.0
    timings_cpu_ms["slice"] = (thread_time() - stage_cpu_started) * 1000.0

    stage_started = perf_counter()
    stage_cpu_started = thread_time()
    cleaned = _sanitize_curve_values(wavelengths, values)
    timings_ms["sanitize"] = (perf_counter() - stage_started) * 1000.0
    timings_cpu_ms["sanitize"] = (thread_time() - stage_cpu_started) * 1000.0
    if cleaned is None:
        return None, None
    wavelengths, values, replaced_nonfinite = cleaned

    stage_started = perf_counter()
    stage_cpu_started = thread_time()
    if settings.baseline_method == "linear":
        values = values - _linear_baseline(values)
    timings_ms["baseline"] = (perf_counter() - stage_started) * 1000.0
    timings_cpu_ms["baseline"] = (thread_time() - stage_cpu_started) * 1000.0

    stage_started = perf_counter()
    stage_cpu_started = thread_time()
    if settings.smoothing_method == "moving_average":
        values = _moving_average(values, settings.smoothing_window)
    elif settings.smoothing_method == "savitzky_golay":
        values = _savitzky_golay(values, settings.smoothing_window)
    timings_ms["smoothing"] = (perf_counter() - stage_started) * 1000.0
    timings_cpu_ms["smoothing"] = (thread_time() - stage_cpu_started) * 1000.0

    stage_started = perf_counter()
    stage_cpu_started = thread_time()
    processed = replace(
        spectrum,
        wavelengths_nm=wavelengths,
        values=values,
        metadata={
            **spectrum.metadata,
            "processing_wavelength_min_nm": settings.wavelength_min_nm,
            "processing_wavelength_max_nm": settings.wavelength_max_nm,
            "processing_baseline_method": settings.baseline_method,
            "processing_smoothing_method": settings.smoothing_method,
            "processing_smoothing_window": settings.smoothing_window,
            "processing_crop_method": settings.crop_method,
            "processing_crop_fraction": settings.crop_fraction,
            "processing_fit_window_width_nm": settings.fit_window_width_nm,
            "processing_nonfinite_replacements": replaced_nonfinite,
            "fwhm_nm": _compute_fwhm_nm(wavelengths, values),
        },
    )
    timings_ms["metadata"] = (perf_counter() - stage_started) * 1000.0
    timings_cpu_ms["metadata"] = (thread_time() - stage_cpu_started) * 1000.0

    stage_started = perf_counter()
    stage_cpu_started = thread_time()
    fit = fit_processed_spectrum(processed, settings)
    timings_ms["fit"] = (perf_counter() - stage_started) * 1000.0
    timings_cpu_ms["fit"] = (thread_time() - stage_cpu_started) * 1000.0

    total_ms = (perf_counter() - started) * 1000.0
    total_cpu_ms = (thread_time() - started_cpu) * 1000.0
    _log_slow_processing_profile(
        total_ms,
        total_cpu_ms,
        timings_ms,
        timings_cpu_ms,
        input_points=len(spectrum.wavelengths_nm),
        processed_points=len(wavelengths),
        replaced_nonfinite=replaced_nonfinite,
        settings=settings,
        fit_present=fit is not None,
    )

    return processed, fit


def resolve_fit_window(
    wavelengths: np.ndarray,
    values: np.ndarray,
    settings: ProcessingSettings,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
    return _resolve_fit_window(wavelengths, values, settings)


def build_dense_analysis_curve(
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
    if processed is None or len(processed.wavelengths_nm) == 0:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64), {}

    fit_wavelengths, _, fit_window_meta = _resolve_fit_window(
        np.asarray(processed.wavelengths_nm, dtype=np.float64),
        np.asarray(processed.values, dtype=np.float64),
        settings,
    )
    if len(fit_wavelengths) == 0:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64), fit_window_meta

    low = float(fit_wavelengths[0])
    high = float(fit_wavelengths[-1])
    span = high - low
    if not np.isfinite(span) or span <= 0:
        return fit_wavelengths.copy(), np.asarray(processed.values[: len(fit_wavelengths)], dtype=np.float64), fit_window_meta

    step = float(getattr(settings, "analysis_resolution_nm", 0.001))
    step = float(min(max(step, 0.0001), max(span, 0.0001)))
    num_points = max(int(np.ceil(span / step)) + 1, 3)
    max_points = 20001
    if num_points > max_points:
        num_points = max_points
        step = span / max(num_points - 1, 1)
    dense_wavelengths = np.linspace(low, high, num_points, dtype=np.float64)

    dense_values = np.interp(dense_wavelengths, processed.wavelengths_nm, processed.values)

    return dense_wavelengths, dense_values.astype(np.float64, copy=False), fit_window_meta


def polynomial_peak_from_curve(
    wavelengths: np.ndarray,
    values: np.ndarray,
    order: int,
) -> float | None:
    if len(wavelengths) < 3:
        return None

    cleaned = _sanitize_curve_values(wavelengths, values)
    if cleaned is None:
        return None
    x, y, _ = cleaned

    low = float(x[0])
    high = float(x[-1])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return None

    order = min(max(int(order), 1), len(x) - 1)
    if order < 1:
        return None

    try:
        poly = np.polynomial.Polynomial.fit(x, y, order).convert()
    except (np.linalg.LinAlgError, ValueError):
        return None

    roots = poly.deriv().roots()
    real_roots = roots[np.isreal(roots)].real
    valid = real_roots[(real_roots >= low) & (real_roots <= high)]
    if valid.size > 0:
        y_valid = poly(valid)
        return float(valid[int(np.nanargmax(y_valid))])

    dense_x = np.linspace(low, high, max(int(np.ceil((high - low) / max((high - low) / 2000.0, 1e-6))) + 1, 3), dtype=np.float64)
    dense_y = poly(dense_x)
    if np.count_nonzero(np.isfinite(dense_y)) == 0:
        return None
    return float(dense_x[int(np.nanargmax(dense_y))])


def centroid_from_curve(wavelengths: np.ndarray, values: np.ndarray) -> float | None:
    if len(wavelengths) < 3:
        return None

    cleaned = _sanitize_curve_values(wavelengths, values)
    if cleaned is None:
        return None
    x, y, _ = cleaned
    shifted = y - float(np.nanmin(y))
    weights = np.clip(shifted, 0.0, None)
    total = float(np.nansum(weights))
    if total <= 0:
        return None
    return float(np.nansum(x * weights) / total)


def gaussian_center_from_curve(wavelengths: np.ndarray, values: np.ndarray) -> float | None:
    fit_values, params = _gaussian_fit(wavelengths, values)
    if fit_values is None or params is None:
        return None
    return float(params[1])


def quadratic_peak_from_curve(wavelengths: np.ndarray, values: np.ndarray) -> float | None:
    if len(wavelengths) < 3:
        return None

    cleaned = _sanitize_curve_values(wavelengths, values)
    if cleaned is None:
        return None
    x, y, _ = cleaned
    if len(x) < 3:
        return None

    peak_index = int(np.nanargmax(y))
    if peak_index <= 0 or peak_index >= len(x) - 1:
        return float(x[peak_index])

    x_local = x[peak_index - 1 : peak_index + 2]
    y_local = y[peak_index - 1 : peak_index + 2]
    try:
        poly = np.polynomial.Polynomial.fit(x_local, y_local, 2)
    except (np.linalg.LinAlgError, ValueError):
        return float(x[peak_index])

    roots = poly.deriv().roots()
    real_roots = roots[np.isreal(roots)].real
    valid = real_roots[(real_roots >= float(x_local[0])) & (real_roots <= float(x_local[-1]))]
    if valid.size == 0:
        return float(x[peak_index])
    return float(valid[0])


def fit_processed_spectrum(processed: Spectrum | None, settings: ProcessingSettings) -> Spectrum | None:
    if processed is None:
        return None

    fit_method = settings.fit_method if settings.fit_method != "" else "none"
    if fit_method == "none":
        return None

    fit_wavelengths, fit_values_source, fit_window_meta = _resolve_fit_window(
        np.asarray(processed.wavelengths_nm, dtype=np.float64),
        np.asarray(processed.values, dtype=np.float64),
        settings,
    )
    if len(fit_wavelengths) < 3:
        return None

    if fit_method == "poly":
        effective_order = min(max(int(settings.polynomial_order), 1), max(len(fit_wavelengths) - 1, 1))
        if len(fit_wavelengths) <= effective_order:
            return None
        fit_values, used_order, polynomial_peak_nm = _stable_polynomial_fit(
            fit_wavelengths,
            fit_values_source,
            effective_order,
        )
        if fit_values is None:
            return None
        return Spectrum(
            wavelengths_nm=fit_wavelengths.copy(),
            values=fit_values,
            y_label=processed.y_label,
            acquired_at=processed.acquired_at,
            metadata={
                "fit_method": "poly",
                "requested_polynomial_order": effective_order,
                "polynomial_order": used_order,
                "polynomial_peak_nm": polynomial_peak_nm,
                "mse": _compute_mse(fit_values_source, fit_values),
                "fwhm_nm": _compute_fwhm_nm(fit_wavelengths, fit_values),
                "fit_window_width_nm": settings.fit_window_width_nm,
                "fit_order_reduced": used_order < effective_order,
                **fit_window_meta,
            },
        )

    if fit_method == "gaussian":
        fit_wavelengths, fit_values_source, fit_window_meta = _resolve_gaussian_fit_window(
            np.asarray(processed.wavelengths_nm, dtype=np.float64),
            np.asarray(processed.values, dtype=np.float64),
            settings,
        )
        if len(fit_wavelengths) < 3:
            return None
        fit_values, params = _gaussian_fit(fit_wavelengths, fit_values_source)
        if fit_values is None or params is None:
            return None
        amplitude, center_nm, sigma_nm, offset = params
        return Spectrum(
            wavelengths_nm=fit_wavelengths.copy(),
            values=fit_values,
            y_label=processed.y_label,
            acquired_at=processed.acquired_at,
            metadata={
                "fit_method": "gaussian",
                "mse": _compute_mse(fit_values_source, fit_values),
                "fwhm_nm": _compute_fwhm_nm(fit_wavelengths, fit_values),
                "fit_window_width_nm": settings.fit_window_width_nm,
                "gaussian_amplitude": amplitude,
                "gaussian_center_nm": center_nm,
                "gaussian_sigma_nm": sigma_nm,
                "gaussian_offset": offset,
                **fit_window_meta,
            },
        )

    return None


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    window = max(int(window), 1)
    if window <= 1:
        return values.copy()

    kernel = np.ones(window, dtype=np.float64)
    summed = np.convolve(values, kernel, mode="same")
    counts = np.convolve(np.ones_like(values, dtype=np.float64), kernel, mode="same")
    return summed / counts


def _linear_baseline(values: np.ndarray) -> np.ndarray:
    if len(values) < 2:
        return np.zeros_like(values, dtype=np.float64)
    return np.linspace(float(values[0]), float(values[-1]), len(values), dtype=np.float64)


def _resolve_fit_window(
    wavelengths: np.ndarray,
    values: np.ndarray,
    settings: ProcessingSettings,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
    if len(wavelengths) == 0:
        return wavelengths.copy(), values.copy(), {}

    cleaned = _sanitize_curve_values(wavelengths, values)
    if cleaned is None:
        return wavelengths.copy(), values.copy(), {}
    wavelengths, values, _ = cleaned

    crop_method = getattr(settings, "crop_method", "fixed_width")
    if crop_method == "threshold":
        fraction = float(np.clip(getattr(settings, "crop_fraction", 0.7), 0.05, 0.95))
        cropped = _resolve_threshold_window(wavelengths, values, fraction)
        if cropped is not None:
            fit_wavelengths, fit_values = cropped
            return (
                fit_wavelengths,
                fit_values,
                {
                    "crop_method": "threshold",
                    "crop_fraction": fraction,
                    "fit_window_min_nm": float(fit_wavelengths[0]),
                    "fit_window_max_nm": float(fit_wavelengths[-1]),
                },
            )

    width_nm = float(settings.fit_window_width_nm)
    if width_nm <= 0:
        return wavelengths.copy(), values.copy(), {"crop_method": "full"}

    peak_index = int(np.nanargmax(values))
    center_nm = float(wavelengths[peak_index])
    half_width = float(width_nm) / 2.0
    mask = (wavelengths >= center_nm - half_width) & (wavelengths <= center_nm + half_width)
    if np.count_nonzero(mask) < 3:
        return wavelengths.copy(), values.copy(), {"crop_method": "full"}
    fit_wavelengths = wavelengths[mask].copy()
    fit_values = values[mask].copy()
    return (
        fit_wavelengths,
        fit_values,
        {
            "crop_method": "fixed_width",
            "fit_window_min_nm": float(fit_wavelengths[0]),
            "fit_window_max_nm": float(fit_wavelengths[-1]),
        },
    )


def _resolve_threshold_window(
    wavelengths: np.ndarray,
    values: np.ndarray,
    fraction: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    finite = np.isfinite(wavelengths) & np.isfinite(values)
    if np.count_nonzero(finite) < 3:
        return None
    wavelengths = wavelengths[finite]
    values = values[finite]

    peak_index = int(np.nanargmax(values))
    peak_value = float(values[peak_index])
    baseline = float(np.nanmin(values))
    amplitude = peak_value - baseline
    if not np.isfinite(amplitude) or amplitude <= 0:
        return None

    threshold = baseline + amplitude * fraction
    above = values >= threshold
    if not np.any(above):
        return None

    left = peak_index
    right = peak_index
    while left > 0 and above[left - 1]:
        left -= 1
    while right < len(values) - 1 and above[right + 1]:
        right += 1

    if (right - left + 1) < 3:
        pad = max(1, 3 - (right - left + 1))
        left = max(0, left - pad)
        right = min(len(values) - 1, right + pad)
    if (right - left + 1) < 3:
        return None
    return wavelengths[left : right + 1].copy(), values[left : right + 1].copy()


def _resolve_gaussian_fit_window(
    wavelengths: np.ndarray,
    values: np.ndarray,
    settings: ProcessingSettings,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
    fit_wavelengths, fit_values, fit_window_meta = _resolve_fit_window(wavelengths, values, settings)
    if len(fit_wavelengths) < 3:
        return fit_wavelengths, fit_values, fit_window_meta

    crop_method = getattr(settings, "crop_method", "fixed_width")
    if crop_method == "threshold":
        fraction = float(np.clip(getattr(settings, "crop_fraction", 0.7), 0.05, 0.95))
    else:
        fraction = float(np.clip(max(getattr(settings, "crop_fraction", 0.7), 0.82), 0.05, 0.95))

    narrowed = _resolve_threshold_window(fit_wavelengths, fit_values, fraction)
    if narrowed is None:
        return fit_wavelengths, fit_values, fit_window_meta

    narrowed_wavelengths, narrowed_values = narrowed
    if len(narrowed_wavelengths) < 3:
        return fit_wavelengths, fit_values, fit_window_meta

    return (
        narrowed_wavelengths,
        narrowed_values,
        {
            **fit_window_meta,
            "gaussian_crop_fraction": fraction,
            "gaussian_window_min_nm": float(narrowed_wavelengths[0]),
            "gaussian_window_max_nm": float(narrowed_wavelengths[-1]),
        },
    )


def _stable_polynomial_fit(
    wavelengths: np.ndarray,
    values: np.ndarray,
    requested_order: int,
) -> tuple[np.ndarray | None, int, float | None]:
    if len(wavelengths) < 2:
        return None, 0, None

    cleaned = _sanitize_curve_values(wavelengths, values)
    if cleaned is None:
        return None, 0, None
    wavelengths, values, _ = cleaned

    low = float(wavelengths[0])
    high = float(wavelengths[-1])
    span = high - low
    if not np.isfinite(span) or span <= 0:
        return None, 0, None

    order = min(requested_order, len(wavelengths) - 1)

    while order >= 1:
        try:
            poly = np.polynomial.Polynomial.fit(wavelengths, values, order)
            fitted = poly(wavelengths)
            peak_nm = _polynomial_peak_from_model(poly, low, high)
            return np.asarray(fitted, dtype=np.float64), order, peak_nm
        except np.linalg.LinAlgError:
            order -= 1

    return None, 0, None


def _fit_polynomial_coefficients(
    wavelengths: np.ndarray,
    values: np.ndarray,
    order: int,
) -> list[float] | None:
    if len(wavelengths) < 2 or order < 1:
        return None
    cleaned = _sanitize_curve_values(wavelengths, values)
    if cleaned is None:
        return None
    wavelengths, values, _ = cleaned
    low = float(wavelengths[0])
    high = float(wavelengths[-1])
    span = high - low
    if not np.isfinite(span) or span <= 0:
        return None
    try:
        poly = np.polynomial.Polynomial.fit(wavelengths, values, order)
    except (np.linalg.LinAlgError, ValueError):
        return None
    coeffs = poly.convert().coef[::-1]
    return coeffs.tolist()


def _polynomial_peak_from_model(
    poly: np.polynomial.Polynomial,
    low: float,
    high: float,
) -> float | None:
    try:
        roots = poly.deriv().roots()
    except Exception:
        return None
    real_roots = roots[np.isreal(roots)].real
    valid = real_roots[(real_roots >= low) & (real_roots <= high)]
    if valid.size == 0:
        return None
    values = poly(valid)
    return float(valid[int(np.nanargmax(values))])


def _gaussian_fit(
    wavelengths: np.ndarray,
    values: np.ndarray,
) -> tuple[np.ndarray | None, tuple[float, float, float, float] | None]:
    if len(wavelengths) < 3:
        return None, None

    cleaned = _sanitize_curve_values(wavelengths, values)
    if cleaned is None:
        return None, None
    x, y, _ = cleaned
    low = float(np.min(x))
    high = float(np.max(x))
    span = max(high - low, 1e-6)

    offset = float(np.nanpercentile(y, 5.0))
    shifted = np.clip(y - offset, 1e-12, None)
    weights = shifted / max(float(np.nanmax(shifted)), 1e-12)
    active = weights >= 0.12
    if np.count_nonzero(active) < 3:
        active = np.ones_like(x, dtype=bool)

    x_fit = x[active]
    log_y = np.log(shifted[active])
    if len(x_fit) < 3 or np.count_nonzero(np.isfinite(log_y)) < 3:
        return None, None

    design = np.column_stack((x_fit * x_fit, x_fit, np.ones_like(x_fit)))
    try:
        coeffs, *_ = np.linalg.lstsq(design, log_y, rcond=None)
    except np.linalg.LinAlgError:
        return None, None

    a, b, c = (float(coeffs[0]), float(coeffs[1]), float(coeffs[2]))
    if not np.isfinite(a) or a >= -1e-12:
        return None, None

    center = float(-b / (2.0 * a))
    sigma = float(np.sqrt(max(-1.0 / (2.0 * a), 1e-12)))
    if not np.isfinite(center) or not np.isfinite(sigma):
        return None, None
    if center < low:
        center = low
    elif center > high:
        center = high
    sigma = min(max(sigma, span * 1e-4), span * 10.0)
    amplitude = float(np.exp(c - (b * b) / (4.0 * a)))
    if not np.isfinite(amplitude) or amplitude <= 0.0:
        return None, None

    fitted = offset + amplitude * np.exp(-0.5 * ((x - center) / max(sigma, 1e-6)) ** 2)
    return fitted, (amplitude, center, sigma, offset)


def _savitzky_golay(values: np.ndarray, window: int) -> np.ndarray:
    window = max(int(window), 3)
    if window % 2 == 0:
        window += 1
    if window > len(values):
        window = len(values) if len(values) % 2 == 1 else max(len(values) - 1, 1)
    if window < 3:
        return values.copy()

    poly_order = min(3, window - 1)
    return savgol_filter(values, window_length=window, polyorder=poly_order, mode="interp")


def _compute_mse(y_true: np.ndarray, y_fit: np.ndarray) -> float:
    finite = np.isfinite(y_true) & np.isfinite(y_fit)
    if np.count_nonzero(finite) == 0:
        return float("nan")
    diff = y_true[finite] - y_fit[finite]
    return float(np.mean(diff * diff))


def _compute_fwhm_nm(wavelengths: np.ndarray, values: np.ndarray) -> float | None:
    if len(wavelengths) < 3:
        return None
    cleaned = _sanitize_curve_values(wavelengths, values)
    if cleaned is None:
        return None
    x, y, _ = cleaned
    baseline = float(np.min(y))
    peak_index = int(np.argmax(y))
    peak_value = float(y[peak_index])
    amplitude = peak_value - baseline
    if amplitude <= 0:
        return None
    half_level = baseline + 0.5 * amplitude

    left = peak_index
    while left > 0 and y[left] >= half_level:
        left -= 1
    right = peak_index
    while right < len(y) - 1 and y[right] >= half_level:
        right += 1
    if left == peak_index or right == peak_index:
        return None

    left_x = _interp_crossing(x[left], y[left], x[left + 1], y[left + 1], half_level)
    right_x = _interp_crossing(x[right - 1], y[right - 1], x[right], y[right], half_level)
    if left_x is None or right_x is None:
        return None
    return float(max(right_x - left_x, 0.0))


def _interp_crossing(x0: float, y0: float, x1: float, y1: float, level: float) -> float | None:
    dy = y1 - y0
    if abs(dy) < 1e-12:
        return None
    t = (level - y0) / dy
    return float(x0 + t * (x1 - x0))


def _sanitize_curve_values(
    wavelengths: np.ndarray,
    values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, int] | None:
    x = np.asarray(wavelengths, dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    if len(x) != len(y) or len(x) < 2:
        return None

    finite = np.isfinite(x) & np.isfinite(y)
    finite_count = int(np.count_nonzero(finite))
    if finite_count < 2:
        return None

    x = x[finite]
    y = y[finite]
    if len(x) < 2:
        return None

    replaced = len(wavelengths) - len(x)
    if replaced <= 0:
        return x, y, 0

    restored = np.asarray(values, dtype=np.float64).copy()
    original_x = np.asarray(wavelengths, dtype=np.float64)
    bad = ~np.isfinite(restored)
    if np.count_nonzero(bad) > 0:
        if len(y) == 1:
            restored[bad] = y[0]
        else:
            restored[bad] = np.interp(original_x[bad], x, y, left=y[0], right=y[-1])
    finite = np.isfinite(original_x) & np.isfinite(restored)
    if np.count_nonzero(finite) < 2:
        return None
    return original_x[finite], restored[finite], int(np.count_nonzero(bad))


def _slow_processing_log_threshold_ms() -> float:
    raw_value = os.getenv("LSPR_PROCESSING_SLOW_LOG_MS")
    if raw_value is None:
        return _DEFAULT_SLOW_PROCESSING_LOG_THRESHOLD_MS
    try:
        return max(float(raw_value), 0.0)
    except ValueError:
        return _DEFAULT_SLOW_PROCESSING_LOG_THRESHOLD_MS


def _log_slow_processing_profile(
    total_ms: float,
    total_cpu_ms: float,
    timings_ms: dict[str, float],
    timings_cpu_ms: dict[str, float],
    *,
    input_points: int,
    processed_points: int,
    replaced_nonfinite: int,
    settings: ProcessingSettings,
    fit_present: bool,
) -> None:
    if not _PROCESSING_DEBUG_MODE_ENABLED:
        return
    if total_ms < _slow_processing_log_threshold_ms():
        return

    stages = (
        f"mask={timings_ms.get('mask', 0.0):.2f}/{timings_cpu_ms.get('mask', 0.0):.2f} ms",
        f"slice={timings_ms.get('slice', 0.0):.2f}/{timings_cpu_ms.get('slice', 0.0):.2f} ms",
        f"sanitize={timings_ms.get('sanitize', 0.0):.2f}/{timings_cpu_ms.get('sanitize', 0.0):.2f} ms",
        f"baseline={timings_ms.get('baseline', 0.0):.2f}/{timings_cpu_ms.get('baseline', 0.0):.2f} ms",
        f"smoothing={timings_ms.get('smoothing', 0.0):.2f}/{timings_cpu_ms.get('smoothing', 0.0):.2f} ms",
        f"metadata={timings_ms.get('metadata', 0.0):.2f}/{timings_cpu_ms.get('metadata', 0.0):.2f} ms",
        f"fit={timings_ms.get('fit', 0.0):.2f}/{timings_cpu_ms.get('fit', 0.0):.2f} ms",
    )
    _PROCESSING_LOGGER.info(
        "Slow spectrum processing: total=%.2f/%.2f ms wall/cpu | points=%d->%d | nonfinite=%d | "
        "baseline=%s | smoothing=%s(%d) | fit=%s | %s",
        total_ms,
        total_cpu_ms,
        input_points,
        processed_points,
        replaced_nonfinite,
        settings.baseline_method,
        settings.smoothing_method,
        settings.smoothing_window,
        "on" if fit_present else "off",
        " | ".join(stages),
    )
