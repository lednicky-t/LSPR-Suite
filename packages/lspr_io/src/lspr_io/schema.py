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
#
# 6.5 (2026-08-24): additive, compatible change - a uniform `/rois/<roi_id>/`
# index, giving both sLSPR acq (exactly one roi_id, "probe") and LSPRi eva
# (one roi_id per sample/reference ROI pair) the same roi-indexed browsing
# shape without moving any existing data. `/rois/<roi_id>/definition` is a
# small real group of descriptive attrs; `/rois/<roi_id>/<name>` entries
# pointing at bulk data (spectra, metrics, sensorgram, absorbance_spectra)
# are h5py soft links to wherever that data actually lives (data/spectra,
# processed/metrics, processed/sensorgram/<roi_id>, etc.) - no duplication,
# no path moved, so every existing reader (including sLSPR eva's heuristic
# visititems-based one) keeps working unchanged; the new group is simply
# ignorable, per hdf_standard.md's "unknown top-level groups may be ignored"
# rule. See `create_roi_index_entry` in hdf5.py.
#
# Also 6.5: LSPR_MEASUREMENT_ROI_DEFINITIONS_COLUMNS gained new columns,
# appended (not inserted) after the original 10 - old positional readers
# (e.g. lspri_acq_app's read_imaging_session, which reads row[0]..row[9])
# keep working unchanged. New columns cover LSPRi eva's richer AreaRoi
# model (domain/models.py) - geometry types beyond circle/annulus, and
# descriptive/array-membership fields LSPRi acq's simpler AreaRoi doesn't
# have yet: sample_geometry_type, reference_geometry_type, label, notes,
# created_by, array_group_id, array_row, array_col. Any new reader must look
# these up by name via the table's `columns` attr, not by hardcoded index.
# 6.6 (2026-08-26): additive, compatible change - two new optional columns on
# processed/sensorgram/{roi_id} and processed/absorbance_spectra/{roi_id}, part
# of LSPRi eva's analysis-result-caching redesign (see apps/LSPRi/eva/docs/
# analysis_pipeline_redesign.md). `cube_index` (sensorgram group only - the
# absorbance group already had it): sensorgram previously stored only
# timestamp_utc_ms, which isn't reliably invertible back to a cube index, so a
# reopened backup couldn't tell which cubes were already recorded without it.
# `signature_hash` (both groups): a sha256 of the same preprocessing/
# chromatic/ROI-geometry/exclusion cache signature already computed in-memory
# for the RAM result caches, letting a reopened session tell whether an
# on-disk row is still valid under the current settings before trusting it as
# a cache hit, instead of only being usable as a write-only backup. Readers
# must tolerate both columns' absence in older files - ImagingMeasurementExportWriter
# never overwrites a row in place when a signature changes; it appends a new
# one, so a superseded row is simply the one whose hash no longer matches.
# 6.7 (2026-08-29): additive, compatible change - new optional `reduced_values/
# <reduction_method>/{sample_mean, reference_mean}` subgroups under
# processed/absorbance_spectra/{roi_id}, part of LSPRi eva's Reduction/Formula
# decoupling (see apps/LSPRi/eva/docs/imaging_measurement_export_format.md).
# Every reduction method (mean/median/trimmed_mean/plane_fit) actually
# computed for a row is written here, not just whichever was active - see
# processing/roi_math.py's reduce_sample_and_reference_all_methods - so any of
# them can be recovered later without re-reading pixels (processing/analysis.py's
# project_reduction_result). The existing flat `sample_mean`/`reference_mean`/
# `absorbance` columns and `formula_key`/`reduction_method` attrs are
# unchanged and keep meaning exactly what they did before - readers that don't
# know about `reduced_values/` still see a fully valid, complete file. New
# group attrs: `reduced_values_start_row` (row index before which no
# `reduced_values/` entry should be trusted - rows before it predate this
# feature and are NaN-backfilled purely to keep column lengths aligned, not
# because they were computed) and, on the parent processed/absorbance_spectra
# group, `reduction_method_definitions`/`formula_key_definitions` (JSON-string
# catalogs of what each method/formula key actually computes, for
# reproducibility without needing this app's source). Also as of this
# version, `signature_hash` on processed/absorbance_spectra rows is computed
# from a reduction-independent signature (a row can now carry every reduction
# method's values, so its validity must not depend on which one was active
# when it was written) - readers must not assume it's directly comparable to
# a pre-6.7 file's hash for the same settings.
LSPR_MEASUREMENT_SCHEMA_NAME = "lspr_measurement"
LSPR_MEASUREMENT_SCHEMA_VERSION = "6.7"
LSPR_MEASUREMENT_SCHEMA_MAJOR = 6
LSPR_MEASUREMENT_SCHEMA_MINOR = 7
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
    # appended in schema 6.5 - see that changelog entry above. Look these up
    # by name (via the table's `columns` attr), not by hardcoded position.
    "sample_geometry_type",
    "reference_geometry_type",
    "label",
    "notes",
    "created_by",
    "array_group_id",
    "array_row",
    "array_col",
]
LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME = "absorbance_spectra"
LSPR_PROCESSED_SENSORGRAM_GROUP_NAME = "sensorgram"
LSPR_PROCESSED_SENSORGRAM_COLUMNS = ["timestamp_utc_ms", "metric_value"]

# schema 6.5 - uniform /rois/<roi_id>/ index (see changelog entry above).
LSPR_ROIS_INDEX_GROUP_NAME = "rois"
LSPR_ROI_DEFINITION_GROUP_NAME = "definition"
LSPR_SINGLE_CHANNEL_ROI_ID = "probe"
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
