"""全局测试 fixture 与配置。"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.services import AppServices
from app.services.generator import ImageGenerator


@pytest.fixture
def mock_services() -> AppServices:
    """返回包含相互隔离的测试替身的服务容器。"""
    return AppServices(
        data_fetcher=None,
        holiday_service=MagicMock(),
        fun_content_service=None,
        kfc_service=None,
        stock_index_service=None,
        gold_price_service=None,
        daily_english_service=None,
        image_renderer=MagicMock(),
        data_computer=MagicMock(),
        cache_cleaner=MagicMock(),
        generator=ImageGenerator(),
    )


# --- 基础工具 ---


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """返回测试数据目录的路径。"""
    return Path(__file__).parent / "data"


@pytest.fixture
def load_json(test_data_dir: Path) -> Callable[[str], dict[str, Any]]:
    """从文件加载 JSON 测试数据。"""

    def _loader(rel_path: str) -> dict[str, Any]:
        with open(test_data_dir / rel_path, encoding="utf-8") as f:
            return json.load(f)

    return _loader


# --- 环境配置 ---


@pytest.fixture(autouse=True)
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """覆盖环境变量，避免环境污染。"""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("TZ", "Asia/Shanghai")


# --- 临时目录 ---


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """创建临时缓存目录和静态文件目录。"""
    (tmp_path / "cache").mkdir(exist_ok=True)
    (tmp_path / "static").mkdir(exist_ok=True)
    return tmp_path


# --- 日志记录器 ---


@pytest.fixture
def logger() -> logging.Logger:
    """返回测试用日志记录器。"""
    return logging.getLogger("test")


# --- 时间 fixture ---


@pytest.fixture
def fixed_datetime() -> datetime:
    """返回测试用固定时间（2026-02-04 10:00:00 CST）。"""
    return datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def fixed_thursday() -> datetime:
    """返回 KFC 测试用的固定周四时间（2026-02-05 为周四）。"""
    return datetime(2026, 2, 5, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))


# --- 浏览器 mock ---


@pytest.fixture
def mock_browser_page() -> AsyncMock:
    """创建 Playwright Page 对象的 mock。"""
    page = AsyncMock()
    page.set_content = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"fake_image_bytes")
    page.close = AsyncMock()
    return page


@pytest.fixture
def mock_browser_manager(mock_browser_page: AsyncMock) -> MagicMock:
    """创建浏览器管理器的 mock。"""
    manager = MagicMock()
    manager.create_page = AsyncMock(return_value=mock_browser_page)
    return manager


# --- HTTP 客户端 mock ---


@pytest.fixture
def mock_httpx_response() -> Callable[[dict[str, Any], int], MagicMock]:
    """创建 httpx 响应的 mock。"""

    def _create_response(json_data: dict[str, Any], status_code: int = 200) -> MagicMock:
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = json_data
        response.raise_for_status = MagicMock()
        if status_code >= 400:
            from httpx import HTTPStatusError

            response.raise_for_status.side_effect = HTTPStatusError(
                message=f"HTTP {status_code}", request=MagicMock(), response=response
            )
        return response

    return _create_response


# --- 样例数据 fixture ---


@pytest.fixture
def sample_news_response() -> dict[str, Any]:
    """60s 新闻 API 响应样例。"""
    return {
        "code": 200,
        "data": {
            "date": "2026年2月4日",
            "news": [
                "今日新闻1",
                "今日新闻2",
                "今日新闻3",
            ],
            "tip": "每天60秒读懂世界",
            "updated": "2026-02-04T06:00:00+08:00",
        },
    }


@pytest.fixture
def sample_stock_data() -> dict[str, Any]:
    """股票指数数据样例。"""
    return {
        "items": [
            {
                "name": "上证指数",
                "price": 3200.50,
                "change_pct": 1.25,
                "trend": "up",
                "market": "A",
                "is_trading_day": True,
            },
            {
                "name": "深证成指",
                "price": 10500.00,
                "change_pct": -0.50,
                "trend": "down",
                "market": "A",
                "is_trading_day": True,
            },
        ],
        "updated": "2026-02-04T10:00:00+08:00",
        "is_stale": False,
    }


@pytest.fixture
def sample_holiday_data() -> list[dict[str, Any]]:
    """节假日数据样例。"""
    return [
        {
            "name": "春节",
            "start_date": "2026-02-15",
            "end_date": "2026-02-23",
            "duration": 9,
            "days_left": 11,
            "is_legal_holiday": True,
            "color": "#E74C3C",
            "is_off_day": True,
        },
        {
            "name": "清明节",
            "start_date": "2026-04-04",
            "end_date": "2026-04-06",
            "duration": 3,
            "days_left": 59,
            "is_legal_holiday": True,
            "color": "#27AE60",
            "is_off_day": True,
        },
    ]
