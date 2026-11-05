"""Pure grammar for signed XGK/XGB comparison expressions in raw LD evidence.

This parser describes only supported syntax and operand widths. It neither
trusts producer metadata nor establishes CPU, connectivity, or runtime facts.
Callers must reparse their raw evidence and enforce their own evidence policy.
"""
from __future__ import annotations

import re
from typing import Any

_COMPARISON_RELATIONS = {
    "=": "equal",
    "<>": "not_equal",
    ">": "greater_than",
    "<": "less_than",
    ">=": "greater_than_or_equal",
    "<=": "less_than_or_equal",
}
_COMPARISON_DEVICE = re.compile(r"^[PMKFTCLD][0-9]+$")
_COMPARISON_LITERAL = re.compile(r"^[+-]?[0-9]+$")


def parse_comparison_expression(expression: str) -> dict[str, Any] | None:
    """Parse one exact signed XGK/XGB comparison without CPU/running claims."""

    fields = expression.split(",")
    if len(fields) != 3:
        return None
    instruction, lhs_text, rhs_text = (field.strip() for field in fields)
    double_word = instruction.startswith("D")
    relation = _COMPARISON_RELATIONS.get(
        instruction[1:] if double_word else instruction
    )
    if relation is None or not lhs_text or not rhs_text:
        return None
    width_words = 2 if double_word else 1
    data_type = "signed_32_bit" if double_word else "signed_16_bit"
    minimum, maximum = (
        (-2_147_483_648, 2_147_483_647)
        if width_words == 2
        else (-32_768, 32_767)
    )

    def operand(text: str) -> dict[str, Any] | None:
        if _COMPARISON_DEVICE.fullmatch(text) is not None:
            return {"kind": "device", "value": text}
        if _COMPARISON_LITERAL.fullmatch(text) is None:
            return None
        value = int(text, 10)
        if not minimum <= value <= maximum:
            return None
        return {"kind": "literal", "value": value}

    lhs = operand(lhs_text)
    rhs = operand(rhs_text)
    if lhs is None or rhs is None:
        return None
    devices: list[str] = []
    for item in (lhs, rhs):
        if item["kind"] == "device" and item["value"] not in devices:
            devices.append(item["value"])
    return {
        "instruction": instruction,
        "relation": relation,
        "data_type": data_type,
        "width_words": width_words,
        "lhs": lhs,
        "rhs": rhs,
        "devices": devices,
    }


__all__ = ["parse_comparison_expression"]
