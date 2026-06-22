# Device Communication Inventory

This note records the current singleLSPR acquisition communication paths before the central DeviceManager refactor is completed.

## Current ownership boundaries

- `apps/sLSPR/acq/src/lspr_app/device/serial_controllers.py`
  - Generic serial controller base class, port enumeration, and controller registration.
- `apps/sLSPR/acq/src/lspr_app/device/reglo_icc.py`
  - Pump client, probe, command sending, and pump-specific protocol handling.
- `apps/sLSPR/acq/src/lspr_app/device/valve_controllers.py`
  - Valve controller registrations and serial protocol adapters.
- `apps/sLSPR/acq/src/lspr_app/device/amf_mswitch.py`
  - AMF switch discovery and connection handling.
- `apps/sLSPR/acq/src/lspr_app/device/ocean.py`
  - SeaBreeze spectrometer backend and auto-integration logic.
- `apps/sLSPR/acq/src/lspr_app/device/connection_registry.py`
  - In-process port ownership bookkeeping.
- `apps/sLSPR/acq/src/lspr_app/device/port_assignments.py`
  - Manual port-to-role assignment state.
- `apps/sLSPR/acq/src/lspr_app/device/probe_diagnostics.py`
  - USB/COM probe event capture and logging.
- `apps/sLSPR/acq/src/lspr_app/device/hardware_inventory.py`
  - Passive/active serial inventory and recognition heuristics.
- `apps/sLSPR/acq/src/lspr_app/device/device_comm_service.py`
  - Thin legacy wrapper around device discovery and probe helpers.

## GUI entry points

- `apps/sLSPR/acq/src/lspr_app/gui/hardware_initializer.py`
  - Initial hardware scan and startup wiring.
- `apps/sLSPR/acq/src/lspr_app/gui/hardware_inventory_dialog.py`
  - Connected-device inventory UI and manual assignment control.
- `apps/sLSPR/acq/src/lspr_app/gui/usb_probe_diagnostics_dialog.py`
  - Probe log viewer.
- `apps/sLSPR/acq/src/lspr_app/gui/experiment_control_window.py`
  - Experiment Control bootstrapping and device refresh workflow.
- `apps/sLSPR/acq/src/lspr_app/gui/main_window_titlebar.py`
  - Device status strip in the title area.
- `apps/sLSPR/acq/src/lspr_app/gui/main_window.py`
  - High-level window orchestration and dialog launch points.

## Current observation

The app already has the ingredients for a service boundary:

- device drivers are isolated under `device/`
- port ownership exists
- probe logging exists
- inventory UI exists

What is still missing is a single stateful service that owns:

- stable device labels and profiles
- a device registry
- command routing by label
- connected-device status snapshots
- safe console operations

The new `DeviceCommunicationService` and `DeviceConsoleDialog` files are the first step toward that boundary.
