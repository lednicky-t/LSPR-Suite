# Device Communication Analysis Bundle

Bundle date: 2026-06-22  
Source root: LSPR-Suite

## Scope
This bundle captures the acquisition app layers that participate in device discovery, connection setup, initialization, runtime communication, device status reporting, and protocol adapters.

## Included layers
- apps/sLSPR/acq/src/lspr_app/device: backend device adapters, probes, registries, serial controllers, Ocean spectrometer backend, simulated backend, valve controllers, and diagnostics.
- apps/sLSPR/acq/src/lspr_app/gui: acquisition controller, hardware initializer, runtime diagnostics/probe, device status/titlebar wiring, worker threads, main window lifecycle, plotting/status integration, experiment-control UI, and startup handling.
- apps/sLSPR/acq/src/lspr_app/domain: shared data models used by the acquisition pipeline.
- apps/sLSPR/acq/src/lspr_app/storage: HDF5/session/output helpers used by acquisition and measurement persistence.
- packages/lspr_core/src/lspr_core: shared launch/profile/model definitions.
- packages/lspr_io/src/lspr_io: shared HDF5/schema helpers.
- packages/lspr_ui/src/lspr_ui: shared application UI helpers and assets.
- package metadata files (pyproject.toml, README.md) for the included packages.

## Why these files
The device stack is split across backend adapters and GUI orchestration. The backend directory defines how devices are discovered, opened, probed, and driven. The GUI layer coordinates startup, initialization, status indicators, background workers, and experiment-control interactions. The shared packages define the models and IO abstractions that the acquisition app depends on during communication and persistence.

## Likely call chain to inspect
- apps/sLSPR/acq/src/main.py
- lspr_app/app.py
- lspr_app/gui/main_window.py
- lspr_app/gui/hardware_initializer.py
- lspr_app/device/device_comm_service.py
- lspr_app/device/connection_registry.py
- lspr_app/device/serial_controllers.py
- lspr_app/device/reglo_icc.py
- lspr_app/device/ocean.py
- lspr_app/device/amf_mswitch.py
- lspr_app/device/arduino_valve.py
- lspr_app/device/valve_controllers.py
- lspr_app/gui/runtime_diagnostics.py
- lspr_app/gui/runtime_probe.py
- lspr_app/gui/workers.py
- lspr_app/gui/main_window_titlebar.py
- lspr_app/gui/main_window_logging.py
- lspr_app/gui/main_window_runtime.py
- lspr_app/gui/main_window_panels.py
- lspr_app/gui/acquisition_controller.py

## Notes
- Generated source cache directories were excluded.
- The bundle is intended for structural review, dependency tracing, and communication-path analysis.
