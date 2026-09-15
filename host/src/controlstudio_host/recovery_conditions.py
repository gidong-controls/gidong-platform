"""Conservative necessary-condition analysis of settled OUT equations.

This is not a scan simulator. A finding proves contradictory requirements in
the settled Boolean equations, not timing, transition, or hardware behavior.
"""
from collections import defaultdict
from typing import Any


def blocked_recovery_conditions(design: dict[str, Any]) -> list[dict[str, Any]]:
    writers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rung in design.get("ladderRungs") or []:
        action = rung.get("action") or {}
        if action.get("targetType") == "signal":
            writers[action.get("targetId")].append(rung)

    def necessary(node: dict[str, Any], state: bool) -> set[tuple[str, str, bool]]:
        kind = node.get("kind")
        if kind == "contact":
            return {(node.get("sourceType"), node.get("sourceId"), state == (node.get("state") == "on"))}
        if kind not in {"all_of", "any_of"} or not node.get("terms"):
            return set()
        branches = [necessary(term, state) for term in node["terms"]]
        # AND true / OR false requires every branch. Otherwise retain only
        # facts shared by all alternatives; never choose an arbitrary branch.
        if (kind == "all_of") == state:
            return set().union(*branches)
        return set.intersection(*branches)

    findings = []
    for index, recovery in enumerate(design.get("recoveryRules") or []):
        required = {("signal", term["signalId"], term["state"] == "on") for term in recovery.get("whenAll") or []}
        pending = list(required)
        expanded = set()
        rung_ids = set()
        while pending:
            fact = pending.pop()
            if fact in expanded:
                continue
            expanded.add(fact)
            kind, identity, state = fact
            candidates = writers.get(identity, [])
            if kind != "signal" or len(candidates) != 1:
                continue
            rung = candidates[0]
            if rung["action"].get("instruction") != "OUT":
                continue
            rung_ids.add(rung["rungId"])
            dependencies = necessary(rung["condition"], state)
            pending.extend(dependencies - required)
            required.update(dependencies)
        conflicts = sorted({identity for kind, identity, state in required if (kind, identity, not state) in required})
        if conflicts:
            findings.append({
                "index": index, "recoveryId": recovery["recoveryId"],
                "conflictingSignals": conflicts, "dependencyRungIds": sorted(rung_ids),
                "scope": "settled_boolean_necessary_conditions_not_runtime_test",
            })
    return findings
