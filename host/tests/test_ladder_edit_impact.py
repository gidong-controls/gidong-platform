import copy

import pytest

from controlstudio_host.planning_drafts import _ladder_edit_impact


@pytest.mark.parametrize("field,executable", [("description", False), ("condition", True), ("action", True)])
def test_saved_fields_distinguish_comments_from_program(field, executable):
    before = [{"rung_id": "rung", "description": "old", "condition": {"state": "on"},
               "action": {"instruction": "OUT"}}]
    after = copy.deepcopy(before)
    after[0][field] = "new" if field == "description" else {"changed": True}
    result = _ladder_edit_impact(before, after)
    assert result["executableChanged"] is executable
    assert result["changedFieldsByRung"] == {"rung": [field]}


def test_scan_order_change_is_executable_even_without_field_changes():
    before = [{"rung_id": "a", "description": "a"}, {"rung_id": "b", "description": "b"}]
    result = _ladder_edit_impact(before, list(reversed(before)))
    assert result["executableChanged"] is True
    assert result["changedFieldsByRung"] == {}
