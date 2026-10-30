"""app/api/v1/ops.py 运维 API 端点测试。"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.ops import _verify_api_key, router as ops_router
from app.core.errors import ErrorCode
from app.core.services import AppServices


@pytest.fixture
def ops_app(tmp_path: Path, mock_services: AppServices) -> FastAPI:
    """创建包含运维路由的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(ops_router)

    # 设置最小应用状态
    config = MagicMock()
    config.ops.api_key = "test-secret-key"
    config.paths.cache_dir = str(tmp_path / "cache")

    # 为 ops_generate 模拟模板配置
    from types import SimpleNamespace
    templates_config = MagicMock()
    templates_config.items = [SimpleNamespace(name="moyuren")]
    config.get_templates_config.return_value = templates_config

    # 创建缓存目录
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
    """有效的鉴权请求头。"""
    return {"Authorization": "Bearer test-secret-key"}


class TestOpsGenerate:
    """/api/v1/ops/generate 端点测试。"""

    @pytest.mark.anyio
    async def test_generate_without_auth(self, ops_app: FastAPI) -> None:
        """未提供 API 密钥时，生成接口返回 401。"""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post("/api/v1/ops/generate")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["www-authenticate"] == "Bearer"
        data = response.json()
        assert data["error"]["code"] == ErrorCode.AUTH_UNAUTHORIZED.value

    @pytest.mark.anyio
    async def test_generate_with_invalid_key(self, ops_app: FastAPI) -> None:
        """API 密钥错误时，生成接口返回 401。"""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/ops/generate",
                headers={"Authorization": "Bearer wrong-key"},
            )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_generate_with_valid_key(
        self, ops_app: FastAPI, auth_headers: dict, tmp_path: Path,
    ) -> None:
        """使用有效密钥触发生成。"""
        # 创建 generate_and_save_image 原本会生成的数据文件
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
                response = await client.post("/api/v1/ops/generate", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["results"] == {"moyuren": "moyuren_20260210_072232.jpg"}
        assert data["data"]["total_templates"] == 1
        assert response.headers.get("cache-control") == "no-store"

    @pytest.mark.anyio
    async def test_generate_busy(self, ops_app: FastAPI, auth_headers: dict) -> None:
        """生成任务繁忙时返回 503。"""
        from app.services.generator import GenerationBusyError

        with patch(
            "app.api.v1.ops.generate_and_save_image",
            new_callable=AsyncMock,
            side_effect=GenerationBusyError("busy"),
        ):
            async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
                response = await client.post("/api/v1/ops/generate", headers=auth_headers)

        assert response.status_code == 503
        assert response.headers.get("retry-after") == "10"
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.anyio
    async def test_generate_failure(
        self, ops_app: FastAPI, auth_headers: dict[str, str],
    ) -> None:
        """生成失败时保留错误响应和 no-store 策略。"""
        with patch("app.api.v1.ops.generate_and_save_image", side_effect=RuntimeError("failed")):
            async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
                response = await client.post("/api/v1/ops/generate", headers=auth_headers)
        assert response.status_code == 500
        assert response.json()["error"]["code"] == ErrorCode.GENERATION_FAILED.value
        assert response.headers["cache-control"] == "no-store"


class TestOpsCacheClean:
    """/api/v1/ops/cache/clean 端点测试。"""

    @pytest.mark.anyio
    async def test_cache_clean_without_auth(self, ops_app: FastAPI) -> None:
        """未提供 API 密钥时，缓存清理接口返回 401。"""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post("/api/v1/ops/cache/clean")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.anyio
    async def test_cache_clean_with_valid_key(
        self, ops_app: FastAPI, auth_headers: dict,
    ) -> None:
        """使用有效密钥清理缓存并返回统计信息。"""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post("/api/v1/ops/cache/clean", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["deleted_files"] == 5
        assert response.headers.get("cache-control") == "no-store"

    @pytest.mark.anyio
    async def test_cache_clean_with_keep_days(
        self, ops_app: FastAPI, auth_headers: dict,
    ) -> None:
        """使用自定义 keep_days 清理缓存。"""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/ops/cache/clean?keep_days=7", headers=auth_headers
            )

        assert response.status_code == 200
        ops_app.state.services.cache_cleaner.cleanup.assert_called_once_with(retain_days=7)

    @pytest.mark.anyio
    async def test_cache_clean_invalid_keep_days(self, ops_app: FastAPI, auth_headers: dict) -> None:
        """keep_days 无效时，缓存清理接口返回 400。"""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/ops/cache/clean?keep_days=0", headers=auth_headers
            )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == ErrorCode.API_INVALID_PARAMETER.value
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.anyio
    async def test_cache_clean_validation_error(
        self, ops_app: FastAPI, auth_headers: dict[str, str],
    ) -> None:
        """请求校验拒绝格式错误的 keep_days。"""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/ops/cache/clean?keep_days=invalid", headers=auth_headers,
            )
        assert response.status_code == 422


class TestOpsMethods:
    """修改状态的操作绝不能通过 GET 请求执行。"""

    @pytest.mark.anyio
    @pytest.mark.parametrize("path", ["/generate", "/cache/clean"])
    @pytest.mark.parametrize("authenticated", [False, True])
    async def test_get_rejected_without_side_effects(
        self, ops_app: FastAPI, auth_headers: dict[str, str], path: str, authenticated: bool,
    ) -> None:
        with patch("app.api.v1.ops.generate_and_save_image", new_callable=AsyncMock) as generate:
            async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
                response = await client.get(
                    f"/api/v1/ops{path}", headers=auth_headers if authenticated else {},
                )

        assert response.status_code == 405
        assert response.headers["allow"] == "POST"
        generate.assert_not_called()
        ops_app.state.services.cache_cleaner.cleanup.assert_not_called()



class TestOpsAuth:
    """非 ASCII 密钥及运维端点禁用行为测试。"""

    @pytest.mark.anyio
    @pytest.mark.parametrize("path", ["/generate", "/cache/clean"])
    async def test_non_ascii_token_returns_401(self, ops_app: FastAPI, path: str) -> None:
        """非 ASCII Bearer 令牌鉴权失败，但不触发服务器错误。"""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/ops{path}", headers={b"Authorization": b"Bearer wrong-\xe9"},
            )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == ErrorCode.AUTH_UNAUTHORIZED.value

    def test_matching_non_ascii_configured_key(self, ops_app: FastAPI) -> None:
        """直接测试匹配的 Unicode 凭据，避开请求头编码限制。"""
        ops_app.state.config.ops.api_key = "密钥-é"
        request = MagicMock()
        request.app = ops_app
        request.headers = {"authorization": "Bearer 密钥-é"}
        _verify_api_key(request)

    @pytest.mark.anyio
    @pytest.mark.parametrize("path", ["/generate", "/cache/clean"])
    async def test_empty_key_disables_ops(self, ops_app: FastAPI, path: str) -> None:
        """密钥为空时，拒绝所有支持的操作方法。"""
        ops_app.state.config.ops.api_key = ""
        async with AsyncClient(transport=ASGITransport(app=ops_app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/ops{path}", headers={"Authorization": "Bearer test-secret-key"},
            )
        assert response.status_code == 401


class TestHealthChecks:
    """健康检查端点测试。"""

    @pytest.mark.anyio
    async def test_healthz(self) -> None:
        """healthz 返回 200。"""
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
