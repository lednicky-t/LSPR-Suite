"""Tests for DeviceCommunicationService's driver connect-factory registry
(lspr_acq_shell.device_manager.register_driver_connect_factory) - the
generalization of _connect_impl()'s previously-hardcoded reglo_icc/
amf-mswitch/valve-detect three-way dispatch, added for LSPRi acq Phase 2
(2026-08-08) so Camera/IlluminationSource drivers can connect through the
shared service without a fourth hardcoded branch.

Tests the real owner (lspr_acq_shell.device_manager), not an app shim -
matches the "test the real owner" convention established across every
Phase 1 extraction (see docs/architecture/general/lspri_acq_build_log.md).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from lspr_acq_shell import device_manager
from lspr_acq_shell.device_manager import DeviceCommunicationService, register_driver_connect_factory
from lspr_acq_shell.serial_controllers import ControllerError


class _FakeCameraConnection:
    """Stand-in for a real driver's connection object - exposes exactly the
    surface register_driver_connect_factory()'s docstring requires
    (._claim_owner, .is_connected(), .close()), nothing more."""

    def __init__(self, endpoint: str) -> None:
        self.port = endpoint
        self._claim_owner = f"fake-camera:{id(self)}"
        self._connected = True
        self.closed = False

    def is_connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        self._connected = False
        self.closed = True


def _fake_camera_factory(endpoint: str):
    connection = _FakeCameraConnection(endpoint)
    return connection, {"model": "Fake Camera", "serial_number": "SN123"}


class DriverConnectFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher_load = patch.object(device_manager, "load_app_setting", return_value=[])
        patcher_save = patch.object(device_manager, "save_app_setting", return_value=None)
        patcher_load.start()
        patcher_save.start()
        self.addCleanup(patcher_load.stop)
        self.addCleanup(patcher_save.stop)
        register_driver_connect_factory("fake-camera-driver", _fake_camera_factory)

    def test_registered_driver_connects_via_generic_path(self) -> None:
        service = DeviceCommunicationService()
        service.register_endpoint_assignment(
            "camera_1", "fake-endpoint-0", device_type="camera", driver="fake-camera-driver", mark_manual=False
        )

        status = service.connect("camera_1")

        self.assertTrue(status.connected)
        self.assertEqual(status.identity["model"], "Fake Camera")
        self.assertTrue(service.is_connected("camera_1"))

    def test_disconnect_closes_the_registered_connection(self) -> None:
        service = DeviceCommunicationService()
        service.register_endpoint_assignment(
            "camera_1", "fake-endpoint-0", device_type="camera", driver="fake-camera-driver", mark_manual=False
        )
        service.connect("camera_1")
        connection = service.connection("camera_1")

        service.disconnect("camera_1")

        self.assertTrue(connection.closed)
        self.assertFalse(service.is_connected("camera_1"))

    def test_unregistered_driver_key_falls_through_to_existing_dispatch(self) -> None:
        # Not a registered driver key, and type/driver don't match
        # reglo_icc/amf-mswitch/valve either - should still hit the
        # pre-existing "unresolved" error, proving the new registry check
        # doesn't swallow a case it has no business handling.
        service = DeviceCommunicationService()
        service.register_endpoint_assignment(
            "unknown_1", "fake-endpoint-1", device_type="unknown", driver="auto", mark_manual=False
        )

        with self.assertRaises(ControllerError):
            service.connect("unknown_1")


if __name__ == "__main__":
    unittest.main()
