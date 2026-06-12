"""Tests for standalone script network and browser lifecycle wiring."""

import importlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FailingDataFetcher:
    """DataFetcher double that fails after all services are constructed."""

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def fetch_all(self):
        raise RuntimeError(
            "failed https://user:pass@example.com/path?token=secret#frag"
        )


class _AsyncService:
    async def fetch_holidays(self):
        return []

    async def fetch_content(self, target_date):
        return {"title": "title", "content": "content"}

    async def fetch_kfc_copy(self):
        return "copy"

    async def fetch_gold_price(self):
        return {"today_price": "1"}

    async def ensure_ready(self):
        return None

    async def fetch_daily_word(self):
        return None


class _ImageRenderer:
    async def render(self, *args, **kwargs):
        return "moyuren.jpg"


class _DataComputer:
    def compute(self, raw_data):
        return {"date": {}, "history": {}, "news_list": []}


def _fake_config(tmp_path):
    proxy_url = "http://user:pass@proxy.example:8080"

    def get_source(cls):
        name = cls.__name__
        if name == "HolidaySource":
            return SimpleNamespace(timeout_sec=5)
        if name == "DailyEnglishSource":
            return SimpleNamespace(enabled=True, backend=SimpleNamespace())
        return SimpleNamespace(enabled=True)

    return SimpleNamespace(
        logging=SimpleNamespace(),
        timezone=SimpleNamespace(business="Asia/Shanghai", display="local"),
        paths=SimpleNamespace(cache_dir=str(tmp_path / "cache")),
        network=SimpleNamespace(
            proxy_url=proxy_url,
            ghproxy_urls=["https://mirror.example/"],
        ),
        templates=SimpleNamespace(config=SimpleNamespace()),
        get_source=get_source,
        get_templates_config=lambda: SimpleNamespace(
            items=[SimpleNamespace(name="moyuren")]
        ),
    )


def _install_common_fakes(monkeypatch, module, tmp_path):
    records = {}
    order = []
    logger = logging.getLogger(f"test.{module.__name__}")
    config = _fake_config(tmp_path)

    browser_manager = SimpleNamespace(
        configure=MagicMock(),
        shutdown=AsyncMock(side_effect=lambda: order.append("browser")),
    )

    stock_service = SimpleNamespace(
        fetch_indices=AsyncMock(return_value={"items": []}),
        close=AsyncMock(side_effect=lambda: order.append("stock")),
    )

    def record_factory(name, instance=None):
        def factory(*args, **kwargs):
            records[name] = kwargs
            return instance if instance is not None else _AsyncService()

        return factory

    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "setup_logging", lambda *args, **kwargs: logger)
    monkeypatch.setattr(module, "init_timezones", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "browser_manager", browser_manager)
    monkeypatch.setattr(module, "DataComputer", lambda *args, **kwargs: _DataComputer())
    monkeypatch.setattr(
        module, "DataFetcher", record_factory("DataFetcher", _FailingDataFetcher())
    )
    monkeypatch.setattr(module, "HolidayService", record_factory("HolidayService"))
    monkeypatch.setattr(
        module, "FunContentService", record_factory("FunContentService")
    )
    monkeypatch.setattr(
        module, "StockIndexService", record_factory("StockIndexService", stock_service)
    )
    monkeypatch.setattr(module, "GoldPriceService", record_factory("GoldPriceService"))
    monkeypatch.setattr(
        module, "DailyEnglishService", record_factory("DailyEnglishService")
    )
    monkeypatch.setattr(
        module, "ImageRenderer", record_factory("ImageRenderer", _ImageRenderer())
    )
    monkeypatch.setattr(
        module, "build_dict_backend", record_factory("build_dict_backend", object())
    )
    if hasattr(module, "KfcService"):
        monkeypatch.setattr(module, "KfcService", record_factory("KfcService"))
    if hasattr(module, "get_display_timezone"):
        monkeypatch.setattr(module, "get_display_timezone", lambda: None)

    return config, logger, browser_manager, records, order


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name, raises_system_exit",
    [
        ("scripts.render_once", False),
        ("scripts.publish_static", True),
        ("scripts.render_test_scenarios", False),
    ],
)
async def test_scripts_configure_browser_pass_proxy_and_cleanup(
    monkeypatch, tmp_path, module_name, raises_system_exit
):
    """Standalone scripts should pass proxy settings without declaring trust_env and cleanup browser resources."""
    module = importlib.import_module(module_name)
    config, logger, browser_manager, records, order = _install_common_fakes(
        monkeypatch, module, tmp_path
    )

    if hasattr(module, "parse_args"):
        monkeypatch.setattr(
            module,
            "parse_args",
            lambda: SimpleNamespace(
                output=str(tmp_path / "out"),
                base_url="https://example.com",
                list=False,
                all=False,
                scenario=None,
            ),
        )

    if raises_system_exit:
        with pytest.raises(SystemExit) as exc_info:
            await module.main()
        assert exc_info.value.code == 10
    else:
        with pytest.raises(RuntimeError):
            await module.main()

    browser_manager.configure.assert_called_once_with(
        logger, proxy_url=config.network.proxy_url
    )
    assert order == ["stock", "browser"]

    expected = {
        "DataFetcher",
        "HolidayService",
        "FunContentService",
        "StockIndexService",
        "GoldPriceService",
        "build_dict_backend",
        "DailyEnglishService",
        "ImageRenderer",
    }
    if hasattr(module, "KfcService"):
        expected.add("KfcService")

    assert expected.issubset(records)
    for name in expected:
        assert records[name]["proxy_url"] == config.network.proxy_url
        assert "trust_env" not in records[name]
