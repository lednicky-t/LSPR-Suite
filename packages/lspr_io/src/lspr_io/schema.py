from __future__ import annotations

# 6.0 (2026-07-21): breaking change - removed the relative "t_ms" column from
# both the raw-spectra groups (data/spectra/{sample,dark,reference}) and the
# processed/metrics group. "acquired_at_unix_ms" (absolute Unix epoch
# milliseconds, already present alongside t_ms since 3.0) is now the sole
# per-row timestamp; readers compute any relative/elapsed display value from
# it at read time instead of trusting a value baked in at write time. This
# was a real, shipped bug source (see apps/sLSPR/acq/docs/sensorgram_
# improvements.md, "Correctness fixes" C1/C2) - a relative anchor that could
# be silently reset mid-file made the derived elapsed time non-monotonic.
#
# 6.1 (2026-07-28): additive, compatible change - added an optional "user"
# attr to root/manifest measurement metadata: who was using the instrument
# (picked from the app's own User field), not to be confused with the
# pre-existing "export_user" (OS login name, captured automatically).
# Readers must tolerate its absence in older files.
#
# 6.2 (2026-07-29): additive, compatible change - added "extinction_value" to
# processed/metrics, the Y-value of whichever fit metric is the primary
# tracked one (smoothed_max/poly_max/gaussian_center/centroid) at the moment
# it was recorded - see get_analysis_metrics in apps/sLSPR/acq's
# gui/processing_helpers.py. Readers must tolerate its absence in older files.
LSPR_MEASUREMENT_SCHEMA_NAME = "lspr_measurement"
LSPR_MEASUREMENT_SCHEMA_VERSION = "6.2"
LSPR_MEASUREMENT_SCHEMA_MAJOR = 6
LSPR_MEASUREMENT_SCHEMA_MINOR = 2
LSPR_MEASUREMENT_FORMAT_NAME = "experiment_run"
LSPR_MEASUREMENT_FORMAT_VERSION = 6

LSPR_SESSION_SCHEMA_NAME = "lspr_session"
LSPR_SESSION_SCHEMA_VERSION = 1
LSPR_SESSION_FORMAT_NAME = "LSPR Session"
LSPR_SESSION_FORMAT_VERSION = 1

LSPR_EXPERIMENT_PLAN_SCHEMA_NAME = "lspr_experiment_plan"
LSPR_EXPERIMENT_PLAN_SCHEMA_VERSION = 1
LSPR_EXPERIMENT_PLAN_FORMAT_NAME = "LSPR Experiment Plan"
LSPR_EXPERIMENT_PLAN_FORMAT_VERSION = 1
LSPR_EXPERIMENT_PLAN_DATASET_NAME = "experiment_plan"

LSPR_FLOW_PLAN_SCHEMA_NAME = LSPR_EXPERIMENT_PLAN_SCHEMA_NAME
LSPR_FLOW_PLAN_SCHEMA_VERSION = LSPR_EXPERIMENT_PLAN_SCHEMA_VERSION
LSPR_FLOW_PLAN_FORMAT_NAME = "LSPR Flow Plan"
LSPR_FLOW_PLAN_FORMAT_VERSION = LSPR_EXPERIMENT_PLAN_FORMAT_VERSION
LSPR_FLOW_PLAN_DATASET_NAME = LSPR_EXPERIMENT_PLAN_DATASET_NAME

LSPR_MEASUREMENT_SPECTRUM_COLUMNS = ["wavelength_nm"]
LSPR_MEASUREMENT_TIME_COLUMNS = ["time_s"]
LSPR_MEASUREMENT_PEAK_COLUMNS = ["peak_nm"]
LSPR_MEASUREMENT_WAVELENGTHS_DATASET_NAME = "wavelengths_nm"
LSPR_MEASUREMENT_RUNTIME_DATASET_NAME = "experiment_control_runtime"
LSPR_MEASUREMENT_RUNTIME_TIMESTAMP_UTC_COLUMN = "timestamp_utc_ms"
LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME = "assignment_tables"
LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME = "switch_solution_map"
LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME = "valve_state_map"
LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME = "color_palette_entries"
LSPR_PROCESSED_METRICS_GROUP_NAME = "metrics"
LSPR_PROCESSED_METRICS_CONFIG_GROUP_NAME = "config"
LSPR_PROCESSED_METRICS_SCHEMA_NAME = "lspr_processed_metrics"
LSPR_PROCESSED_METRICS_SCHEMA_VERSION = "1.0"
LSPR_PROCESSED_METRICS_FORMAT_NAME = "processed_metrics"
LSPR_PROCESSED_METRICS_FORMAT_VERSION = 1
LSPR_PROCESSING_SETTINGS_SCHEMA_NAME = "lspr_processing_settings"
LSPR_PROCESSING_SETTINGS_SCHEMA_VERSION = "1.0"
LSPR_MEASUREMENT_RUNTIME_COLUMNS = [
    LSPR_MEASUREMENT_RUNTIME_TIMESTAMP_UTC_COLUMN,
    "t_ms",
    "event",
    "step_index",
    "elapsed_in_step_ms",
    "pump_running",
    "valve_position",
    "switch_position",
    "pump_connected",
    "valve_connected",
    "switch_connected",
    "status",
    "ch1_flow_ul_min",
    "ch1_direction",
    "ch1_tube_mm",
    "ch2_flow_ul_min",
    "ch2_direction",
    "ch2_tube_mm",
    "ch3_flow_ul_min",
    "ch3_direction",
    "ch3_tube_mm",
    "ch4_flow_ul_min",
    "ch4_direction",
    "ch4_tube_mm",
    "ch5_flow_ul_min",
    "ch5_direction",
    "ch5_tube_mm",
    "ch6_flow_ul_min",
    "ch6_direction",
    "ch6_tube_mm",
]

LSPR_MEASUREMENT_FLOW_EVENTS_DATASET_NAME = "flow_events"
LSPR_MEASUREMENT_FLOW_STATE_DATASET_NAME = "flow_state"
LSPR_MEASUREMENT_FLOW_STATE_COLUMNS = LSPR_MEASUREMENT_RUNTIME_COLUMNS
LSPR_PROCESSED_METRICS_ACQUIRED_AT_UNIX_MS_DATASET_NAME = "acquired_at_unix_ms"

LSPR_DEVICE_ENVIRONMENT_GROUP_NAME = "environment"
LSPR_DEVICE_ENVIRONMENT_TIMESTAMP_UTC_MS_DATASET_NAME = "timestamp_utc_ms"
LSPR_DEVICE_ENVIRONMENT_TEMPERATURE_C_DATASET_NAME = "temperature_c"
LSPR_DEVICE_ENVIRONMENT_HUMIDITY_PERCENT_DATASET_NAME = "humidity_percent"

LSPR_MEASUREMENT_PLAN_COLUMNS = [
    "step",
    "duration_s",
    "start_s",
    "end_s",
    "color",
    "valve",
    "switch_position",
    "description",
    "ch1_flow_ul_min",
    "ch1_direction",
    "ch1_tube_mm",
    "ch2_flow_ul_min",
    "ch2_direction",
    "ch2_tube_mm",
    "ch3_flow_ul_min",
    "ch3_direction",
    "ch3_tube_mm",
    "ch4_flow_ul_min",
    "ch4_direction",
    "ch4_tube_mm",
    "ch5_flow_ul_min",
    "ch5_direction",
    "ch5_tube_mm",
    "ch6_flow_ul_min",
    "ch6_direction",
    "ch6_tube_mm",
]
