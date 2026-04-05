from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import xml.etree.ElementTree as ET
from urllib.request import urlopen


@dataclass
class FeedData:
    title: str
    site_url: str | None
    description: str | None
    artwork_url: str | None
    episodes: list[dict]


def _parse_duration(raw: str | None) -> int | None:
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    m = re.match(r"^(?:(\d+):)?(\d+):(\d+)$", raw)
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _child_text(node: ET.Element, tag_names: list[str]) -> str | None:
    for tag in tag_names:
        el = node.find(tag)
        if el is not None and el.text:
            return el.text.strip()
    return None


def _channel_artwork_url(channel: ET.Element) -> str | None:
    # Prefer the iTunes podcast artwork when present.
    itunes_image = channel.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}image")
    if itunes_image is not None:
        href = (itunes_image.get("href") or "").strip()
        if href:
            return href

    # Fall back to the standard RSS image block.
    image = channel.find("image")
    if image is not None:
        image_url = _child_text(image, ["url"])
        if image_url:
            return image_url

    return None


def fetch_feed(url: str) -> FeedData:
    logger = logging.getLogger(__name__)
    logger.info("Fetching feed: %s", url)
    xml_text = urlopen(url, timeout=20).read().decode("utf-8", errors="replace")
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("Feed did not include a channel element")

    episodes = []
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        media_url = enc.get("url") if enc is not None else None
        if not media_url:
            continue
        episodes.append(
            {
                "guid": (_child_text(item, ["guid"]) or media_url),
                "title": (_child_text(item, ["title"]) or "Untitled episode"),
                "description": _child_text(item, ["description"]),
                "published_at": _child_text(item, ["pubDate"]),
                "duration_seconds": _parse_duration(
                    _child_text(item, ["{http://www.itunes.com/dtds/podcast-1.0.dtd}duration", "itunes:duration"])
                ),
                "media_url": media_url,
            }
        )

    feed_data = FeedData(
        title=_child_text(channel, ["title"]) or url,
        site_url=_child_text(channel, ["link"]),
        description=_child_text(channel, ["description"]),
        artwork_url=_channel_artwork_url(channel),
        episodes=episodes,
    )
    logger.info("Parsed feed '%s' with %s episodes from %s", feed_data.title, len(feed_data.episodes), url)
    return feed_data
