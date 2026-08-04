"""Unit tests for device_inventory_rows() (device/communication_models.py),
the pure DeviceStatus -> plain-string-row flattener consumed by
HDF5MeasurementWriter.write_device_inventory(). Column order must match
lspr_io.LSPR_DEVICE_INVENTORY_COLUMNS - see tests/integration/test_acq_hdf5.py
for the end-to-end HDF5 write path.
"""
from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.communication_models import DeviceStatus, device_inventory_rows


class DeviceInventoryRowsTests(unittest.TestCase):
    def test_full_status_is_flattened_in_column_order(self) -> None:
        status = DeviceStatus(
            uuid="abc",
            label="spectrometer_1",
            type="spectrometer",
            driver="OceanSpectrometer",
            endpoint="USB0",
            connected=True,
            state="connected",
            identity={"model": "HR4000", "serial_number": "SN123", "protocol_version": "2"},
            display_name="Ocean HR4000",
            role="primary",
        )
        rows = device_inventory_rows([status])
        self.assertEqual(
            rows,
            [["spectrometer_1", "spectrometer", "primary", "OceanSpectrometer", "USB0", "Ocean HR4000", "HR4000", "SN123", "true"]],
        )

    def test_missing_optional_fields_become_empty_strings(self) -> None:
        status = DeviceStatus(
            uuid="def",
            label="pump_1",
            type="pump",
            driver="RegloICCClient",
            endpoint="COM3",
            connected=False,
            state="disconnected",
        )
        rows = device_inventory_rows([status])
        self.assertEqual(rows, [["pump_1", "pump", "", "RegloICCClient", "COM3", "", "", "", "false"]])

    def test_empty_status_list_returns_empty_rows(self) -> None:
        self.assertEqual(device_inventory_rows([]), [])

    def test_multiple_statuses_preserve_input_order(self) -> None:
        first = DeviceStatus(
            uuid="a", label="pump_1", type="pump", driver="RegloICCClient", endpoint="COM3", connected=True, state="connected"
        )
        second = DeviceStatus(
            uuid="b", label="switch_1", type="switch", driver="AMFSwitchController", endpoint="COM4", connected=False, state="error"
        )
        rows = device_inventory_rows([first, second])
        self.assertEqual([row[0] for row in rows], ["pump_1", "switch_1"])
        self.assertEqual(rows[1][-1], "false")


if __name__ == "__main__":
    unittest.main()
