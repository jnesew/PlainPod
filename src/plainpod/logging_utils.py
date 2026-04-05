from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    log_file = data_dir / "plainpod.log"

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    existing_file = any(
        isinstance(h, logging.FileHandler) and Path(getattr(h, "baseFilename", "")) == log_file
        for h in root.handlers
    )
    existing_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )

    # Avoid duplicate handlers during repeated app startups in the same process.
    if not existing_file:
        root.addHandler(file_handler)
    if not existing_console:
        root.addHandler(console_handler)

    return log_file
