"""私有预览使用隔离渲染与只读数据访问。"""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import ops
from app.core.config import OpsConfig, TemplateItemConfig, TemplatesConfig, ViewportConfig
from app.services.daily_cache import DailyCache

JPEG = b"\xff\xd8preview\xff\xd9"
URL = "/api/v1/ops/templates/preview"
AUTH = {"Authorization": "Bearer preview-secret"}


@pytest.fixture
def preview_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """构建不含 lifespan 与浏览器启动的最小应用。"""
    async def inline(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline)
    monkeypatch.setattr(ops, "today_business", lambda: date(2026, 10, 29))
    app = FastAPI()
    app.include_router(ops.router)
    templates = TemplatesConfig(items=[TemplateItemConfig(name="demo", path="demo.html", viewport=ViewportConfig())])
    app.state.config = SimpleNamespace(
        ops=OpsConfig(api_key="preview-secret", preview_wait_timeout_sec=0.02),
        paths=SimpleNamespace(cache_dir=str(tmp_path)),
        get_templates_config=lambda: templates,
    )
    app.state.logger = logging.getLogger("test-preview")
    app.state.services = SimpleNamespace(
        image_renderer=SimpleNamespace(render_bytes=AsyncMock(return_value=JPEG)),
        data_computer=SimpleNamespace(compute=MagicMock(side_effect=lambda raw, **_: raw)),
        generator=MagicMock(), data_fetcher=None, holiday_service=None,
        fun_content_service=None, kfc_service=None, stock_index_service=None,
        gold_price_service=None, daily_english_service=None,
    )
    monkeypatch.setattr(ops, "generate_and_save_image", AsyncMock(side_effect=AssertionError("generator called")))
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
async def test_auth(preview_app: FastAPI, headers: dict[str, str]) -> None:
    async with AsyncClient(transport=ASGITransport(app=preview_app), base_url="http://test") as client:
        response = await client.post(URL, json={"template": "demo"}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_6001"
    assert response.headers["www-authenticate"] == "Bearer"
    preview_app.state.services.image_renderer.render_bytes.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [
    ({"template": "missing"}, 404),
    ({"template": "../demo"}, 404),
    ({"template": "demo", "date": "2026-02-30"}, 422),
    ({"template": "demo", "date": "20261029"}, 422),
    ({"template": "demo", "data": "network"}, 422),
])
async def test_invalid_request(preview_app: FastAPI, body: dict[str, Any], status: int) -> None:
    async with AsyncClient(transport=ASGITransport(app=preview_app), base_url="http://test") as client:
        response = await client.post(URL, json=body, headers=AUTH)
    assert response.status_code == status
    if status == 404:
        assert response.json()["error"]["code"] == "API_7004"
    preview_app.state.services.image_renderer.render_bytes.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("preview_date", [None, "2026-02-05"])
async def test_sample_is_private_and_side_effect_free(
    preview_app: FastAPI, tmp_path: Path, preview_date: str | None,
) -> None:
    before = list(tmp_path.rglob("*"))
    async with AsyncClient(transport=ASGITransport(app=preview_app), base_url="http://test") as client:
        response = await client.post(URL, json={"template": "demo", "data": "sample", "date": preview_date}, headers=AUTH)
        get_response = await client.get(URL, headers=AUTH)
    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert get_response.status_code == 405
    assert list(tmp_path.rglob("*")) == before
    compute = preview_app.state.services.data_computer.compute
    now = compute.call_args.kwargs["now"]
    assert now.date().isoformat() == (preview_date or "2026-10-29")
    assert now.hour == 9 and now.minute == 0
    assert now.utcoffset().total_seconds() == 8 * 3600
    preview_app.state.services.image_renderer.render_bytes.assert_awaited_once_with(
        compute.call_args.args[0], template_name="demo",
    )
    ops.generate_and_save_image.assert_not_called()
    preview_app.state.services.generator.assert_not_called()


class DiskCache(DailyCache[dict[str, Any]]):
    """使用现有的只读磁盘缓存加载逻辑。"""

    async def fetch_fresh(self) -> dict[str, Any] | None:
        raise AssertionError("Network data fetch attempted")


@pytest.mark.asyncio
async def test_cached_reads_only_disk_with_per_key_fallback(
    preview_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = AsyncMock(side_effect=AssertionError("HTTP attempted"))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", network)
    cache = DiskCache("news", tmp_path, preview_app.state.logger)
    news = {"data": {"news": ["磁盘新闻"], "date": "2026-10-29"}}
    (tmp_path / "news.json").write_text(json.dumps({"date": "2026-10-29", "data": {"news": news}}))
    preview_app.state.services.data_fetcher = cache
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    async with AsyncClient(transport=ASGITransport(app=preview_app), base_url="http://test") as client:
        response = await client.post(URL, json={"template": "demo"}, headers=AUTH)
    assert response.status_code == 200
    raw = preview_app.state.services.data_computer.compute.call_args.args[0]
    assert raw["news"] == news
    assert raw["fun_content"] and raw["gold_price"] and raw["daily_english"]
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    network.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_preview_times_out_and_releases_lock(preview_app: FastAPI) -> None:
    entered, finish = asyncio.Event(), asyncio.Event()

    async def render(*args: Any, **kwargs: Any) -> bytes:
        entered.set()
        await finish.wait()
        return JPEG

    preview_app.state.services.image_renderer.render_bytes.side_effect = render
    async with AsyncClient(transport=ASGITransport(app=preview_app), base_url="http://test") as client:
        first = asyncio.create_task(client.post(URL, json={"template": "demo", "data": "sample"}, headers=AUTH))
        await asyncio.wait_for(entered.wait(), timeout=1)
        try:
            second = await client.post(URL, json={"template": "demo", "data": "sample"}, headers=AUTH)
            assert second.status_code == 503
            assert int(second.headers["retry-after"]) > 0
            assert second.headers["cache-control"] == "no-store"
            assert second.json()["error"]["code"] == "GENERATION_5002"
        finally:
            finish.set()
            assert (await first).status_code == 200
    assert not preview_app.state.preview_lock.locked()


@pytest.mark.asyncio
async def test_render_failure_releases_lock(preview_app: FastAPI) -> None:
    preview_app.state.services.image_renderer.render_bytes.side_effect = RuntimeError("failed")
    async with AsyncClient(transport=ASGITransport(app=preview_app), base_url="http://test") as client:
        response = await client.post(URL, json={"template": "demo", "data": "sample"}, headers=AUTH)
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert not preview_app.state.preview_lock.locked()
