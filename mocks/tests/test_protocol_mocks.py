from __future__ import annotations

import unittest

from controlstudio_mocks.agent import MANIFEST as AGENT_MANIFEST
from controlstudio_mocks.agent import MockAgent, frame
from controlstudio_mocks.common import canonical_digest
from controlstudio_mocks.mcp import MANIFEST, MANIFEST_BASE
from controlstudio_mocks.mcp import handle as mcp_handle


class ProtocolMockTests(unittest.TestCase):
    def test_agent_never_exposes_target_or_approval_data(self) -> None:
        agent = MockAgent()
        initialized = agent.handle(frame("initialize"))[0]
        self.assertEqual(initialized["payload"]["manifest"], AGENT_MANIFEST)
        replies = agent.handle(frame("run.start", {"run_id": "run-1", "conversation_id": "c-1", "tools": []}))
        self.assertEqual(replies[0]["type"], "run.started")
        proposal = replies[1]["payload"]
        self.assertEqual(proposal["tool_name"], "workspace.inspect")
        self.assertNotIn("target", str(replies).lower())
        self.assertNotIn("approval", str(replies).lower())
        final = agent.handle(frame("tool.result", {"request_id": proposal["request_id"], "status": "completed", "result": {}}))
        self.assertEqual(final[0]["type"], "run.final")

    def test_agent_envelope_is_extra_forbid(self) -> None:
        invalid = {**frame("health"), "request_id": "legacy-field"}
        reply = MockAgent().handle(invalid)
        self.assertEqual(reply[0]["type"], "protocol.error")

    def test_mcp_manifest_digest_and_unapproved_mutation(self) -> None:
        self.assertEqual(MANIFEST["manifest_digest"], canonical_digest(MANIFEST_BASE))
        reply = mcp_handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "project.apply_mock", "arguments": {"change": "x"}}})
        result = reply["result"]["structuredContent"]
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(result["failure_kind"], "approval_required")


if __name__ == "__main__":
    unittest.main()
