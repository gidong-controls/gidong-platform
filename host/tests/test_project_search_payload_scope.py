import copy

import pytest

from controlstudio_host.mcp_client import MCPToolError
from controlstudio_host.structural_evidence import (
    _project_search_contract,
    canonical_digest,
)


def _search_result(program_count, matched_program):
    payloads = [
        {"program_id": f"program-{index:03d}", "name": f"Program {index:03d}",
         "payload_sha256": "b" * 64, "stable_repeat": True, "cache_hit": False}
        for index in range(program_count)
    ]
    match = {"kind": "program_name", "matched_field": "name",
             "matched_text": f"Program {matched_program:03d}",
             "program_id": f"program-{matched_program:03d}",
             "program_name": f"Program {matched_program:03d}"}
    match["match_id"] = canonical_digest(match)
    search = {"schema": "xg5000.project-search/1.0", "query": "Program",
              "case_sensitive": False, "project_sha256": "a" * 64,
              "program_id": None, "pagination": {"offset": 0, "limit": 1,
                  "returned": 1, "total": 1, "next_offset": None},
              "payloads": payloads, "program_count": program_count,
              "matches": [match], "match_count": 1, "total_match_count": 1,
              "status": "complete", "limitations": []}
    search["search_digest"] = canonical_digest(search)
    return {"ok": True, "verified": True, "project_unchanged": True,
            "operation": "project.search", "project_hash_before": "a" * 64,
            "project_hash_after": "a" * 64, "search": search}


def test_search_accepts_201st_program_beyond_retained_payload_scope():
    raw = _search_result(201, 200)
    original = copy.deepcopy(raw)
    candidates = []
    receipt = _project_search_contract(raw, model_candidates=candidates)
    scope = receipt["scope"]
    assert scope["payloadCount"] == 201
    assert len(scope["payloadBindings"]) == 200
    assert scope["payloadBindingsTruncated"] is True
    assert receipt["coverage"]["status"] == "partial"
    assert any("bounded subset" in item for item in receipt["coverage"]["limitations"])
    assert all(item["programId"] != "program-200" for item in scope["payloadBindings"])
    assert candidates[0]["programId"] == "program-200"
    assert candidates[0]["programName"] == "Program 200"
    assert raw == original

    boundary = _project_search_contract(_search_result(200, 199))["scope"]
    assert len(boundary["payloadBindings"]) == 200
    assert boundary["payloadBindingsTruncated"] is False

    # A bounded public scope must not relax membership in the actual raw scope.
    with pytest.raises(MCPToolError, match="program-name match is inconsistent"):
        _project_search_contract(_search_result(201, 201))
