"""RSS responses using ASGI transport without lifespan or browser startup."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi import FastAPI

import app.api.v1.feed as feed_module
from app.core.config import FeedConfig, SchedulerConfig, TemplateItemConfig, TemplatesConfig, ViewportConfig
from app.core.errors import StorageError
from app.models.schemas import DailyDataFile


@pytest.fixture
def feed_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Provide injected configuration and inline thread work for this sandbox."""
    async def inline(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(feed_module.asyncio, "to_thread", inline)
    app = FastAPI()
    app.include_router(feed_module.router)
    templates = TemplatesConfig(
        default="cute",
        items=[
            TemplateItemConfig(name="moyuren", path="templates/moyuren.html", viewport=ViewportConfig()),
            TemplateItemConfig(name="cute", path="templates/moyuren_cute.html", viewport=ViewportConfig()),
        ],
    )
    app.state.config = SimpleNamespace(
        feed=FeedConfig(),
        paths=SimpleNamespace(cache_dir=str(tmp_path)),
        server=SimpleNamespace(base_domain="https://example.com/"),
        scheduler=SchedulerConfig(),
        get_templates_config=lambda: templates,
    )
    app.state.logger = logging.getLogger("test.feed")
    return app


def _save(
    tmp_path: Path, day: str = "2026-10-29", **values: Any
) -> DailyDataFile:
    """Persist daily metadata and a small JPEG placeholder with known size."""
    data = DailyDataFile.model_validate({
        "date": day, "updated": f"{day} 06:00:00",
        "updated_at": int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000) + 321,
        "images": {"cute": f"cute_{day}.jpg", "moyuren": f"moyuren_{day}.jpg"},
        **values,
    })
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / f"{day}.json").write_text(json.dumps(data.model_dump()))
    (tmp_path / "images").mkdir(exist_ok=True)
    for filename in data.images.values():
        (tmp_path / "images" / filename).write_bytes(b"jpeg bytes")
    return data


def _client(app: FastAPI) -> httpx.AsyncClient:
    """Create a minimal async HTTP client."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _channel(response: httpx.Response) -> ET.Element:
    """Parse RSS and require its channel element."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/rss+xml; charset=utf-8"
    root = ET.fromstring(response.content)
    assert root.tag == "rss" and root.attrib == {"version": "2.0"}
    channel = root.find("channel")
    assert channel is not None
    return channel


async def test_feed_xml_content_and_safe_description(feed_app: FastAPI, tmp_path: Path) -> None:
    """Expose ordered dated items, valid dates, enclosures and safely escaped HTML."""
    _save(tmp_path, "2026-10-28")
    newest = _save(
        tmp_path, weekday="星期四\x01", lunar_date="九月\x0b十九 & <节>",
        news_list=["<script>alert('x')</script> & 新闻\x00\ufffe", "二", "三", "四", "五", "六"],
        is_stale=True, source_date="2026-10-28",
    )
    async with _client(feed_app) as client:
        response = await client.get("/api/v1/feed.xml?limit=2")
    channel = _channel(response)
    assert channel.findtext("title") == "摸鱼日历"
    assert channel.findtext("link") == "https://example.com/"
    assert channel.findtext("description")
    assert channel.findtext("language") == "zh-CN"
    assert channel.findtext("ttl") == "1440"
    atom = channel.find("{http://www.w3.org/2005/Atom}link")
    assert atom is not None
    assert atom.attrib == {
        "href": "https://example.com/api/v1/feed.xml?limit=2",
        "rel": "self", "type": "application/rss+xml",
    }
    items = channel.findall("item")
    assert [item.findtext("title") for item in items] == [
        "摸鱼日历 2026-10-29 星期四", "摸鱼日历 2026-10-28 星期三",
    ]
    item = items[0]
    assert item.findtext("link") == "https://example.com/static/cute_2026-10-29.jpg"
    guid = item.find("guid")
    assert guid is not None and guid.get("isPermaLink") == "false"
    assert guid.text == f"moyuren:cute:2026-10-29:{newest.updated_at}"
    published = item.findtext("pubDate")
    assert published is not None
    assert parsedate_to_datetime(published) == datetime(2026, 10, 29, tzinfo=UTC)
    assert channel.findtext("lastBuildDate") == published
    enclosure = item.find("enclosure")
    assert enclosure is not None
    assert enclosure.attrib == {
        "url": "https://example.com/static/cute_2026-10-29.jpg",
        "type": "image/jpeg", "length": "10",
    }
    description = item.findtext("description") or ""
    assert '<img src="https://example.com/static/cute_2026-10-29.jpg" alt="摸鱼日历 2026-10-29"/>' in description
    assert "九月十九 &amp; &lt;节&gt;" in description
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; 新闻" in description
    assert "<script>" not in description
    assert "&amp;lt;script&amp;gt;" in response.text
    assert description.count("<li>") == 5 and "<li>六</li>" not in description
    assert "数据日期 2026-10-28 （非当日）" in description
    assert not any(character in response.text for character in ["\x00", "\x01", "\x0b", "\ufffe"])


async def test_skip_bad_files_and_stop_after_limit(
    feed_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Ignore malformed filenames, bad JSON, unreadable files and nonmatching templates."""
    _save(tmp_path, "2026-10-20")
    _save(tmp_path, "2026-10-21")
    _save(tmp_path, "2026-10-22", images={"moyuren": "other.jpg"})
    _save(tmp_path, "2026-10-23")
    (tmp_path / "data" / "2026-10-24.json").write_text("{bad")
    _save(tmp_path, "2026-10-25", updated_at=10**30)
    for name in ["2026-02-30.json", "latest.json", "2026-1-1.json"]:
        (tmp_path / "data" / name).write_text("{bad")
    reads: list[str] = []
    original = DailyDataFile.load

    def load(path: Path) -> DailyDataFile:
        reads.append(path.name)
        if path.name == "2026-10-23.json":
            raise StorageError("unreadable file")
        return original(path)

    monkeypatch.setattr(DailyDataFile, "load", load)
    with caplog.at_level(logging.DEBUG, logger="test.feed"):
        async with _client(feed_app) as client:
            response = await client.get("/api/v1/feed.xml?limit=1")
    items = _channel(response).findall("item")
    assert len(items) == 1 and "2026-10-21" in (items[0].findtext("title") or "")
    assert reads == ["2026-10-25.json", "2026-10-24.json", "2026-10-23.json", "2026-10-22.json", "2026-10-21.json"]
    assert "Skipping invalid or unreadable feed file" in caplog.text


@pytest.mark.parametrize("existing_directory", [False, True])
async def test_empty_feed(feed_app: FastAPI, tmp_path: Path, existing_directory: bool) -> None:
    """Absent and empty data directories both produce valid zero-item RSS."""
    if existing_directory:
        (tmp_path / "data").mkdir()
    async with _client(feed_app) as client:
        response = await client.get("/api/v1/feed.xml")
    channel = _channel(response)
    assert channel.findall("item") == []
    assert channel.find("lastBuildDate") is None


async def test_missing_image_omits_only_enclosure(feed_app: FastAPI, tmp_path: Path) -> None:
    """An unavailable JPEG preserves its dated entry and description."""
    _save(tmp_path)
    (tmp_path / "images" / "cute_2026-10-29.jpg").unlink()
    async with _client(feed_app) as client:
        response = await client.get("/api/v1/feed.xml")
    item = _channel(response).find("item")
    assert item is not None
    assert item.find("enclosure") is None
    assert item.findtext("link") and item.findtext("description")


async def test_limit_template_and_enabled_validation(feed_app: FastAPI, tmp_path: Path) -> None:
    """Respect the configured maximum, template default and standard error response."""
    feed_app.state.config.feed = FeedConfig(max_items=2)
    for day in ["2026-10-27", "2026-10-28", "2026-10-29"]:
        _save(tmp_path, day)
    async with _client(feed_app) as client:
        response = await client.get("/api/v1/feed.xml")
        assert len(_channel(response).findall("item")) == 2
        assert len(_channel(await client.get("/api/v1/feed.xml?limit=1")).findall("item")) == 1
        for limit in ["0", "-1", "3", "61", "abc"]:
            assert (await client.get(f"/api/v1/feed.xml?limit={limit}")).status_code == 422
        response = await client.get("/api/v1/feed.xml?template=moyuren")
        channel = _channel(response)
        assert channel.findtext("title") == "摸鱼日历 moyuren"
        assert "moyuren_2026-10-29.jpg" in (channel.findtext("item/link") or "")
        unknown = await client.get("/api/v1/feed.xml?template=unknown")
        assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "API_7004"
        feed_app.state.config.feed.enabled = False
        for method in [client.get, client.head]:
            for query in ["", "?limit=abc", "?limit=0", "?limit=3", "?template=unknown"]:
                response = await method(f"/api/v1/feed.xml{query}")
                assert response.status_code == 404
        assert response.content == b""
        assert (await client.get("/api/v1/feed.xml")).json() == {"detail": "Not Found"}


async def test_http_cache_and_head(feed_app: FastAPI, tmp_path: Path) -> None:
    """ETag scopes template/limit, regeneration changes validators, HEAD matches GET."""
    data = _save(tmp_path)
    async with _client(feed_app) as client:
        response = await client.get("/api/v1/feed.xml?limit=1")
        assert response.status_code == 200
        etag = response.headers["etag"]
        modified = response.headers["last-modified"]
        assert str(data.updated_at) in etag
        assert response.headers["cache-control"] == "public, max-age=300, must-revalidate"
        head = await client.head("/api/v1/feed.xml?limit=1")
        assert head.status_code == 200 and head.content == b""
        for key in ["etag", "last-modified", "cache-control", "content-type", "content-length"]:
            assert head.headers[key] == response.headers[key]
        for headers in [
            {"If-None-Match": etag}, {"If-None-Match": f"W/{etag}"},
            {"If-Modified-Since": modified},
        ]:
            cached = await client.get("/api/v1/feed.xml?limit=1", headers=headers)
            assert cached.status_code == 304 and cached.content == b""
            assert cached.headers["etag"] == etag
            assert cached.headers["cache-control"] == response.headers["cache-control"]
        assert (await client.head("/api/v1/feed.xml?limit=1", headers={"If-None-Match": etag})).status_code == 304
        for query in ["limit=2", "template=moyuren&limit=1"]:
            other = await client.get(f"/api/v1/feed.xml?{query}", headers={"If-None-Match": etag})
            assert other.status_code == 200 and other.headers["etag"] != etag
        mismatched = await client.get("/api/v1/feed.xml?limit=1", headers={
            "If-None-Match": '"other"', "If-Modified-Since": modified,
        })
        assert mismatched.status_code == 200
        for since in ["invalid", "Wed, 28 Oct 2026 00:00:00 GMT"]:
            assert (await client.get("/api/v1/feed.xml?limit=1", headers={"If-Modified-Since": since})).status_code == 200
        _save(tmp_path, updated_at=data.updated_at + 1000)
        regenerated = await client.get("/api/v1/feed.xml?limit=1", headers={"If-None-Match": etag})
        assert regenerated.status_code == 200 and regenerated.headers["etag"] != etag
        assert _channel(regenerated).findtext("item/guid") == f"moyuren:cute:{data.date}:{data.updated_at + 1000}"


@pytest.mark.parametrize(("scheduler", "ttl"), [
    (SchedulerConfig(mode="hourly"), "60"),
    (SchedulerConfig(daily_times=["06:00", "18:00"]), None),
])
async def test_scheduler_ttl(feed_app: FastAPI, scheduler: SchedulerConfig, ttl: str | None) -> None:
    """Include TTL for simple intervals and omit it for multiple daily slots."""
    feed_app.state.config.scheduler = scheduler
    async with _client(feed_app) as client:
        response = await client.get("/api/v1/feed.xml")
    assert _channel(response).findtext("ttl") == ttl


def test_strip_xml_invalid_characters() -> None:
    """Keep legal whitespace/Unicode and strip controls and lone surrogates."""
    assert feed_module._clean_text("新闻😀\t\n\r\x00\x01\x0b\ud800\ufffe") == "新闻😀\t\n\r"
