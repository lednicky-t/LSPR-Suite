from __future__ import annotations

LSPR_MEASUREMENT_SCHEMA_NAME = "lspr_measurement"
LSPR_MEASUREMENT_SCHEMA_VERSION = "3.0"
LSPR_MEASUREMENT_SCHEMA_MAJOR = 3
LSPR_MEASUREMENT_SCHEMA_MINOR = 0
LSPR_MEASUREMENT_FORMAT_NAME = "experiment_run"
LSPR_MEASUREMENT_FORMAT_VERSION = 3

LSPR_SESSION_SCHEMA_NAME = "lspr_session"
LSPR_SESSION_SCHEMA_VERSION = 1
LSPR_SESSION_FORMAT_NAME = "LSPR Session"
LSPR_SESSION_FORMAT_VERSION = 1

LSPR_EXPERIMENT_PLAN_SCHEMA_NAME = "lspr_experiment_plan"
LSPR_EXPERIMENT_PLAN_SCHEMA_VERSION = 1
LSPR_EXPERIMENT_PLAN_FORMAT_NAME = "LSPR Experiment Plan"
LSPR_EXPERIMENT_PLAN_FORMAT_VERSION = 1
LSPR_EXPERIMENT_PLAN_DATASET_NAME = "experiment_plan"
LSPR_EXPERIMENT_PLAN_TMP_DATASET_NAME = "experiment_plan_tmp"

LSPR_FLOW_PLAN_SCHEMA_NAME = LSPR_EXPERIMENT_PLAN_SCHEMA_NAME
LSPR_FLOW_PLAN_SCHEMA_VERSION = LSPR_EXPERIMENT_PLAN_SCHEMA_VERSION
LSPR_FLOW_PLAN_FORMAT_NAME = "LSPR Flow Plan"
LSPR_FLOW_PLAN_FORMAT_VERSION = LSPR_EXPERIMENT_PLAN_FORMAT_VERSION
LSPR_FLOW_PLAN_DATASET_NAME = LSPR_EXPERIMENT_PLAN_DATASET_NAME
LSPR_FLOW_PLAN_TMP_DATASET_NAME = LSPR_EXPERIMENT_PLAN_TMP_DATASET_NAME

LSPR_MEASUREMENT_SPECTRUM_COLUMNS = ["wavelength_nm"]
LSPR_MEASUREMENT_TIME_COLUMNS = ["time_s"]
LSPR_MEASUREMENT_PEAK_COLUMNS = ["peak_nm"]

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

LSPR_MEASUREMENT_FLOW_STATE_COLUMNS = [
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
