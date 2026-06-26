from __future__ import annotations

from plainpod.filtering import filter_items_by_text


def test_filter_items_by_text_filters_selected_fields_only() -> None:
    items = [
        {"title": "Alpha", "podcast": "One", "status": "queued"},
        {"title": "Beta", "podcast": "Two", "status": "failed"},
    ]

    filtered = filter_items_by_text(items, "two", fields=("title", "podcast"))

    assert filtered == [items[1]]


def test_filter_items_by_text_returns_all_items_for_empty_text() -> None:
    items = [{"title": "Alpha"}, {"title": "Beta"}]

    assert filter_items_by_text(items, "   ", fields=("title",)) == items
