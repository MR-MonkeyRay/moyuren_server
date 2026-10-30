"""测试 app/api/v1/templates.py 的模板 API 端点。"""

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.templates import router as templates_router
from app.core.config import TemplateRenderConfig
from app.services.template_discovery import TemplateDiscovery


@pytest.fixture(autouse=True)
def run_to_thread_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免沙箱线程池挂起，同时保留异步调用边界。"""

    async def inline(
        function: Callable[..., object], *args: object, **kwargs: object
    ) -> object:
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline)


@pytest.fixture
def templates_app(tmp_path: Path) -> FastAPI:
    """创建挂载模板路由的测试 FastAPI 应用。"""
    app = FastAPI()
    app.include_router(templates_router)

    config = MagicMock()
    config.server.base_domain = "http://localhost:8000"
    config.paths.cache_dir = str(tmp_path / "cache")

    # 创建模板项
    item1 = MagicMock()
    item1.name = "moyuren"
    item1.description = "杂志风格版"
    item1.show_daily_english = False
    templates_config = MagicMock()
    templates_config.items = [item1]
    config.get_templates_config.return_value = templates_config

    # 创建缓存目录
    (tmp_path / "cache" / "data").mkdir(parents=True)

    app.state.config = config
    return app


class TestTemplatesAPI:
    """测试 /api/v1/templates 端点。"""

    @pytest.mark.anyio
    async def test_get_templates(self, templates_app: FastAPI) -> None:
        """验证获取模板时返回模板列表。"""
        with patch("app.api.v1.templates.today_business", return_value=date(2026, 2, 10)):
            async with AsyncClient(transport=ASGITransport(app=templates_app), base_url="http://test") as client:
                response = await client.get("/api/v1/templates")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) == 1
        assert data["data"][0]["name"] == "moyuren"
        assert data["data"][0]["description"] == "杂志风格版"
        assert data["data"][0]["image"] is None  # 尚无数据文件
        assert data["data"][0]["show_daily_english"] is False

    @pytest.mark.anyio
    async def test_get_templates_with_image(self, templates_app: FastAPI, tmp_path: Path) -> None:
        """验证存在数据时获取模板会返回图片 URL。"""
        data_dir = tmp_path / "cache" / "data"
        data_file = data_dir / "2026-02-10.json"
        data_file.write_text(json.dumps({
            "date": "2026-02-10",
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {"moyuren": "moyuren_20260210_072232.jpg"},
        }))

        with patch("app.api.v1.templates.today_business", return_value=date(2026, 2, 10)):
            async with AsyncClient(transport=ASGITransport(app=templates_app), base_url="http://test") as client:
                response = await client.get("/api/v1/templates")

        assert response.status_code == 200
        data = response.json()
        assert data["data"][0]["image"] == "http://localhost:8000/static/moyuren_20260210_072232.jpg"

    @pytest.mark.anyio
    async def test_get_templates_cache_header(self, templates_app: FastAPI) -> None:
        """验证模板端点返回正确的缓存头。"""
        with patch("app.api.v1.templates.today_business", return_value=date(2026, 2, 10)):
            async with AsyncClient(transport=ASGITransport(app=templates_app), base_url="http://test") as client:
                response = await client.get("/api/v1/templates")

        assert response.status_code == 200
        assert response.headers.get("cache-control") == "public, max-age=300, must-revalidate"

    @pytest.mark.anyio
    async def test_get_templates_reports_actual_daily_english_support(self, tmp_path: Path) -> None:
        """返回从各正式模板 meta 标签解析出的功能开关。"""
        app = FastAPI()
        app.include_router(templates_router)
        config = MagicMock()
        config.server.base_domain = "http://localhost:8000"
        config.paths.cache_dir = str(tmp_path / "cache")
        (tmp_path / "cache" / "data").mkdir(parents=True)
        templates_dir = Path(__file__).resolve().parents[3] / "templates"
        items = TemplateDiscovery().discover(str(templates_dir), TemplateRenderConfig())
        config.get_templates_config.return_value = MagicMock(items=items)
        app.state.config = config

        with patch("app.api.v1.templates.today_business", return_value=date(2026, 2, 10)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/v1/templates")

        assert response.status_code == 200
        by_name = {item["name"]: item for item in response.json()["data"]}
        assert by_name["moyuren"]["show_daily_english"] is False
        assert by_name["moyuren_cute"]["show_daily_english"] is True
