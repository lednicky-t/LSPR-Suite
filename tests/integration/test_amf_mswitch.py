from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.amf_mswitch import detect_amf_mswitch_devices


class AmfMswitchTests(unittest.TestCase):
    def test_detect_amf_mswitch_devices_suppresses_backend_console_output(self) -> None:
        fake_device = SimpleNamespace(
            deviceFamily="RVM",
            deviceType="RVM-1",
            comPort="COM9",
            serialnumber="1234",
            connectionMode="RS485",
        )

        fake_amf_tools = SimpleNamespace(
            util=SimpleNamespace(
                getProductList=lambda: (
                    print("No RS485 device found"),
                    print("No USB/RS232 device found"),
                    [fake_device],
                )[-1]
            )
        )

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with patch("lspr_acq_shell.amf_mswitch.amfTools", fake_amf_tools):
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                devices = detect_amf_mswitch_devices()

        self.assertEqual(stdout_buffer.getvalue(), "")
        self.assertEqual(stderr_buffer.getvalue(), "")
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].port, "COM9")
        self.assertEqual(devices[0].controller_type, "amf-mswitch")


if __name__ == "__main__":
    unittest.main()
