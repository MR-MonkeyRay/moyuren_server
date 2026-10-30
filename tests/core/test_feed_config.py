"""Tests for RSS feed configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import AppConfig, FeedConfig


def test_feed_config_defaults() -> None:
    """Enable the feed with a seven-item default window."""
    config = FeedConfig()

    assert config.enabled is True
    assert config.max_items == 7


@pytest.mark.parametrize("max_items", [1, 60])
def test_feed_config_accepts_bounds(max_items: int) -> None:
    """Accept both documented item-count boundaries."""
    assert FeedConfig(max_items=max_items).max_items == max_items


@pytest.mark.parametrize("max_items", [0, 61])
def test_feed_config_rejects_out_of_range_values(max_items: int) -> None:
    """Reject item-count limits outside the supported range."""
    with pytest.raises(ValidationError):
        FeedConfig(max_items=max_items)


def test_feed_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load feed settings through the standard nested environment convention."""
    monkeypatch.setenv("MOYUREN_FEED_ENABLED", "false")
    monkeypatch.setenv("MOYUREN_FEED_MAX_ITEMS", "12")

    config = AppConfig.from_yaml(str(Path(__file__).resolve().parents[2] / "config.yaml"))

    assert config.feed.enabled is False
    assert config.feed.max_items == 12
