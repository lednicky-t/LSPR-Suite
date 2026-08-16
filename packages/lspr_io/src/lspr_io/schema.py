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
#
# 6.3: additive, compatible change - two new, previously-drafted-but-unwritten
# tables. devices/inventory: one row per known device (label, type, role,
# driver, endpoint, display_name, model, serial_number, connected), snapshotted
# once when a measurement starts. metadata/assignment_tables/
# switch_solution_details: optional concentration/concentration_unit/notes per
# M-switch port, keyed by switch_port to join against the pre-existing
# switch_solution_map table - a deliberately minimal alternative to the fuller
# /metadata/solutions registry still described (but not implemented) in
# measurement_file_format.md. Readers must tolerate both tables' absence in
# older files; neither existing table/column is touched.
#
# 6.4 (2026-08-09): additive, compatible change - new groups for LSPRimaging
# Acquisition's session-recording (see docs/architecture/general/
# lspri_acq_build_log.md's 2026-08-09 entry for the full design discussion).
# metadata/illumination_settings: one row per swept wavelength (wavelength_nm,
# settle_time_ms, current, spectrum_source), joined to
# metadata/illumination_spectra/{wavelength_nm} for the actual spectrum array.
# metadata/camera_settings: one row per swept wavelength (exposure_us, gain,
# binning, resolution, crop, saving_mode), joined to illumination_settings by
# wavelength_nm - same join convention 6.3's switch_solution_details already
# uses against switch_solution_map. metadata/image_cube_manifest: cube_index /
# timestamp_utc_ms / file_path rows pointing at the separate TIFF/OME-Zarr
# files lspri_acq_app's image_writer.py writes - pixel data itself stays out
# of HDF5. metadata.attrs["has_recorded_data"]: distinguishes a pure setup/
# session snapshot (no raw rows yet, still editable) from a file that has
# actually recorded data. Also new: metadata/roi_definitions (AreaRoi/
# AreaRoiGroup snapshot) and processed/absorbance_spectra/{roi_id},
# processed/sensorgram/{roi_id} (per-ROI extinction spectra and sensorgram
# points), matching the shapes already sketched in the plan doc's §9 before
# this bump. None of this is specific to a single-spectrometer measurement;
# readers must tolerate all of it being absent, which every non-imaging file
# (including every sLSPR acq file) will continue to be.
LSPR_MEASUREMENT_SCHEMA_NAME = "lspr_measurement"
LSPR_MEASUREMENT_SCHEMA_VERSION = "6.4"
LSPR_MEASUREMENT_SCHEMA_MAJOR = 6
LSPR_MEASUREMENT_SCHEMA_MINOR = 4
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
LSPR_MEASUREMENT_SWITCH_SOLUTION_DETAILS_DATASET_NAME = "switch_solution_details"
LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME = "valve_state_map"
LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME = "color_palette_entries"
LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_DATASET_NAME = "illumination_settings"
LSPR_MEASUREMENT_ILLUMINATION_SPECTRA_GROUP_NAME = "illumination_spectra"
LSPR_MEASUREMENT_CAMERA_SETTINGS_DATASET_NAME = "camera_settings"
LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_DATASET_NAME = "image_cube_manifest"
LSPR_MEASUREMENT_HAS_RECORDED_DATA_ATTR = "has_recorded_data"
LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_COLUMNS = [
    "wavelength_nm",
    "settle_time_ms",
    "current",
    "spectrum_source",
]
LSPR_MEASUREMENT_CAMERA_SETTINGS_COLUMNS = [
    "wavelength_nm",
    "exposure_us",
    "gain",
    "binning",
    "resolution_width_px",
    "resolution_height_px",
    "crop_x_px",
    "crop_y_px",
    "crop_width_px",
    "crop_height_px",
    "saving_mode",
]
LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_COLUMNS = [
    "cube_index",
    "timestamp_utc_ms",
    "file_path",
]
LSPR_MEASUREMENT_ROI_DEFINITIONS_DATASET_NAME = "roi_definitions"
LSPR_MEASUREMENT_ROI_DEFINITIONS_COLUMNS = [
    "area_roi_id",
    "group_id",
    "center_x",
    "center_y",
    "sample_radius_px",
    "sample_diameter_px",
    "reference_inner_diameter_px",
    "reference_outer_diameter_px",
    "sample_color_hex",
    "reference_color_hex",
]
LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME = "absorbance_spectra"
LSPR_PROCESSED_SENSORGRAM_GROUP_NAME = "sensorgram"
LSPR_PROCESSED_SENSORGRAM_COLUMNS = ["timestamp_utc_ms", "metric_value"]
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

LSPR_DEVICE_INVENTORY_GROUP_NAME = "inventory"
LSPR_DEVICE_INVENTORY_SCHEMA_NAME = "lspr_device_inventory"
LSPR_DEVICE_INVENTORY_SCHEMA_VERSION = "1.0"
LSPR_DEVICE_INVENTORY_FORMAT_NAME = "device_inventory"
LSPR_DEVICE_INVENTORY_FORMAT_VERSION = 1
LSPR_DEVICE_INVENTORY_TABLE_DATASET_NAME = "devices"
LSPR_DEVICE_INVENTORY_COLUMNS = [
    "label",
    "type",
    "role",
    "driver",
    "endpoint",
    "display_name",
    "model",
    "serial_number",
    "connected",
]

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
