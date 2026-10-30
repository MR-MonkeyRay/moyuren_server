"""Tests for app lifespan network configuration propagation."""

import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

import app.main as main_module
import app.core.bootstrap as bootstrap_module
from app.core.config import TextLimitsConfig


class _CachedService:
    async def get(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _StockIndexService:
    def __init__(self, close_order: list[str]) -> None:
        self._close_order = close_order

    async def close(self) -> None:
        self._close_order.append("stock")


class _TaskScheduler:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.add_hourly_job = MagicMock()
        self.add_daily_job = MagicMock()
        self.start = MagicMock()
        self.shutdown = MagicMock()


class _AsyncClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.aclose = AsyncMock()


def _fake_config(tmp_path: Path) -> SimpleNamespace:
    cache_dir = tmp_path / "cache"
    data_dir = cache_dir / "data"
    data_dir.mkdir(parents=True)
    (cache_dir / "images").mkdir(parents=True)
    today = date(2026, 2, 4)
    (data_dir / f"{today.isoformat()}.json").write_text(
        json.dumps(
            {
                "date": today.isoformat(),
                "updated": "2026/02/04 00:00:00",
                "updated_at": 1,
                "images": {"moyuren": "x.jpg"},
            }
        ),
        encoding="utf-8",
    )

    def get_source(cls: type[object]) -> SimpleNamespace:
        name = cls.__name__
        if name == "HolidaySource":
            return SimpleNamespace(timeout_sec=5)
        if name == "DailyEnglishSource":
            return SimpleNamespace(enabled=True, backend=SimpleNamespace())
        return SimpleNamespace(enabled=True)

    return SimpleNamespace(
        logging=SimpleNamespace(file=str(tmp_path / "logs" / "app.log")),
        timezone=SimpleNamespace(business="Asia/Shanghai", display="local"),
        paths=SimpleNamespace(cache_dir=str(cache_dir)),
        network=SimpleNamespace(
            proxy_url="http://user:pass@proxy.example:8080",
            ghproxy_urls=["https://mirror.example/"],
        ),
        instances=SimpleNamespace(list_url=None, urls=None),
        templates=SimpleNamespace(config=SimpleNamespace(text_limits=TextLimitsConfig())),
        cache=SimpleNamespace(retain_days=30, max_images_per_template_per_day=3),
        scheduler=SimpleNamespace(mode="hourly", minute_of_hour=0, daily_times=[]),
        get_source=get_source,
        get_templates_config=lambda: SimpleNamespace(
            items=[SimpleNamespace(name="moyuren")]
        ),
    )


def _record_factory(
    records: dict[str, dict[str, Any]], name: str, instance: object | None = None
) -> Callable[..., object]:
    def factory(*args: object, **kwargs: Any) -> object:
        records[name] = kwargs
        return instance if instance is not None else _CachedService()

    return factory


@pytest.mark.asyncio
async def test_lifespan_passes_network_proxy_to_httpx_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """App startup should pass proxy settings without declaring trust_env."""
    records: dict[str, dict[str, Any]] = {}
    close_order: list[str] = []
    config = _fake_config(tmp_path)
    logger = logging.getLogger("test.main.network")
    shared_client_holder: dict[str, _AsyncClient] = {}

    browser_manager = SimpleNamespace(
        configure=MagicMock(),
        warmup=AsyncMock(),
        shutdown=AsyncMock(side_effect=lambda: close_order.append("browser")),
    )

    def async_client_factory(**kwargs: Any) -> _AsyncClient:
        client = _AsyncClient(**kwargs)
        shared_client_holder["client"] = client
        return client

    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "setup_logging", lambda *args, **kwargs: logger)
    monkeypatch.setattr(main_module, "init_timezones", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "today_business", lambda: date(2026, 2, 4))
    monkeypatch.setattr(bootstrap_module, "today_business", lambda: date(2026, 2, 4))
    monkeypatch.setattr(main_module, "browser_manager", browser_manager)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", async_client_factory)
    monkeypatch.setattr(main_module, "TaskScheduler", _TaskScheduler)
    monkeypatch.setattr(main_module, "generate_and_save_image", AsyncMock())
    monkeypatch.setattr(
        bootstrap_module, "CachedDataFetcher", _record_factory(records, "CachedDataFetcher")
    )
    monkeypatch.setattr(
        bootstrap_module,
        "CachedHolidayService",
        _record_factory(records, "CachedHolidayService"),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "CachedFunContentService",
        _record_factory(records, "CachedFunContentService"),
    )
    monkeypatch.setattr(
        bootstrap_module, "CachedKfcService", _record_factory(records, "CachedKfcService")
    )
    monkeypatch.setattr(
        bootstrap_module,
        "StockIndexService",
        _record_factory(records, "StockIndexService", _StockIndexService(close_order)),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "CachedGoldPriceService",
        _record_factory(records, "CachedGoldPriceService"),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "build_dict_backend",
        _record_factory(records, "build_dict_backend", object()),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "CachedDailyEnglishService",
        _record_factory(records, "CachedDailyEnglishService"),
    )
    monkeypatch.setattr(
        bootstrap_module, "ImageRenderer", _record_factory(records, "ImageRenderer")
    )
    monkeypatch.setattr(
        bootstrap_module,
        "CacheCleaner",
        lambda *args, **kwargs: SimpleNamespace(cleanup=lambda: {}),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "ImageGenerator",
        lambda: SimpleNamespace(shutdown=AsyncMock()),
    )

    app = FastAPI()
    async with main_module.lifespan(app):
        assert "trust_env" not in shared_client_holder["client"].kwargs
        assert (
            shared_client_holder["client"].kwargs["proxy"] == config.network.proxy_url
        )

    browser_manager.configure.assert_called_once_with(
        logger, proxy_url=config.network.proxy_url
    )
    assert close_order == ["stock", "browser"]

    expected = {
        "CachedDataFetcher",
        "CachedHolidayService",
        "CachedFunContentService",
        "CachedKfcService",
        "StockIndexService",
        "CachedGoldPriceService",
        "build_dict_backend",
        "CachedDailyEnglishService",
        "ImageRenderer",
    }
    assert expected.issubset(records)
    for name in expected:
        assert records[name]["proxy_url"] == config.network.proxy_url
        assert "trust_env" not in records[name]
