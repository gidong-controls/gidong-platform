import unittest

from controlstudio_host.execution_broker import _ld_record_projection, _ld_topology_projection
from controlstudio_host.mcp_client import MCPToolError


class WireProjectionTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "opcode": "0", "coordinate": 2051, "value": "", "kind": "WIRE",
            "visual_row": 2, "column": 3, "token_offset": 33,
            "value_token_offset": 35, "raw_tokens": ["0", "2051", ""],
        }

    def test_wire_retains_empty_operand(self):
        self.assertEqual(_ld_record_projection(self.record), self.record)

    def test_empty_operand_is_not_accepted_for_other_record_kinds(self):
        for change in ({"kind": "CONTACT"}, {"opcode": "1"}, {"value": None}):
            with self.subTest(change=change), self.assertRaises(MCPToolError):
                _ld_record_projection({**self.record, **change})

    def test_partial_topology_is_preserved_instead_of_implying_serial_logic(self):
        value = {"status": "partial", "parallel_branches": []}
        self.assertEqual(_ld_topology_projection(value), value)
        self.assertEqual(_ld_topology_projection(None), value)

    def test_invalid_topology_is_rejected(self):
        with self.assertRaises(MCPToolError):
            _ld_topology_projection({"status": "complete", "parallel_branches": []})


if __name__ == "__main__":
    unittest.main()
