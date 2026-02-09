"""Global test fixtures and configuration."""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# --- Basic Utilities ---

@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Return the test data directory path."""
    return Path(__file__).parent / "data"


@pytest.fixture
def load_json(test_data_dir: Path) -> Callable[[str], dict[str, Any]]:
    """Load JSON test data from file."""
    def _loader(rel_path: str) -> dict[str, Any]:
        with open(test_data_dir / rel_path, encoding="utf-8") as f:
            return json.load(f)
    return _loader


# --- Environment Configuration ---

@pytest.fixture(autouse=True)
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override environment variables to prevent pollution."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("TZ", "Asia/Shanghai")


# --- Temporary Directories ---

@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    """Create temporary state and static directories."""
    (tmp_path / "state").mkdir()
    (tmp_path / "static").mkdir()
    return tmp_path


# --- Logger ---

@pytest.fixture
def logger() -> logging.Logger:
    """Return a test logger."""
    return logging.getLogger("test")


# --- Time Fixtures ---

@pytest.fixture
def fixed_datetime() -> datetime:
    """Return a fixed datetime for testing (2026-02-04 10:00:00 CST)."""
    return datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def fixed_thursday() -> datetime:
    """Return a fixed Thursday datetime for KFC testing (2026-02-05 is Thursday)."""
    return datetime(2026, 2, 5, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))


# --- Mock Browser ---

@pytest.fixture
def mock_browser_page() -> AsyncMock:
    """Mock Playwright Page object."""
    page = AsyncMock()
    page.set_content = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"fake_image_bytes")
    page.close = AsyncMock()
    return page


@pytest.fixture
def mock_browser_manager(mock_browser_page: AsyncMock) -> MagicMock:
    """Mock browser manager."""
    manager = MagicMock()
    manager.create_page = AsyncMock(return_value=mock_browser_page)
    return manager


# --- Mock HTTP Client ---

@pytest.fixture
def mock_httpx_response() -> Callable[[dict[str, Any], int], MagicMock]:
    """Create a mock httpx response."""
    def _create_response(json_data: dict[str, Any], status_code: int = 200) -> MagicMock:
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = json_data
        response.raise_for_status = MagicMock()
        if status_code >= 400:
            from httpx import HTTPStatusError
            response.raise_for_status.side_effect = HTTPStatusError(
                message=f"HTTP {status_code}",
                request=MagicMock(),
                response=response
            )
        return response
    return _create_response


# --- Sample Data Fixtures ---

@pytest.fixture
def sample_news_response() -> dict[str, Any]:
    """Sample 60s news API response."""
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
            "updated": "2026-02-04T06:00:00+08:00"
        }
    }


@pytest.fixture
def sample_stock_data() -> dict[str, Any]:
    """Sample stock indices data."""
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
    """Sample holiday data."""
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


@pytest.fixture
def sample_v1_state() -> dict[str, Any]:
    """Sample v1 state data for migration testing."""
    return {
        "date": "2026-02-04",
        "timestamp": "2026-02-04T10:00:00+08:00",
        "filename": "moyuren_20260204.jpg",
        "weekday": "星期三",
        "lunar_date": "正月初七",
        "fun_content": {
            "title": "🐟 摸鱼小贴士",
            "content": "工作再忙，也要记得摸鱼。"
        },
        "is_crazy_thursday": False,
    }


@pytest.fixture
def sample_v2_state() -> dict[str, Any]:
    """Sample v2 state data."""
    return {
        "version": 2,
        "public": {
            "date": "2026-02-04",
            "timestamp": "2026-02-04T10:00:00+08:00",
            "updated": "2026/02/04 10:00:00",
            "updated_at": 1738634400000,
            "weekday": "星期三",
            "lunar_date": "正月初七",
            "fun_content": None,
            "is_crazy_thursday": False,
            "kfc_content": None,
        },
        "templates": {
            "moyuren": {
                "filename": "moyuren_20260204.jpg",
                "updated": "2026/02/04 10:00:00",
                "updated_at": 1738634400000,
            }
        },
        "template_data": {
            "moyuren": {}
        },
    }
