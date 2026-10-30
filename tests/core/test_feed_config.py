"""RSS 订阅源配置测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import AppConfig, FeedConfig


def test_feed_config_defaults() -> None:
    """feed 默认启用，窗口为 7 条。"""
    config = FeedConfig()

    assert config.enabled is True
    assert config.max_items == 7


@pytest.mark.parametrize("max_items", [1, 60])
def test_feed_config_accepts_bounds(max_items: int) -> None:
    """接受文档规定的两个条目数边界值。"""
    assert FeedConfig(max_items=max_items).max_items == max_items


@pytest.mark.parametrize("max_items", [0, 61])
def test_feed_config_rejects_out_of_range_values(max_items: int) -> None:
    """拒绝超出支持范围的条目数上限。"""
    with pytest.raises(ValidationError):
        FeedConfig(max_items=max_items)


def test_feed_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """通过标准的嵌套环境变量约定加载 feed 配置。"""
    monkeypatch.setenv("MOYUREN_FEED_ENABLED", "false")
    monkeypatch.setenv("MOYUREN_FEED_MAX_ITEMS", "12")

    config = AppConfig.from_yaml(str(Path(__file__).resolve().parents[2] / "config.yaml"))

    assert config.feed.enabled is False
    assert config.feed.max_items == 12
