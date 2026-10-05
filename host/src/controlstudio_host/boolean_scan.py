"""Bounded Boolean scan analysis; never generates or repairs PLC source.

OUT, SET and RST execute in source order. Unknown initial bits remain symbolic,
so equivalent latch implementations need not match a particular rung template.
Unsupported/time-dependent effects cannot establish a successful proof.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

ScanOffResult = Literal["proven_off", "may_be_on", "inconclusive"]
ScanUnchangedResult = Literal["proven_unchanged", "may_change", "inconclusive"]
ScanMatchResult = Literal["proven_match", "may_differ", "inconclusive"]


class _ProofLimit(Exception):
    pass


class _BooleanGraph:
    def __init__(self, limit: int):
        self.limit = limit
        self.nodes: dict[int, tuple[int, int, int]] = {}
        self.unique: dict[tuple[int, int, int], int] = {}
        self.cache: dict[tuple[str, int, int], int] = {}
        self.variables = 0
        self.opaque: set[int] = set()

    def node(self, variable: int, low: int, high: int) -> int:
        if low == high:
            return low
        key = (variable, low, high)
        if key not in self.unique:
            if len(self.nodes) >= self.limit:
                raise _ProofLimit
            identity = len(self.nodes) + 2
            self.nodes[identity] = key
            self.unique[key] = identity
        return self.unique[key]

    def variable(self, *, opaque: bool = False) -> int:
        index = self.variables
        self.variables += 1
        if opaque:
            self.opaque.add(index)
        return self.node(index, 0, 1)

    def combine(self, operator: str, left: int, right: int) -> int:
        if left > right:
            left, right = right, left
        if operator == "and" and (left == 0 or left == right):
            return left
        if operator == "or" and (left == 1 or left == right):
            return left
        if operator == "xor" and left == right:
            return 0
        key = (operator, left, right)
        if key in self.cache:
            return self.cache[key]
        if left < 2 and right < 2:
            return {"and": left & right, "or": left | right, "xor": left ^ right}[operator]
        if len(self.cache) >= max(1, self.limit * 8):
            raise _ProofLimit
        top = min(self.nodes[item][0] for item in (left, right) if item >= 2)

        def branches(item: int) -> tuple[int, int]:
            if item >= 2 and self.nodes[item][0] == top:
                return self.nodes[item][1:]
            return item, item

        left_low, left_high = branches(left)
        right_low, right_high = branches(right)
        result = self.node(
            top,
            self.combine(operator, left_low, right_low),
            self.combine(operator, left_high, right_high),
        )
        self.cache[key] = result
        return result

    def has_opaque(self, value: int) -> bool:
        pending = [value]
        seen: set[int] = set()
        while pending:
            item = pending.pop()
            if item < 2 or item in seen:
                continue
            seen.add(item)
            variable, low, high = self.nodes[item]
            if variable in self.opaque:
                return True
            pending.extend((low, high))
        return False

    def result(self, value: int) -> ScanOffResult:
        if value == 0:
            return "proven_off"
        if self.has_opaque(value):
            return "inconclusive"
        return "may_be_on"


def _scan_symbolic(
    rungs: Sequence[Mapping[str, Any]],
    *,
    fixed_signals: Mapping[str, bool],
    outputs: Sequence[str],
    node_limit: int,
    capture_initial_outputs: bool,
) -> tuple[_BooleanGraph, dict[tuple[str, str], int], dict[str, int]] | None:
    """Evaluate one source-ordered scan and optionally retain output start values."""
    graph = _BooleanGraph(node_limit)
    values = {("signal", name): int(value) for name, value in fixed_signals.items()}
    opaque_kinds: set[str] = set()

    def read(kind: str, identity: str) -> int:
        key = (kind, identity)
        if key not in values:
            values[key] = graph.variable(opaque=kind in opaque_kinds)
        return values[key]

    def condition(value: Any) -> int:
        if not isinstance(value, Mapping):
            return graph.variable(opaque=True)
        kind = value.get("kind")
        if not isinstance(kind, str):
            return graph.variable(opaque=True)
        if kind == "contact":
            source = value.get("sourceType")
            identity = value.get("sourceId")
            state = value.get("state")
            if (not isinstance(source, str)
                    or source not in {"signal", "timer", "step", "sequence_reset"}
                    or not isinstance(identity, str) or not identity
                    or not isinstance(state, str) or state not in {"on", "off"}):
                return graph.variable(opaque=True)
            result = read(source, identity)
            return result if state == "on" else graph.combine("xor", result, 1)
        terms = value.get("terms")
        if kind not in {"all_of", "any_of"} or not isinstance(terms, list) or not terms:
            return graph.variable(opaque=True)
        result = 1 if kind == "all_of" else 0
        operator = "and" if kind == "all_of" else "or"
        for term in terms:
            result = graph.combine(operator, result, condition(term))
            if result == (0 if operator == "and" else 1):
                break
        return result

    try:
        initial_values = {
            target: read("signal", target) for target in dict.fromkeys(outputs)
        } if capture_initial_outputs else {}
        for rung in rungs:
            if not isinstance(rung, Mapping):
                raise _ProofLimit
            action = rung.get("action")
            if not isinstance(action, Mapping):
                raise _ProofLimit
            if action.get("targetType") != "signal":
                # Sequence instructions can reset multiple step bits; timed
                # instructions depend on elapsed time. Do not invent those
                # effects or retain stale correlations across their writes.
                opaque_kinds.update({"timer", "step", "sequence_reset"})
                for key in list(values):
                    if key[0] in opaque_kinds:
                        values[key] = graph.variable(opaque=True)
                continue
            target = action.get("targetId")
            if not isinstance(target, str) or not target:
                raise _ProofLimit
            instruction = action.get("instruction")
            enabled = condition(rung.get("condition"))
            if instruction == "OUT":
                result = enabled
            elif instruction == "SET":
                result = 1 if enabled == 1 else graph.combine("or", read("signal", target), enabled)
            elif instruction == "RST":
                result = 0 if enabled == 1 else graph.combine(
                    "and", read("signal", target), graph.combine("xor", enabled, 1),
                )
            else:
                result = graph.variable(opaque=True)
            values[("signal", target)] = result
        for target in dict.fromkeys(outputs):
            read("signal", target)
        return graph, values, initial_values
    except (_ProofLimit, RecursionError):
        return None


def scan_outputs_off(
    rungs: Sequence[Mapping[str, Any]],
    *,
    fixed_signals: Mapping[str, bool],
    outputs: Sequence[str],
    node_limit: int = 4096,
) -> dict[str, ScanOffResult]:
    """Prove scan-end outputs OFF for every other initial Boolean state.

    Results describe only this scan property, not full program correctness or
    reachability. Resource exhaustion and unsupported effects are inconclusive.
    """
    scanned = _scan_symbolic(
        rungs,
        fixed_signals=fixed_signals,
        outputs=outputs,
        node_limit=node_limit,
        capture_initial_outputs=False,
    )
    if scanned is None:
        return {target: "inconclusive" for target in outputs}
    graph, values, _initial_values = scanned
    return {
        target: graph.result(values[("signal", target)])
        for target in outputs
    }


def scan_outputs_unchanged(
    rungs: Sequence[Mapping[str, Any]],
    *,
    fixed_signals: Mapping[str, bool],
    outputs: Sequence[str],
    node_limit: int = 4096,
) -> dict[str, ScanUnchangedResult]:
    """Classify whether each output retains its scan-start Boolean value."""
    scanned = _scan_symbolic(
        rungs,
        fixed_signals=fixed_signals,
        outputs=outputs,
        node_limit=node_limit,
        capture_initial_outputs=True,
    )
    if scanned is None:
        return {target: "inconclusive" for target in outputs}
    graph, values, initial_values = scanned
    results: dict[str, ScanUnchangedResult] = {}
    try:
        for target in outputs:
            difference = graph.combine(
                "xor", initial_values[target], values[("signal", target)]
            )
            if difference == 0:
                result: ScanUnchangedResult = "proven_unchanged"
            elif graph.has_opaque(difference):
                result = "inconclusive"
            else:
                result = "may_change"
            results[target] = result
    except (_ProofLimit, RecursionError):
        return {target: "inconclusive" for target in outputs}
    return results


def scan_outputs_match(
    rungs: Sequence[Mapping[str, Any]],
    *,
    fixed_signals: Mapping[str, bool],
    expected_outputs: Mapping[str, bool],
    when_final_any_on: Sequence[str] | None = None,
    node_limit: int = 4096,
) -> dict[str, ScanMatchResult]:
    """Prove final values, optionally only while a final-state guard is ON.

    The guard uses scan-end values, not input snapshots. This permits an
    invariant to stop applying after an actual state transition. A guarded
    match does not prove the guard was reachable or ever became true.
    """
    outputs = list(expected_outputs)
    scanned = _scan_symbolic(
        rungs,
        fixed_signals=fixed_signals,
        outputs=list(dict.fromkeys([*outputs, *(when_final_any_on or ())])),
        node_limit=node_limit,
        capture_initial_outputs=False,
    )
    if scanned is None:
        return {target: "inconclusive" for target in outputs}
    graph, values, _initial_values = scanned
    results: dict[str, ScanMatchResult] = {}
    try:
        guard = 1 if when_final_any_on is None else 0
        for identity in when_final_any_on or ():
            guard = graph.combine("or", guard, values[("signal", identity)])
        for target, expected in expected_outputs.items():
            difference = graph.combine(
                "xor", values[("signal", target)], int(expected)
            )
            difference = graph.combine("and", guard, difference)
            if difference == 0:
                result: ScanMatchResult = "proven_match"
            elif graph.has_opaque(difference):
                result = "inconclusive"
            else:
                result = "may_differ"
            results[target] = result
    except (_ProofLimit, RecursionError):
        return {target: "inconclusive" for target in outputs}
    return results
