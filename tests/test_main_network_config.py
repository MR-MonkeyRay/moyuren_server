"""Tests for app lifespan network configuration propagation."""

import json
import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

import app.main as main_module


class _CachedService:
    async def get(self):
        return None

    async def close(self):
        return None


class _StockIndexService:
    def __init__(self, close_order):
        self._close_order = close_order

    async def close(self):
        self._close_order.append("stock")


class _TaskScheduler:
    def __init__(self, *args, **kwargs):
        self.add_hourly_job = MagicMock()
        self.add_daily_job = MagicMock()
        self.start = MagicMock()
        self.shutdown = MagicMock()


class _AsyncClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.aclose = AsyncMock()


def _fake_config(tmp_path):
    cache_dir = tmp_path / "cache"
    data_dir = cache_dir / "data"
    data_dir.mkdir(parents=True)
    (cache_dir / "images").mkdir(parents=True)
    today = date(2026, 2, 4)
    (data_dir / f"{today.isoformat()}.json").write_text(
        json.dumps(
            {"date": today.isoformat(), "updated_at": 1, "images": {"moyuren": "x.jpg"}}
        ),
        encoding="utf-8",
    )

    def get_source(cls):
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
        templates=SimpleNamespace(config=SimpleNamespace()),
        cache=SimpleNamespace(retain_days=30),
        scheduler=SimpleNamespace(mode="hourly", minute_of_hour=0, daily_times=[]),
        get_source=get_source,
        get_templates_config=lambda: SimpleNamespace(
            items=[SimpleNamespace(name="moyuren")]
        ),
    )


def _record_factory(records, name, instance=None):
    def factory(*args, **kwargs):
        records[name] = kwargs
        return instance if instance is not None else _CachedService()

    return factory


@pytest.mark.asyncio
async def test_lifespan_passes_network_proxy_to_httpx_services(monkeypatch, tmp_path):
    """App startup should pass proxy settings without declaring trust_env."""
    records = {}
    close_order = []
    config = _fake_config(tmp_path)
    logger = logging.getLogger("test.main.network")
    shared_client_holder = {}

    browser_manager = SimpleNamespace(
        configure=MagicMock(),
        warmup=AsyncMock(),
        shutdown=AsyncMock(side_effect=lambda: close_order.append("browser")),
    )

    def async_client_factory(**kwargs):
        client = _AsyncClient(**kwargs)
        shared_client_holder["client"] = client
        return client

    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "setup_logging", lambda *args, **kwargs: logger)
    monkeypatch.setattr(main_module, "init_timezones", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "today_business", lambda: date(2026, 2, 4))
    monkeypatch.setattr(main_module, "browser_manager", browser_manager)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", async_client_factory)
    monkeypatch.setattr(main_module, "TaskScheduler", _TaskScheduler)
    monkeypatch.setattr(main_module, "generate_and_save_image", AsyncMock())
    monkeypatch.setattr(
        main_module, "CachedDataFetcher", _record_factory(records, "CachedDataFetcher")
    )
    monkeypatch.setattr(
        main_module,
        "CachedHolidayService",
        _record_factory(records, "CachedHolidayService"),
    )
    monkeypatch.setattr(
        main_module,
        "CachedFunContentService",
        _record_factory(records, "CachedFunContentService"),
    )
    monkeypatch.setattr(
        main_module, "CachedKfcService", _record_factory(records, "CachedKfcService")
    )
    monkeypatch.setattr(
        main_module,
        "StockIndexService",
        _record_factory(records, "StockIndexService", _StockIndexService(close_order)),
    )
    monkeypatch.setattr(
        main_module,
        "CachedGoldPriceService",
        _record_factory(records, "CachedGoldPriceService"),
    )
    monkeypatch.setattr(
        main_module,
        "build_dict_backend",
        _record_factory(records, "build_dict_backend", object()),
    )
    monkeypatch.setattr(
        main_module,
        "CachedDailyEnglishService",
        _record_factory(records, "CachedDailyEnglishService"),
    )
    monkeypatch.setattr(
        main_module, "ImageRenderer", _record_factory(records, "ImageRenderer")
    )
    monkeypatch.setattr(
        main_module,
        "CacheCleaner",
        lambda *args, **kwargs: SimpleNamespace(cleanup=lambda: {}),
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
