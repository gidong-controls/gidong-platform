"""Literal, paged navigation of a retained design; never an edit or a repair."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def search_design_items(
    sections: Mapping[str, Any],
    identities: Mapping[str, str],
    *,
    query: str,
    offset: int = 0,
    limit: int = 16,
) -> dict[str, Any]:
    if not isinstance(query, str) or not 1 <= len(query) <= 160:
        raise ValueError("search query must contain 1 to 160 characters")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 64:
        raise ValueError("search offset/limit is invalid")
    needle = query.casefold()

    def paths(value: Any, path: str = "") -> list[str]:
        if isinstance(value, str):
            return [path] if needle in value.casefold() else []
        if isinstance(value, dict):
            matched: list[str] = []
            for key, child in value.items():
                escaped_key = str(key).replace("~", "~0").replace("/", "~1")
                child_path = path + "/" + escaped_key
                if needle in str(key).casefold():
                    matched.append(child_path)
                matched.extend(paths(child, child_path))
            # A literal may match both a key and its string value. Return the
            # field locator once while preserving the document's traversal order.
            return list(dict.fromkeys(matched))
        if isinstance(value, list):
            return [p for index, child in enumerate(value) for p in paths(child, path + f"/{index}")]
        return []

    matches: list[dict[str, Any]] = []
    for section, value in sections.items():
        identity = identities.get(section)
        if section == "simulation_scenario" and isinstance(value, dict):
            items, identity = value.get("steps", []), "name"
        else:
            items = value if isinstance(value, list) else [value]
        for item in items:
            matched_paths = paths(item)
            if not matched_paths:
                continue
            matches.append({
                "section": section,
                "itemId": item.get(identity) if identity and isinstance(item, dict) else None,
                "matchedPaths": matched_paths,
                "value": item,
            })
    page: list[dict[str, Any]] = []
    size = 0
    for match in matches[offset:offset + limit]:
        encoded = json.dumps(match, ensure_ascii=False, separators=(",", ":"))
        if page and size + len(encoded.encode("utf-8")) > 48_000:
            break
        # Keep one item intact even when it exceeds the page target. Never
        # supply a truncated item that could overwrite omitted user fields.
        page.append(json.loads(encoded))
        size += len(encoded.encode("utf-8"))
    end = offset + len(page)
    return {
        "query": query, "matches": page, "matchCount": len(matches),
        "nextOffset": end if end < len(matches) else None,
        "selectionKind": "matching_items_not_complete_sections",
    }
