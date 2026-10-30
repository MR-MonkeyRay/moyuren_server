"""应用就绪检查端点测试。"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

import app.main as main_module


@pytest.fixture
def anyio_backend() -> str:
    """使用项目的 asyncio 后端运行端点测试。"""
    return "asyncio"


class _BrowserManager:
    """仅提供就绪状态的最简浏览器管理器桩。"""

    def __init__(self, is_ready: bool) -> None:
        self.is_ready = is_ready


def _build_app(cache_dir: Path | None, startup_state: str = "ready") -> FastAPI:
    """创建使用生产就绪检查处理器的独立应用。"""
    app = FastAPI()
    app.add_api_route("/readyz", main_module.readyz, methods=["GET", "HEAD"])
    if cache_dir is not None:
        app.state.config = SimpleNamespace(
            paths=SimpleNamespace(cache_dir=str(cache_dir))
        )
    app.state.startup_state = startup_state
    return app


async def _get_readyz(app: FastAPI, method: str = "GET") -> Response:
    """不运行生产生命周期，直接请求就绪检查。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, "/readyz")


@pytest.mark.anyio
async def test_readyz_starting_returns_503(tmp_path: Path) -> None:
    """启动任务进行中时，服务应保持未就绪。"""
    cache_dir = tmp_path / "cache"
    (cache_dir / "data").mkdir(parents=True)
    app = _build_app(cache_dir, startup_state="starting")
    app.state.browser_manager = _BrowserManager(is_ready=True)

    response = await _get_readyz(app)

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "config": True,
            "cache_dir": True,
            "startup": "starting",
            "today_data": False,
            "browser": True,
        },
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("today_data", "browser_ready", "expected_status"),
    [
        (True, True, "ready"),
        (False, True, "degraded"),
        (True, False, "degraded"),
        (False, False, "degraded"),
    ],
)
async def test_readyz_ready_or_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    today_data: bool,
    browser_ready: bool,
    expected_status: str,
) -> None:
    """启动完成后，应根据依赖是否就绪报告就绪或降级状态。"""
    business_date = date(2026, 2, 10)
    cache_dir = tmp_path / "cache"
    data_dir = cache_dir / "data"
    data_dir.mkdir(parents=True)
    if today_data:
        (data_dir / f"{business_date.isoformat()}.json").touch()

    monkeypatch.setattr(main_module, "today_business", lambda: business_date)
    app = _build_app(cache_dir)
    app.state.browser_manager = _BrowserManager(is_ready=browser_ready)

    response = await _get_readyz(app)

    assert response.status_code == 200
    assert response.json() == {
        "status": expected_status,
        "checks": {
            "config": True,
            "cache_dir": True,
            "startup": "ready",
            "today_data": today_data,
            "browser": browser_ready,
        },
    }


@pytest.mark.anyio
async def test_readyz_failed_startup_is_degraded(tmp_path: Path) -> None:
    """启动任务意外失败时，应允许以降级状态接收请求。"""
    cache_dir = tmp_path / "cache"
    (cache_dir / "data").mkdir(parents=True)
    app = _build_app(cache_dir, startup_state="failed")
    app.state.browser_manager = _BrowserManager(is_ready=False)

    response = await _get_readyz(app)

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["startup"] == "failed"


@pytest.mark.anyio
async def test_readyz_defaults_to_starting_without_startup_state(
    tmp_path: Path,
) -> None:
    """缺少启动和浏览器状态时，应使用安全默认值。"""
    cache_dir = tmp_path / "cache"
    (cache_dir / "data").mkdir(parents=True)
    app = _build_app(cache_dir)
    del app.state.startup_state

    response = await _get_readyz(app)

    assert response.status_code == 503
    assert response.json()["checks"]["startup"] == "starting"
    assert response.json()["checks"]["browser"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("missing_path", ["cache", "data"])
async def test_readyz_missing_cache_path_returns_503(
    tmp_path: Path, missing_path: str
) -> None:
    """缓存根目录和数据目录均存在时，服务才能就绪。"""
    cache_dir = tmp_path / "cache"
    if missing_path == "data":
        cache_dir.mkdir()
    app = _build_app(cache_dir)

    response = await _get_readyz(app)

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["cache_dir"] is False


@pytest.mark.anyio
async def test_readyz_missing_config_returns_503(tmp_path: Path) -> None:
    """缺少配置时，所有缓存检查都应显示未就绪。"""
    app = _build_app(cache_dir=None)

    response = await _get_readyz(app)

    assert response.status_code == 503
    assert response.json()["checks"]["config"] is False
    assert response.json()["checks"]["cache_dir"] is False


@pytest.mark.anyio
async def test_readyz_supports_head(tmp_path: Path) -> None:
    """HEAD 应返回就绪状态，但不返回响应体。"""
    cache_dir = tmp_path / "cache"
    (cache_dir / "data").mkdir(parents=True)
    app = _build_app(cache_dir)

    response = await _get_readyz(app, method="HEAD")

    assert response.status_code == 200
    assert response.content == b""
