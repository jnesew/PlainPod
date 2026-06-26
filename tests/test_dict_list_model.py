from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, Qt

from plainpod.viewmodel import DictListModel


def test_update_item_by_key_emits_changed_roles_only() -> None:
    QCoreApplication.instance() or QCoreApplication([])
    model = DictListModel(["episode_id", "status", "progress_percent", "speed_bps"])
    model.set_items([{"episode_id": 1, "status": "downloading", "progress_percent": 10, "speed_bps": 100}])
    emissions: list[tuple[int, int, list[int]]] = []
    model.dataChanged.connect(
        lambda top_left, bottom_right, roles: emissions.append((top_left.row(), bottom_right.row(), list(roles)))
    )

    assert model.update_item_by_key(
        "episode_id",
        1,
        {"status": "downloading", "progress_percent": 25, "speed_bps": 200},
    )

    assert model.item(0)["progress_percent"] == 25
    assert model.item(0)["speed_bps"] == 200
    assert emissions == [(0, 0, [Qt.UserRole + 3, Qt.UserRole + 4])]


def test_update_item_by_key_returns_false_for_missing_row() -> None:
    QCoreApplication.instance() or QCoreApplication([])
    model = DictListModel(["episode_id", "status"])
    model.set_items([{"episode_id": 1, "status": "downloading"}])

    assert not model.update_item_by_key("episode_id", 2, {"status": "paused"})
    assert model.item(0)["status"] == "downloading"
