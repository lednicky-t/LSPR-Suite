from __future__ import annotations

import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.connection_registry import (
    claim_port,
    port_owners,
    release_port,
    snapshot_port_ownership,
)


class ConnectionRegistryTests(unittest.TestCase):
    def test_claim_and_release_port_ownership(self) -> None:
        claim_port("COM9", "Experiment Control / Pump")
        claim_port("COM9", "Manual diagnostics")
        self.assertEqual(port_owners("COM9"), ("Experiment Control / Pump", "Manual diagnostics"))
        snapshot = snapshot_port_ownership()
        self.assertEqual(snapshot["COM9"], "Experiment Control / Pump, Manual diagnostics")

        release_port("COM9", "Experiment Control / Pump")
        self.assertEqual(port_owners("COM9"), ("Manual diagnostics",))

        release_port("COM9")
        self.assertEqual(port_owners("COM9"), ())
        self.assertNotIn("COM9", snapshot_port_ownership())


if __name__ == "__main__":
    unittest.main()
