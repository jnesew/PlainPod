from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from .repository import Podcast


def export_opml(podcasts: list[Podcast], target: Path) -> None:
    opml = ET.Element("opml", version="2.0")
    body = ET.SubElement(opml, "body")
    for p in podcasts:
        ET.SubElement(body, "outline", type="rss", text=p.title, title=p.title, xmlUrl=p.feed_url)
    target.write_text(ET.tostring(opml, encoding="unicode"), encoding="utf-8")


def import_opml(source: Path) -> list[str]:
    root = ET.fromstring(source.read_text(encoding="utf-8"))
    urls: list[str] = []
    for outline in root.findall(".//outline"):
        xml_url = outline.attrib.get("xmlUrl")
        if xml_url:
            urls.append(xml_url)
    return urls
