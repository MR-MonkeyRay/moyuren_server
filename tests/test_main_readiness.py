"""Tests for the application readiness endpoint."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

import app.main as main_module


@pytest.fixture
def anyio_backend() -> str:
    """Run these endpoint tests on the project's asyncio backend."""
    return "asyncio"


class _BrowserManager:
    """Minimal browser manager stub exposing readiness state."""

    def __init__(self, is_ready: bool) -> None:
        self.is_ready = is_ready


def _build_app(cache_dir: Path | None, startup_state: str = "ready") -> FastAPI:
    """Create an isolated app exposing the production readiness handler."""
    app = FastAPI()
    app.add_api_route("/readyz", main_module.readyz, methods=["GET", "HEAD"])
    if cache_dir is not None:
        app.state.config = SimpleNamespace(
            paths=SimpleNamespace(cache_dir=str(cache_dir))
        )
    app.state.startup_state = startup_state
    return app


async def _get_readyz(app: FastAPI, method: str = "GET") -> Response:
    """Request readiness without running the production lifespan."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, "/readyz")


@pytest.mark.anyio
async def test_readyz_starting_returns_503(tmp_path: Path) -> None:
    """Startup work in progress should keep the service unready."""
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
    """Completed startup should report dependency readiness or degradation."""
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
    """An unexpected startup-task failure should allow degraded traffic."""
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
    """Missing startup and browser state should use safe defaults."""
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
    """The cache root and data directory are both required for readiness."""
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
    """Missing configuration should make every cache check unready."""
    app = _build_app(cache_dir=None)

    response = await _get_readyz(app)

    assert response.status_code == 503
    assert response.json()["checks"]["config"] is False
    assert response.json()["checks"]["cache_dir"] is False


@pytest.mark.anyio
async def test_readyz_supports_head(tmp_path: Path) -> None:
    """HEAD should expose readiness status without a response body."""
    cache_dir = tmp_path / "cache"
    (cache_dir / "data").mkdir(parents=True)
    app = _build_app(cache_dir)

    response = await _get_readyz(app, method="HEAD")

    assert response.status_code == 200
    assert response.content == b""
