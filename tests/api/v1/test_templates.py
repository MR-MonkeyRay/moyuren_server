"""Tests for app/api/v1/templates.py - templates API endpoint."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.templates import router as templates_router
from app.core.config import TemplateRenderConfig
from app.services.template_discovery import TemplateDiscovery


@pytest.fixture
def templates_app(tmp_path: Path) -> FastAPI:
    """Create a test FastAPI app with templates router."""
    app = FastAPI()
    app.include_router(templates_router)

    config = MagicMock()
    config.server.base_domain = "http://localhost:8000"
    config.paths.cache_dir = str(tmp_path / "cache")

    # Create template items
    item1 = MagicMock()
    item1.name = "moyuren"
    item1.show_daily_english = False
    templates_config = MagicMock()
    templates_config.items = [item1]
    config.get_templates_config.return_value = templates_config

    # Create cache directories
    (tmp_path / "cache" / "data").mkdir(parents=True)

    app.state.config = config
    return app


class TestTemplatesAPI:
    """Tests for /api/v1/templates endpoint."""

    @pytest.mark.anyio
    async def test_get_templates(self, templates_app: FastAPI) -> None:
        """Test get templates returns template list."""
        with patch("app.api.v1.templates.today_business", return_value=date(2026, 2, 10)):
            async with AsyncClient(transport=ASGITransport(app=templates_app), base_url="http://test") as client:
                response = await client.get("/api/v1/templates")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) == 1
        assert data["data"][0]["name"] == "moyuren"
        assert data["data"][0]["image"] is None  # No data file yet
        assert data["data"][0]["show_daily_english"] is False

    @pytest.mark.anyio
    async def test_get_templates_with_image(self, templates_app: FastAPI, tmp_path: Path) -> None:
        """Test get templates returns image URL when data exists."""
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
        """Test templates endpoint returns correct cache headers."""
        with patch("app.api.v1.templates.today_business", return_value=date(2026, 2, 10)):
            async with AsyncClient(transport=ASGITransport(app=templates_app), base_url="http://test") as client:
                response = await client.get("/api/v1/templates")

        assert response.status_code == 200
        assert response.headers.get("cache-control") == "public, max-age=300, must-revalidate"

    @pytest.mark.anyio
    async def test_get_templates_reports_actual_daily_english_support(self, tmp_path: Path) -> None:
        """Expose the feature flag parsed from each production template's meta tags."""
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
