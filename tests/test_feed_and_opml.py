from pathlib import Path

from plainpod.feed import _parse_duration
from plainpod.opml import export_opml, import_opml
from plainpod.repository import Podcast


def test_parse_duration_formats() -> None:
    assert _parse_duration("123") == 123
    assert _parse_duration("01:02:03") == 3723
    assert _parse_duration("12:34") == 754
    assert _parse_duration("nope") is None


def test_opml_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "feeds.opml"
    podcasts = [
        Podcast(id=1, title="A", feed_url="https://example.com/a.xml", site_url=None, description=None, artwork_url=None, download_policy="ask"),
        Podcast(id=2, title="B", feed_url="https://example.com/b.xml", site_url=None, description=None, artwork_url=None, download_policy="ask"),
    ]
    export_opml(podcasts, out)
    urls = import_opml(out)
    assert urls == ["https://example.com/a.xml", "https://example.com/b.xml"]
