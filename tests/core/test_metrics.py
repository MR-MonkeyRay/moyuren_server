"""指标配置与端点测试，不依赖启动流程。"""

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY

from app import __version__
from app.core.config import MetricsConfig, load_config
from app.core.metrics import record_source_fetch, registry
from app.api.metrics import router as metrics_router


def test_metrics_disabled_by_default() -> None:
    """即使 YAML 省略该段，端点也需显式开启。"""
    assert MetricsConfig().enabled is False


def test_metrics_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """使用与其他配置段相同的嵌套环境变量约定。"""
    monkeypatch.setenv("MOYUREN_METRICS_ENABLED", "true")
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    assert load_config(str(config_path)).metrics.enabled is True


@pytest.mark.asyncio
async def test_metrics_endpoint_follows_config() -> None:
    """仅在启用指标时提供专用注册表的内容。"""
    app = FastAPI()
    app.include_router(metrics_router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 配置尚未加载（lifespan 之前）与显式禁用都返回 404
        assert (await client.get("/metrics")).status_code == 404
        app.state.config = SimpleNamespace(metrics=MetricsConfig())
        assert (await client.get("/metrics")).status_code == 404

        record_source_fetch("endpoint_test", "fresh")
        app.state.config = SimpleNamespace(metrics=MetricsConfig(enabled=True))
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"] == CONTENT_TYPE_LATEST
        assert 'moyuren_source_fetch_total{result="fresh",source="endpoint_test"}' in response.text
        assert f'moyuren_build_info{{version="{__version__}"}} 1.0' in response.text
        assert "process_cpu_seconds" not in response.text
        assert "/metrics" not in app.openapi()["paths"]


def test_registry_is_dedicated() -> None:
    """应用指标不污染默认的采集器注册表。"""
    assert registry is not REGISTRY
    assert REGISTRY.get_sample_value("moyuren_build_info", {"version": __version__}) is None
