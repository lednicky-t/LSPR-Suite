"""Canonical device type identifiers.

These string constants are the single source of truth for device type names used
across the GUI and the device service layer.

Extracted from singleLSPR Acquisition's `device/device_types.py` (Phase 1,
2026-08-08), part of the fluidics device framework verbatim move (§4.2).

To rename a device category in the future:
  1. Change the constant value here.
  2. Add the old value as a recognized alias in ``_normalize_device_type()`` in
     ``device_manager.py``.
  3. Add a migration entry in ``_LEGACY_LABEL_MIGRATIONS`` in ``device_manager.py``
     so that profiles saved under the old label are transparently upgraded on load.
  4. Update display strings in each app's own device-status UI.
"""

from __future__ import annotations

PUMP = "pump"
SWITCH = "switch"       # injection valve / flow switch (formerly "valve")
# AMF switch rotary valve (user-facing label: "Switch rotary valve"). Internal
# key/class/file names still say "mswitch"/"selector" - not renamed yet.
# TODO: this assumes a distribution/selector-head valve (one common port ->
# N individually addressable positions). AMF also sells a switch-head variant
# (N ports wired in pairs -> N/2 logical positions) which this code does not
# yet distinguish or support. See the "Hardware Topology" section in
# apps/sLSPR/acq/docs/experiment-control/mswitch_control_guide.md before
# trusting raw switch_position numbers on a switch-head unit.
SELECTOR = "selector"
