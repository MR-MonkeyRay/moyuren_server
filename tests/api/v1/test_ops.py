"""Tests for app/api/v1/ops.py - operations API endpoints."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.ops import router as ops_router
from app.core.errors import ErrorCode


@pytest.fixture
def ops_app(tmp_path: Path) -> FastAPI:
    """Create a test FastAPI app with ops router."""
    app = FastAPI()
    app.include_router(ops_router)

    # Setup minimal app state
    config = MagicMock()
    config.ops.api_key = "test-secret-key"
    config.paths.cache_dir = str(tmp_path / "cache")

    # Create cache directories
    (tmp_path / "cache" / "data").mkdir(parents=True)
    (tmp_path / "cache" / "images").mkdir(parents=True)

    services = MagicMock()
    services.cache_cleaner.cleanup.return_value = {
        "deleted_files": 5,
        "freed_bytes": 1024000,
        "oldest_kept": "2026-01-15",
    }
    services.cache_cleaner.retain_days = 30

    app.state.config = config
    app.state.services = services
    return app


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Valid auth headers."""
    return {"Authorization": "Bearer test-secret-key"}


class TestOpsGenerate:
    """Tests for /api/v1/ops/generate endpoint."""

    @pytest.mark.anyio
    async def test_generate_without_auth(self, ops_app: FastAPI) -> None:
        """Test generate without API key returns 401."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.get("/api/v1/ops/generate")
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == ErrorCode.AUTH_UNAUTHORIZED.value

    @pytest.mark.anyio
    async def test_generate_with_invalid_key(self, ops_app: FastAPI) -> None:
        """Test generate with wrong API key returns 401."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/ops/generate",
                headers={"Authorization": "Bearer wrong-key"},
            )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_generate_with_valid_key(
        self, ops_app: FastAPI, auth_headers: dict, tmp_path: Path
    ) -> None:
        """Test generate with valid key triggers generation."""
        # Create data file that generate_and_save_image would create
        data_dir = tmp_path / "cache" / "data"
        today_str = "2026-02-10"
        data_file = data_dir / f"{today_str}.json"
        data_file.write_text(
            json.dumps({"images": {"moyuren": "moyuren_20260210_072232.jpg"}})
        )

        with (
            patch(
                "app.api.v1.ops.generate_and_save_image",
                new_callable=AsyncMock,
                return_value="moyuren_20260210_072232.jpg",
            ),
            patch("app.api.v1.ops.today_business", return_value=date(2026, 2, 10)),
        ):
            async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
                response = await client.get("/api/v1/ops/generate", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["filename"] == "moyuren_20260210_072232.jpg"
        assert response.headers.get("cache-control") == "no-store"

    @pytest.mark.anyio
    async def test_generate_busy(self, ops_app: FastAPI, auth_headers: dict) -> None:
        """Test generate returns 503 when busy."""
        from app.services.generator import GenerationBusyError

        with patch(
            "app.api.v1.ops.generate_and_save_image",
            new_callable=AsyncMock,
            side_effect=GenerationBusyError("busy"),
        ):
            async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
                response = await client.get("/api/v1/ops/generate", headers=auth_headers)

        assert response.status_code == 503
        assert response.headers.get("retry-after") == "10"


class TestOpsCacheClean:
    """Tests for /api/v1/ops/cache/clean endpoint."""

    @pytest.mark.anyio
    async def test_cache_clean_without_auth(self, ops_app: FastAPI) -> None:
        """Test cache clean without API key returns 401."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.get("/api/v1/ops/cache/clean")
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_cache_clean_with_valid_key(self, ops_app: FastAPI, auth_headers: dict) -> None:
        """Test cache clean with valid key returns cleanup stats."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.get("/api/v1/ops/cache/clean", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["deleted_files"] == 5
        assert response.headers.get("cache-control") == "no-store"

    @pytest.mark.anyio
    async def test_cache_clean_with_keep_days(self, ops_app: FastAPI, auth_headers: dict) -> None:
        """Test cache clean with custom keep_days."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/ops/cache/clean?keep_days=7", headers=auth_headers
            )

        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_cache_clean_invalid_keep_days(self, ops_app: FastAPI, auth_headers: dict) -> None:
        """Test cache clean with invalid keep_days returns 400."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/ops/cache/clean?keep_days=0", headers=auth_headers
            )

        assert response.status_code == 400


class TestHealthChecks:
    """Tests for health check endpoints."""

    @pytest.mark.anyio
    async def test_healthz(self) -> None:
        """Test healthz returns 200."""
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
