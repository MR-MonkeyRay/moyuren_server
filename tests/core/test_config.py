"""Tests for app/core/config.py - configuration management."""

import pytest
from pydantic import ValidationError

from app.core.config import (
    CacheConfig,
    CrazyThursdaySource,
    FunContentEndpoint,
    FunContentSource,
    HolidaySource,
    LoggingConfig,
    NewsSource,
    SchedulerConfig,
    ServerConfig,
    StockIndexSource,
    TemplateItemConfig,
    TemplateRenderConfig,
    TemplatesConfig,
    TimezoneConfig,
    ViewportConfig,
)


class TestServerConfig:
    """Tests for ServerConfig model."""

    def test_valid_server_config(self) -> None:
        """Test valid server configuration."""
        config = ServerConfig(
            host="0.0.0.0",
            port=8000,
            base_domain="http://localhost:8000"
        )
        assert config.host == "0.0.0.0"
        assert config.port == 8000


class TestSchedulerConfig:
    """Tests for SchedulerConfig model."""

    def test_valid_daily_times(self) -> None:
        """Test valid daily times."""
        config = SchedulerConfig(daily_times=["06:00", "18:00"])
        assert len(config.daily_times) == 2

    def test_valid_hourly_mode_config(self) -> None:
        """Test valid hourly mode config."""
        config = SchedulerConfig(mode="hourly", minute_of_hour=30)
        assert config.mode == "hourly"
        assert config.minute_of_hour == 30

    def test_invalid_time_format_raises_error(self) -> None:
        """Test invalid time format raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(daily_times=["25:00"])
        assert "Invalid time format" in str(exc_info.value)

    def test_empty_daily_times_raises_error(self) -> None:
        """Test empty daily times raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(daily_times=[])
        assert "cannot be empty" in str(exc_info.value)

    def test_empty_daily_times_allowed_in_hourly_mode(self) -> None:
        """Test empty daily times is allowed in hourly mode."""
        config = SchedulerConfig(mode="hourly", daily_times=[], minute_of_hour=0)
        assert config.mode == "hourly"
        assert config.daily_times == []

    @pytest.mark.parametrize("minute", [-1, 60])
    def test_invalid_minute_of_hour_raises_error(self, minute: int) -> None:
        """Test invalid minute_of_hour raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SchedulerConfig(mode="hourly", minute_of_hour=minute)
        assert "minute_of_hour must be between 0 and 59" in str(exc_info.value)

    def test_strips_whitespace(self) -> None:
        """Test strips whitespace from time strings."""
        config = SchedulerConfig(daily_times=["  06:00  "])
        assert config.daily_times == ["06:00"]


class TestCacheConfig:
    """Tests for CacheConfig model."""

    def test_valid_ttl(self) -> None:
        """Test valid TTL."""
        config = CacheConfig(ttl_hours=24)
        assert config.ttl_hours == 24

    def test_zero_ttl_raises_error(self) -> None:
        """Test zero TTL raises error."""
        with pytest.raises(ValidationError) as exc_info:
            CacheConfig(ttl_hours=0)
        assert "must be positive" in str(exc_info.value)

    def test_negative_ttl_raises_error(self) -> None:
        """Test negative TTL raises error."""
        with pytest.raises(ValidationError):
            CacheConfig(ttl_hours=-1)


class TestTemplatesConfig:
    """Tests for TemplatesConfig model."""

    def test_get_template_by_name(self) -> None:
        """Test get template by name."""
        config = TemplatesConfig(
            default="main",
            items=[
                TemplateItemConfig(
                    name="main",
                    path="templates/main.html",
                    viewport=ViewportConfig(width=800, height=600)
                ),
                TemplateItemConfig(
                    name="alt",
                    path="templates/alt.html",
                    viewport=ViewportConfig(width=800, height=600)
                ),
            ]
        )
        template = config.get_template("alt")
        assert template.name == "alt"

    def test_get_template_default(self) -> None:
        """Test get template uses default."""
        config = TemplatesConfig(
            default="main",
            items=[
                TemplateItemConfig(
                    name="main",
                    path="templates/main.html",
                    viewport=ViewportConfig(width=800, height=600)
                ),
            ]
        )
        template = config.get_template()
        assert template.name == "main"

    def test_get_template_not_found_raises_error(self) -> None:
        """Test get template not found raises error."""
        config = TemplatesConfig(
            items=[
                TemplateItemConfig(
                    name="main",
                    path="templates/main.html",
                    viewport=ViewportConfig(width=800, height=600)
                )
            ]
        )
        with pytest.raises(ValueError, match="Template not found"):
            config.get_template("nonexistent")

    def test_duplicate_names_raises_error(self) -> None:
        """Test duplicate template names raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplatesConfig(
                items=[
                    TemplateItemConfig(
                        name="main",
                        path="templates/main.html",
                        viewport=ViewportConfig(width=800, height=600)
                    ),
                    TemplateItemConfig(
                        name="main",
                        path="templates/other.html",
                        viewport=ViewportConfig(width=800, height=600)
                    ),
                ]
            )
        assert "unique" in str(exc_info.value)


class TestLoggingConfig:
    """Tests for LoggingConfig model."""

    def test_valid_log_level(self) -> None:
        """Test valid log level."""
        config = LoggingConfig(level="DEBUG")
        assert config.level == "DEBUG"

    def test_case_insensitive_level(self) -> None:
        """Test log level is case insensitive."""
        config = LoggingConfig(level="debug")
        assert config.level == "DEBUG"

    def test_invalid_level_raises_error(self) -> None:
        """Test invalid log level raises error."""
        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(level="INVALID")
        assert "must be one of" in str(exc_info.value)


class TestTimezoneConfig:
    """Tests for TimezoneConfig model."""

    def test_valid_iana_timezone(self) -> None:
        """Test valid IANA timezone."""
        config = TimezoneConfig(business="Asia/Shanghai", display="America/New_York")
        assert config.business == "Asia/Shanghai"

    def test_display_accepts_local(self) -> None:
        """Test display field accepts 'local'."""
        config = TimezoneConfig(business="Asia/Shanghai", display="local")
        assert config.display == "local"

    def test_business_rejects_local(self) -> None:
        """Test business field rejects 'local'."""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="local", display="local")
        assert "does not accept 'local'" in str(exc_info.value)

    def test_valid_utc_offset(self) -> None:
        """Test valid UTC offset format."""
        config = TimezoneConfig(business="UTC+8", display="UTC-5")
        assert config.business == "UTC+8"

    def test_invalid_timezone_raises_error(self) -> None:
        """Test invalid timezone raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="Invalid/Zone")
        assert "Invalid timezone" in str(exc_info.value)

    def test_utc_offset_out_of_range_raises_error(self) -> None:
        """Test UTC offset out of range raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TimezoneConfig(business="UTC+15")
        assert "Invalid timezone offset" in str(exc_info.value)


class TestHolidaySource:
    """Tests for HolidaySource model."""

    def test_valid_holiday_config(self) -> None:
        """Test valid holiday configuration."""
        config = HolidaySource(
            type="holiday",
            mirror_urls=["https://mirror.example.com/"],
            timeout_sec=10
        )
        assert len(config.mirror_urls) == 1

    def test_zero_timeout_raises_error(self) -> None:
        """Test zero timeout raises error."""
        with pytest.raises(ValidationError):
            HolidaySource(type="holiday", timeout_sec=0)


class TestCrazyThursdaySource:
    """Tests for CrazyThursdaySource model."""

    def test_valid_config(self) -> None:
        """Test valid crazy thursday configuration."""
        config = CrazyThursdaySource(
            type="crazy_thursday",
            enabled=True,
            url="https://api.example.com/kfc",
            timeout_sec=5
        )
        assert config.enabled is True

    def test_disabled_config(self) -> None:
        """Test disabled configuration."""
        config = CrazyThursdaySource(
            type="crazy_thursday",
            enabled=False,
            url="https://api.example.com/kfc",
            timeout_sec=5
        )
        assert config.enabled is False


class TestStockIndexSource:
    """Tests for StockIndexSource model."""

    def test_valid_config(self) -> None:
        """Test valid stock index configuration."""
        config = StockIndexSource(
            type="stock_index",
            quote_url="https://api.example.com/quote",
            secids=["1.000001", "0.399001"],
            timeout_sec=5,
            market_timezones={"A": "Asia/Shanghai"},
            cache_ttl_sec=60
        )
        assert len(config.secids) == 2
        assert config.cache_ttl_sec == 60


class TestTemplateRenderConfig:
    """Tests for TemplateRenderConfig model."""

    def test_valid_config(self) -> None:
        """Test valid template render configuration."""
        config = TemplateRenderConfig(
            device_scale_factor=2,
            jpeg_quality=90,
            use_china_cdn=True
        )
        assert config.device_scale_factor == 2
        assert config.jpeg_quality == 90

    def test_zero_scale_raises_error(self) -> None:
        """Test zero device scale factor raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(device_scale_factor=0)
        assert "must be positive" in str(exc_info.value)

    def test_invalid_jpeg_quality_raises_error(self) -> None:
        """Test invalid JPEG quality raises error."""
        with pytest.raises(ValidationError) as exc_info:
            TemplateRenderConfig(jpeg_quality=101)
        assert "must be between 1 and 100" in str(exc_info.value)


class TestNewsSource:
    """Tests for NewsSource model."""

    def test_valid_config(self) -> None:
        """Test valid news source configuration."""
        config = NewsSource(
            type="news",
            url="https://api.example.com/news",
            params={"key": "value"}
        )
        assert config.type == "news"
        assert config.url == "https://api.example.com/news"

    def test_empty_url_raises_error(self) -> None:
        """Test empty URL raises error."""
        with pytest.raises(ValidationError) as exc_info:
            NewsSource(type="news", url="")
        assert "cannot be empty" in str(exc_info.value)


class TestFunContentSource:
    """Tests for FunContentSource model."""

    def test_valid_config(self) -> None:
        """Test valid fun content source configuration."""
        config = FunContentSource(
            type="fun_content",
            endpoints=[
                FunContentEndpoint(
                    name="joke",
                    url="https://api.example.com/joke",
                    data_path="data",
                    display_title="笑话"
                )
            ]
        )
        assert config.type == "fun_content"
        assert len(config.endpoints) == 1

    def test_empty_endpoints_raises_error(self) -> None:
        """Test empty endpoints raises error."""
        with pytest.raises(ValidationError) as exc_info:
            FunContentSource(type="fun_content", endpoints=[])
        assert "cannot be empty" in str(exc_info.value)
