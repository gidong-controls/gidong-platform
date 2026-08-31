from __future__ import annotations

import unittest
from unittest.mock import patch

from controlstudio_host.structural_evidence import logic_graph_context


class LogicGraphModelContextTests(unittest.TestCase):
    def test_preserves_nested_condition_and_parenthesized_expression(self) -> None:
        condition = {
            "operator": "AND",
            "terms": [
                {"operator": "DEVICE", "device": "M00001", "contact": "NO"},
                {
                    "operator": "OR",
                    "terms": [
                        {"operator": "DEVICE", "device": "M00000", "contact": "NO"},
                        {"operator": "DEVICE", "device": "M00010", "contact": "NO"},
                    ],
                },
            ],
        }
        result = {
            "operation": "program.logic_graph",
            "ok": True,
            "verified": True,
            "project_unchanged": True,
            "project_hash_before": "a" * 64,
            "project_hash_after": "a" * 64,
            "graph": {
                "networks": [
                    {
                        "network_index": 0,
                        "rungs": [
                            {
                                "rung_id": "rung:seal-in",
                                "visual_rows": [0, 1],
                                "condition": condition,
                                "condition_expression": "M00001 AND (M00000 OR M00010)",
                                "outputs": [{"action": "OUTPUT", "operand": "M00010"}],
                                "semantic_status": "structured",
                                "confidence": "coordinate-derived",
                            }
                        ],
                    }
                ]
            },
        }

        with patch(
            "controlstudio_host.structural_evidence._logic_graph_contract"
        ) as validate_graph:
            context = logic_graph_context(result)

        validate_graph.assert_called_once_with(result)
        assert context is not None
        rung = context["rungs"][0]
        self.assertEqual(rung["condition"], condition)
        self.assertEqual(
            rung["condition_expression"], "M00001 AND (M00000 OR M00010)"
        )
        self.assertEqual(rung["visual_rows"], [0, 1])
        self.assertEqual(rung["outputs"], [{"action": "OUTPUT", "operand": "M00010"}])


if __name__ == "__main__":
    unittest.main()
