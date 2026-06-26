from __future__ import annotations

from typing import Any


def filter_items_by_text(items: list[dict[str, Any]], text: str, fields: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    query = text.strip().lower()
    if not query:
        return items

    return [
        item
        for item in items
        if any(query in str(item.get(field) or "").lower() for field in fields)
    ]
