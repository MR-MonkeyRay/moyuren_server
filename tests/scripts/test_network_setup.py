"""Tests for standalone script network and browser lifecycle wiring."""

import importlib
import logging
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.config import TextLimitsConfig


class _FailingDataFetcher:
    """DataFetcher double that fails after all services are constructed."""

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def fetch_all(self):
        raise RuntimeError(
            "failed https://user:pass@example.com/path?token=secret#frag"
        )


class _SuccessfulDataFetcher:
    async def fetch_all(self) -> dict[str, Any]:
        return {}


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
    def __init__(self) -> None:
        self.raw_data: dict[str, Any] | None = None

    def compute(
        self, raw_data: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        self.raw_data = raw_data
        return {"date": {}, "history": {}, "news_list": []}


def _fake_config(
    tmp_path: Path, *, holiday_enabled: bool = True
) -> SimpleNamespace:
    proxy_url = "http://user:pass@proxy.example:8080"

    def get_source(cls: type[Any]) -> SimpleNamespace | None:
        name = cls.__name__
        if name == "HolidaySource":
            return SimpleNamespace(timeout_sec=5) if holiday_enabled else None
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
        instances=SimpleNamespace(list_url=None, urls=None),
        templates=SimpleNamespace(config=SimpleNamespace(text_limits=TextLimitsConfig())),
        get_source=get_source,
        get_templates_config=lambda: SimpleNamespace(
            items=[SimpleNamespace(name="moyuren")]
        ),
    )


def _install_common_fakes(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    tmp_path: Path,
    *,
    holiday_enabled: bool = True,
) -> tuple[SimpleNamespace, logging.Logger, SimpleNamespace, dict[str, dict[str, Any]], list[str]]:
    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    logger = logging.getLogger(f"test.{module.__name__}")
    config = _fake_config(tmp_path, holiday_enabled=holiday_enabled)

    browser_manager = SimpleNamespace(
        configure=MagicMock(),
        shutdown=AsyncMock(side_effect=lambda: order.append("browser")),
    )

    stock_service = SimpleNamespace(
        fetch_indices=AsyncMock(return_value={"items": []}),
        close=AsyncMock(side_effect=lambda: order.append("stock")),
    )

    def record_factory(
        name: str, instance: Any = None
    ) -> Callable[..., Any]:
        def factory(*args: Any, **kwargs: Any) -> Any:
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name, raises_system_exit",
    [
        ("scripts.render_once", False),
        ("scripts.publish_static", True),
    ],
)
async def test_scripts_skip_holiday_service_when_source_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
    raises_system_exit: bool,
) -> None:
    """Disabled holiday configuration should not construct or call the service."""
    module = importlib.import_module(module_name)
    _, _, _, records, _ = _install_common_fakes(
        monkeypatch, module, tmp_path, holiday_enabled=False
    )

    if hasattr(module, "parse_args"):
        monkeypatch.setattr(
            module,
            "parse_args",
            lambda: SimpleNamespace(
                output=str(tmp_path / "out"),
                base_url="https://example.com",
            ),
        )

    if raises_system_exit:
        with pytest.raises(SystemExit) as exc_info:
            await module.main()
        assert exc_info.value.code == 10
    else:
        with pytest.raises(RuntimeError):
            await module.main()

    assert "HolidayService" not in records


@pytest.mark.asyncio
async def test_render_once_generates_with_empty_holidays_when_source_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Disabled holidays should flow through generation as an empty list."""
    module = importlib.import_module("scripts.render_once")
    _install_common_fakes(monkeypatch, module, tmp_path, holiday_enabled=False)
    data_computer = _DataComputer()
    monkeypatch.setattr(
        module, "DataFetcher", lambda *args, **kwargs: _SuccessfulDataFetcher()
    )
    monkeypatch.setattr(module, "DataComputer", lambda **kwargs: data_computer)

    await module.main()

    assert data_computer.raw_data is not None
    assert data_computer.raw_data["holidays"] == []


@pytest.mark.asyncio
async def test_publish_static_generates_with_empty_holidays_when_source_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Static publishing should complete with an empty holiday list when disabled."""
    module = importlib.import_module("scripts.publish_static")
    _, _, _, records, _ = _install_common_fakes(
        monkeypatch, module, tmp_path, holiday_enabled=False
    )
    data_computer = _DataComputer()
    image_path = tmp_path / "cache" / "images" / "moyuren.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image")
    monkeypatch.setattr(
        module, "parse_args", lambda: SimpleNamespace(
            output=str(tmp_path / "out"), base_url="https://example.com"
        )
    )
    monkeypatch.setattr(
        module, "DataFetcher", lambda *args, **kwargs: _SuccessfulDataFetcher()
    )
    monkeypatch.setattr(module, "DataComputer", lambda **kwargs: data_computer)
    monkeypatch.setattr(module, "DailyDataFile", _PassthroughDailyDataFile)

    await module.main()

    assert "HolidayService" not in records
    assert data_computer.raw_data is not None
    assert data_computer.raw_data["holidays"] == []


class _PassthroughDailyDataFile:
    """Minimal DailyDataFile double for exercising the script artifact flow."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    @classmethod
    def model_validate(
        cls, payload: dict[str, Any]
    ) -> "_PassthroughDailyDataFile":
        return cls(payload)

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        return self.payload

    def dump_json(self) -> str:
        return "{}"
