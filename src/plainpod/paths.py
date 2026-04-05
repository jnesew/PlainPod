from __future__ import annotations

from pathlib import Path
import os


def data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".local" / "share")
    root = base / "plainpod"
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    return data_dir() / "plainpod.db"


def downloads_dir() -> Path:
    folder = data_dir() / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def artwork_cache_dir() -> Path:
    folder = data_dir() / "artwork"
    folder.mkdir(parents=True, exist_ok=True)
    return folder
