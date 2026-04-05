from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from .paths import artwork_cache_dir


def _suffix_for_url(url: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return suffix
    return ".img"


def cache_podcast_artwork(url: str | None) -> str:
    if not url:
        return ""

    logger = logging.getLogger(__name__)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    target = artwork_cache_dir() / f"{digest}{_suffix_for_url(url)}"
    if target.exists() and target.stat().st_size > 0:
        return target.resolve().as_uri()

    try:
        payload = urlopen(url, timeout=20).read()
    except (ValueError, OSError) as exc:
        logger.warning("Artwork download failed for %s: %s", url, exc)
        return url

    if not payload:
        return url

    target.write_bytes(payload)
    logger.info("Cached podcast artwork %s -> %s", url, target)
    return target.resolve().as_uri()
