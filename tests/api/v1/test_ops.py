"""Tests for app/api/v1/ops.py - operations API endpoints."""

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.ops import GET_DEPRECATION_TIMESTAMP, _verify_api_key, router as ops_router
from app.core.errors import ErrorCode
from app.core.services import AppServices


@pytest.fixture
def ops_app(tmp_path: Path, mock_services: AppServices) -> FastAPI:
    """Create a test FastAPI app with ops router."""
    app = FastAPI()
    app.include_router(ops_router)

    # Setup minimal app state
    config = MagicMock()
    config.ops.api_key = "test-secret-key"
    config.paths.cache_dir = str(tmp_path / "cache")

    # Mock templates config for ops_generate
    from types import SimpleNamespace
    templates_config = MagicMock()
    templates_config.items = [SimpleNamespace(name="moyuren")]
    config.get_templates_config.return_value = templates_config

    # Create cache directories
    (tmp_path / "cache" / "data").mkdir(parents=True)
    (tmp_path / "cache" / "images").mkdir(parents=True)

    services = mock_services
    cache_cleaner = MagicMock()
    cache_cleaner.cleanup.return_value = {
        "deleted_files": 5,
        "freed_bytes": 1024000,
        "oldest_kept": "2026-01-15",
    }
    cache_cleaner.retain_days = 30
    services.cache_cleaner = cache_cleaner

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
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_generate_without_auth(self, ops_app: FastAPI, method: str) -> None:
        """Test generate without API key returns 401."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.request(method, "/api/v1/ops/generate")
        assert response.status_code == 401
        assert response.headers.get("deprecation") == (
            f"@{GET_DEPRECATION_TIMESTAMP}" if method == "GET" else None
        )
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
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_generate_with_valid_key(
        self, ops_app: FastAPI, auth_headers: dict, tmp_path: Path, method: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test generate with valid key triggers generation."""
        # Create data file that generate_and_save_image would create
        data_dir = tmp_path / "cache" / "data"
        today_str = "2026-02-10"
        data_file = data_dir / f"{today_str}.json"
        data_file.write_text(json.dumps({
            "date": today_str,
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {"moyuren": "moyuren_20260210_072232.jpg"},
        }))

        with (
            patch(
                "app.api.v1.ops.generate_and_save_image",
                new_callable=AsyncMock,
                return_value={"moyuren": "moyuren_20260210_072232.jpg"},
            ),
            patch("app.api.v1.ops.today_business", return_value=date(2026, 2, 10)),
        ):
            async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
                with caplog.at_level(logging.WARNING, logger="moyuren"):
                    response = await client.request(method, "/api/v1/ops/generate", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["results"] == {"moyuren": "moyuren_20260210_072232.jpg"}
        assert data["data"]["total_templates"] == 1
        assert response.headers.get("cache-control") == "no-store"
        assert response.headers.get("deprecation") == (
            f"@{GET_DEPRECATION_TIMESTAMP}" if method == "GET" else None
        )
        warnings = [record for record in caplog.records if "is deprecated" in record.message]
        assert len(warnings) == (1 if method == "GET" else 0)

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
        assert response.headers["deprecation"] == f"@{GET_DEPRECATION_TIMESTAMP}"

    @pytest.mark.anyio
    async def test_generate_failure_has_deprecation(
        self, ops_app: FastAPI, auth_headers: dict[str, str],
    ) -> None:
        """Test failed legacy generation still carries deprecation headers."""
        with patch("app.api.v1.ops.generate_and_save_image", side_effect=RuntimeError("failed")):
            async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
                response = await client.get("/api/v1/ops/generate", headers=auth_headers)
        assert response.status_code == 500
        assert response.headers["deprecation"] == f"@{GET_DEPRECATION_TIMESTAMP}"


class TestOpsCacheClean:
    """Tests for /api/v1/ops/cache/clean endpoint."""

    @pytest.mark.anyio
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_cache_clean_without_auth(self, ops_app: FastAPI, method: str) -> None:
        """Test cache clean without API key returns 401."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.request(method, "/api/v1/ops/cache/clean")
        assert response.status_code == 401
        assert response.headers.get("deprecation") == (
            f"@{GET_DEPRECATION_TIMESTAMP}" if method == "GET" else None
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_cache_clean_with_valid_key(
        self, ops_app: FastAPI, auth_headers: dict, method: str,
    ) -> None:
        """Test cache clean with valid key returns cleanup stats."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.request(method, "/api/v1/ops/cache/clean", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["deleted_files"] == 5
        assert response.headers.get("cache-control") == "no-store"
        assert response.headers.get("deprecation") == (
            f"@{GET_DEPRECATION_TIMESTAMP}" if method == "GET" else None
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_cache_clean_with_keep_days(
        self, ops_app: FastAPI, auth_headers: dict, method: str,
    ) -> None:
        """Test cache clean with custom keep_days."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.request(
                method, "/api/v1/ops/cache/clean?keep_days=7", headers=auth_headers
            )

        assert response.status_code == 200
        ops_app.state.services.cache_cleaner.cleanup.assert_called_once_with(retain_days=7)

    @pytest.mark.anyio
    async def test_cache_clean_invalid_keep_days(self, ops_app: FastAPI, auth_headers: dict) -> None:
        """Test cache clean with invalid keep_days returns 400."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/ops/cache/clean?keep_days=0", headers=auth_headers
            )

        assert response.status_code == 400
        assert response.headers["deprecation"] == f"@{GET_DEPRECATION_TIMESTAMP}"

    @pytest.mark.anyio
    async def test_cache_clean_validation_error_has_deprecation(
        self, ops_app: FastAPI, auth_headers: dict[str, str],
    ) -> None:
        """Test framework validation responses also carry the legacy GET header."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/ops/cache/clean?keep_days=invalid", headers=auth_headers,
            )
        assert response.status_code == 422
        assert response.headers["deprecation"] == f"@{GET_DEPRECATION_TIMESTAMP}"


class TestOpsAuth:
    """Tests for non-ASCII keys and disabled ops endpoints."""

    def test_deprecation_timestamp(self) -> None:
        """Test the structured date uses the specified UTC deprecation date."""
        assert datetime.fromtimestamp(GET_DEPRECATION_TIMESTAMP, tz=UTC) == datetime(2026, 10, 30, tzinfo=UTC)

    @pytest.mark.anyio
    @pytest.mark.parametrize("path", ["/generate", "/cache/clean"])
    async def test_non_ascii_token_returns_401(self, ops_app: FastAPI, path: str) -> None:
        """Test non-ASCII bearer tokens fail authentication without a server error."""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/ops{path}", headers={b"Authorization": b"Bearer wrong-\xe9"},
            )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == ErrorCode.AUTH_UNAUTHORIZED.value

    def test_matching_non_ascii_configured_key(self, ops_app: FastAPI) -> None:
        """Test matching Unicode credentials directly, avoiding header codec restrictions."""
        ops_app.state.config.ops.api_key = "密钥-é"
        request = MagicMock()
        request.app = ops_app
        request.headers = {"authorization": "Bearer 密钥-é"}
        _verify_api_key(request)

    @pytest.mark.anyio
    @pytest.mark.parametrize("method", ["GET", "POST"])
    @pytest.mark.parametrize("path", ["/generate", "/cache/clean"])
    async def test_empty_key_disables_ops(self, ops_app: FastAPI, method: str, path: str) -> None:
        """Test an empty key rejects every supported operation method."""
        ops_app.state.config.ops.api_key = ""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.request(
                method, f"/api/v1/ops{path}", headers={"Authorization": "Bearer test-secret-key"},
            )
        assert response.status_code == 401


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
