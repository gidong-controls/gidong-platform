from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from controlstudio_conformance.runner import check_agent, check_mcp

MOCKS_SRC = Path(__file__).resolve().parents[2] / "mocks" / "src"


class ConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_pythonpath = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = str(MOCKS_SRC) + (os.pathsep + self.old_pythonpath if self.old_pythonpath else "")

    def tearDown(self) -> None:
        if self.old_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = self.old_pythonpath

    def test_public_mocks_pass_agent_and_mcp_contracts(self) -> None:
        check_agent([sys.executable, "-m", "controlstudio_mocks.agent"])
        check_mcp([sys.executable, "-m", "controlstudio_mocks.mcp"])


if __name__ == "__main__":
    unittest.main()
