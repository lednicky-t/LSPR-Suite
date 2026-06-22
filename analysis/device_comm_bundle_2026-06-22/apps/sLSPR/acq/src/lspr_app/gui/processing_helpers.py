from __future__ import annotations

from typing import Iterable

import numpy as np

from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app.domain.processing import (
    build_dense_analysis_curve,
    centroid_from_curve,
    gaussian_center_from_curve,
    polynomial_peak_from_curve,
    process_spectrum,
    quadratic_peak_from_curve,
    resolve_fit_window,
)


def processing_cache_token(spectrum: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
    if spectrum is None:
        return None
    return (
        id(spectrum),
        spectrum.acquired_at,
        settings.wavelength_min_nm,
        settings.wavelength_max_nm,
        settings.baseline_method,
        settings.smoothing_method,
        settings.smoothing_window,
        settings.temporal_smoothing,
        settings.crop_method,
        settings.crop_fraction,
        settings.fit_method,
        settings.polynomial_order,
        settings.fit_window_width_nm,
        settings.spectrum_tracking_mode,
    )


def analysis_cache_token(
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[object, ...] | None:
    if processed is None:
        return None
    return (
        id(processed),
        processed.acquired_at,
        len(processed.wavelengths_nm),
        float(processed.wavelengths_nm[0]) if len(processed.wavelengths_nm) else None,
        float(processed.wavelengths_nm[-1]) if len(processed.wavelengths_nm) else None,
        id(fit) if fit is not None else None,
        fit.acquired_at if fit is not None else None,
        fit.metadata.get("fit_method") if fit is not None else None,
        settings.crop_method,
        settings.crop_fraction,
        settings.fit_method,
        settings.fit_window_width_nm,
        settings.analysis_resolution_nm,
        settings.polynomial_order,
    )


def analysis_metrics_cache_token(
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[object, ...] | None:
    if processed is None:
        return None
    return (
        id(processed),
        processed.acquired_at,
        len(processed.wavelengths_nm),
        float(processed.wavelengths_nm[0]) if len(processed.wavelengths_nm) else None,
        float(processed.wavelengths_nm[-1]) if len(processed.wavelengths_nm) else None,
        id(fit) if fit is not None else None,
        fit.acquired_at if fit is not None else None,
        fit.metadata.get("fit_method") if fit is not None else None,
        settings.wavelength_min_nm,
        settings.wavelength_max_nm,
        settings.baseline_method,
        settings.smoothing_method,
        settings.smoothing_window,
        settings.temporal_smoothing,
        settings.crop_method,
        settings.crop_fraction,
        settings.fit_method,
        settings.polynomial_order,
        settings.fit_window_width_nm,
        settings.analysis_resolution_nm,
        settings.spectrum_tracking_mode,
    )


def needs_gaussian_metric(settings: ProcessingSettings) -> bool:
    return settings.spectrum_tracking_mode == "gaussian_center" or "gaussian_center" in settings.trace_metrics


def get_processed_spectrum(
    spectrum: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[Spectrum | None, Spectrum | None]:
    return process_spectrum(spectrum, settings)


def get_dense_analysis_curve(
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
    return build_dense_analysis_curve(processed, fit, settings)


def _safe_nanargmax_index(values: np.ndarray) -> int | None:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return None
    finite = np.isfinite(array)
    if not np.any(finite):
        return None
    return int(np.nanargmax(array))


def compute_metric_nm(mode: str, processed: Spectrum, fit: Spectrum | None, settings: ProcessingSettings) -> float:
    analysis = get_analysis_metrics(processed, fit, settings)
    value = analysis.get(mode)
    if isinstance(value, (int, float)) and np.isfinite(float(value)):
        return float(value)
    dense_max = analysis.get("dense_max_nm")
    if isinstance(dense_max, (int, float)) and np.isfinite(float(dense_max)):
        return float(dense_max)
    peak_index = _safe_nanargmax_index(processed.values)
    if peak_index is not None and peak_index < len(processed.wavelengths_nm):
        return float(processed.wavelengths_nm[peak_index])
    return float("nan")


def compute_peak_metric_nm(processed: Spectrum, fit: Spectrum | None, settings: ProcessingSettings) -> float:
    metrics = get_analysis_metrics(processed, fit, settings)
    peak = metrics.get("primary_peak_nm")
    if isinstance(peak, (int, float)) and np.isfinite(float(peak)):
        return float(peak)
    return compute_metric_nm(settings.spectrum_tracking_mode, processed, fit, settings)


def compute_trace_metrics(
    processed: Spectrum,
    fit: Spectrum | None,
    settings: ProcessingSettings,
    selected_metrics: Iterable[str],
) -> dict[str, float]:
    analysis = get_analysis_metrics(processed, fit, settings)
    metrics: dict[str, float] = {}
    for metric_name in selected_metrics:
        value = analysis.get(metric_name)
        if isinstance(value, (int, float)) and np.isfinite(float(value)):
            metrics[metric_name] = float(value)
        else:
            metrics[metric_name] = compute_metric_nm(metric_name, processed, fit, settings)
    return metrics


def compute_centroid_nm(processed: Spectrum, fit: Spectrum | None, settings: ProcessingSettings) -> float:
    dense_wavelengths, dense_values, _ = get_dense_analysis_curve(processed, fit, settings)
    centroid = centroid_from_curve(dense_wavelengths, dense_values)
    if centroid is not None:
        return centroid
    peak_index = _safe_nanargmax_index(processed.values)
    if peak_index is not None and peak_index < len(processed.wavelengths_nm):
        return float(processed.wavelengths_nm[peak_index])
    return float("nan")


def compute_fit_r(processed: Spectrum, fit: Spectrum | None) -> str | None:
    if fit is None:
        return None
    if processed.wavelengths_nm.shape == fit.wavelengths_nm.shape and np.allclose(processed.wavelengths_nm, fit.wavelengths_nm):
        y_true = np.asarray(processed.values, dtype=np.float64)
        y_fit = np.asarray(fit.values, dtype=np.float64)
    else:
        y_true = np.interp(fit.wavelengths_nm, processed.wavelengths_nm, processed.values)
        y_fit = np.asarray(fit.values, dtype=np.float64)
    finite = np.isfinite(y_true) & np.isfinite(y_fit)
    if np.count_nonzero(finite) < 3:
        return None
    y_true = y_true[finite]
    y_fit = y_fit[finite]
    ss_res = float(np.sum((y_true - y_fit) ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    if ss_tot <= 0:
        return None
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    return f"{np.sqrt(r2):.4f}"


def compute_fit_mse(processed: Spectrum, fit: Spectrum | None) -> str:
    if fit is None:
        return "-"
    mse = fit.metadata.get("mse")
    if isinstance(mse, (int, float)) and np.isfinite(float(mse)):
        return f"{float(mse):.4g}"
    if processed.wavelengths_nm.shape == fit.wavelengths_nm.shape and np.allclose(processed.wavelengths_nm, fit.wavelengths_nm):
        y_true = np.asarray(processed.values, dtype=np.float64)
    else:
        y_true = np.interp(fit.wavelengths_nm, processed.wavelengths_nm, processed.values)
    y_fit = np.asarray(fit.values, dtype=np.float64)
    finite = np.isfinite(y_true) & np.isfinite(y_fit)
    if np.count_nonzero(finite) < 3:
        return "-"
    diff = y_true[finite] - y_fit[finite]
    return f"{float(np.mean(diff * diff)):.4g}"


def compute_fwhm_text(processed: Spectrum, fit: Spectrum | None) -> str:
    if fit is not None:
        fwhm = fit.metadata.get("fwhm_nm")
        if isinstance(fwhm, (int, float)) and np.isfinite(float(fwhm)):
            return f"{float(fwhm):.3f} nm"
    fwhm = processed.metadata.get("fwhm_nm")
    if isinstance(fwhm, (int, float)) and np.isfinite(float(fwhm)):
        return f"{float(fwhm):.3f} nm"
    return "-"


def estimate_signal_to_noise(processed: Spectrum, fit: Spectrum | None) -> str | None:
    values = np.asarray(processed.values, dtype=np.float64)
    finite = np.isfinite(values)
    if np.count_nonzero(finite) < 5:
        return None
    values = values[finite]
    signal = _estimate_signal_level(processed, fit)
    if signal is None or not np.isfinite(signal) or signal <= 0:
        return None
    if fit is not None:
        fit_values = np.asarray(fit.values, dtype=np.float64)
        fit_finite = np.isfinite(fit_values)
        if np.count_nonzero(fit_finite) < 3:
            return None
        fit_interp = np.interp(processed.wavelengths_nm[finite], fit.wavelengths_nm, fit.values)
        residual = values - fit_interp
        noise_sigma = float(np.nanstd(residual))
    else:
        noise_sigma = float(np.nanstd(np.diff(values))) / np.sqrt(2.0)
    if noise_sigma <= 0:
        return None
    return f"{signal / noise_sigma:.1f}"


def _estimate_signal_level(processed: Spectrum, fit: Spectrum | None) -> float | None:
    if fit is not None:
        fit_method = str(fit.metadata.get("fit_method", "")).strip().lower()
        if fit_method == "gaussian":
            amplitude = fit.metadata.get("gaussian_amplitude")
            if isinstance(amplitude, (int, float)) and np.isfinite(float(amplitude)):
                return float(abs(amplitude))
        fit_values = np.asarray(fit.values, dtype=np.float64)
        fit_finite = np.isfinite(fit_values)
        if np.count_nonzero(fit_finite) >= 3:
            fit_values = fit_values[fit_finite]
            signal = float(np.nanmax(fit_values) - np.nanmin(fit_values))
            if np.isfinite(signal) and signal > 0:
                return signal

    values = np.asarray(processed.values, dtype=np.float64)
    finite = np.isfinite(values)
    if np.count_nonzero(finite) < 5:
        return None
    values = values[finite]
    edge_count = max(int(round(len(values) * 0.1)), 1)
    edges = np.concatenate([values[:edge_count], values[-edge_count:]])
    baseline = float(np.nanmedian(edges)) if len(edges) > 0 else float(np.nanmin(values))
    signal = float(np.nanmax(values) - baseline)
    if not np.isfinite(signal) or signal <= 0:
        signal = float(np.nanmax(values) - np.nanmin(values))
    return signal if np.isfinite(signal) and signal > 0 else None


def get_analysis_metrics(
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> dict[str, object]:
    if processed is None:
        return {
            "dense_wavelengths": np.empty(0, dtype=np.float64),
            "dense_values": np.empty(0, dtype=np.float64),
            "dense_max_nm": float("nan"),
            "centroid_nm": float("nan"),
            "gaussian_center_nm": float("nan"),
            "poly_peak_nm": float("nan"),
            "primary_peak_nm": float("nan"),
            "smoothed_max": float("nan"),
            "centroid": float("nan"),
            "gaussian_center": float("nan"),
            "poly_max": float("nan"),
            "fit_r": None,
            "mse": "-",
            "snr": None,
            "fwhm": "-",
        }

    dense_wavelengths, dense_values, _ = get_dense_analysis_curve(processed, fit, settings)
    dense_max_nm = float("nan")
    if len(dense_wavelengths) >= 3 and len(dense_values) >= 3:
        dense_peak = quadratic_peak_from_curve(dense_wavelengths, dense_values)
        if dense_peak is not None:
            dense_max_nm = float(dense_peak)
        else:
            dense_peak_index = _safe_nanargmax_index(dense_values)
            if dense_peak_index is not None and dense_peak_index < len(dense_wavelengths):
                dense_max_nm = float(dense_wavelengths[dense_peak_index])
    elif processed is not None and len(processed.wavelengths_nm) > 0:
        processed_peak_index = _safe_nanargmax_index(processed.values)
        if processed_peak_index is not None and processed_peak_index < len(processed.wavelengths_nm):
            dense_max_nm = float(processed.wavelengths_nm[processed_peak_index])

    centroid_nm = centroid_from_curve(dense_wavelengths, dense_values)
    if centroid_nm is None:
        centroid_nm = dense_max_nm

    gaussian_center_nm = float("nan")
    if fit is not None and fit.metadata.get("fit_method") == "gaussian":
        center = fit.metadata.get("gaussian_center_nm")
        if isinstance(center, (int, float)) and np.isfinite(float(center)):
            gaussian_center_nm = float(center)
    elif needs_gaussian_metric(settings):
        gaussian_center = gaussian_center_from_curve(dense_wavelengths, dense_values)
        if gaussian_center is not None:
            gaussian_center_nm = gaussian_center

    if fit is not None and processed is not None:
        peak_nm = fit.metadata.get("polynomial_peak_nm")
        if isinstance(peak_nm, (int, float)) and np.isfinite(float(peak_nm)):
            poly_peak_nm = float(peak_nm)
        else:
            poly_wavelengths, poly_values, _ = resolve_fit_window(
                np.asarray(processed.wavelengths_nm, dtype=np.float64),
                np.asarray(processed.values, dtype=np.float64),
                settings,
            )
            peak = polynomial_peak_from_curve(poly_wavelengths, poly_values, settings.polynomial_order)
            poly_peak_nm = float(peak) if peak is not None else dense_max_nm
    else:
        poly_peak_nm = dense_max_nm

    metric_values = {
        "smoothed_max": dense_max_nm,
        "centroid": centroid_nm,
        "gaussian_center": gaussian_center_nm,
        "poly_max": poly_peak_nm,
    }
    primary_mode = settings.spectrum_tracking_mode
    primary_peak_nm = metric_values.get(primary_mode, dense_max_nm)
    if not np.isfinite(float(primary_peak_nm)):
        primary_peak_nm = dense_max_nm

    fit_r = compute_fit_r(processed, fit)
    mse = compute_fit_mse(processed, fit)
    snr = estimate_signal_to_noise(processed, fit)
    fwhm = compute_fwhm_text(processed, fit)

    return {
        "dense_wavelengths": dense_wavelengths,
        "dense_values": dense_values,
        "dense_max_nm": dense_max_nm,
        "centroid_nm": centroid_nm,
        "gaussian_center_nm": gaussian_center_nm,
        "poly_peak_nm": poly_peak_nm,
        "primary_peak_nm": primary_peak_nm,
        **metric_values,
        "fit_r": fit_r,
        "mse": mse,
        "snr": snr,
        "fwhm": fwhm,
    }
