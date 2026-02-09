"""Tests for app/services/compute.py - normalize_datetime and data aggregation."""

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from app.services.compute import (
    DomainDataAggregator,
    TemplateAdapter,
    normalize_datetime,
)


class TestNormalizeDatetime:
    """Tests for normalize_datetime function."""

    # --- Valid ISO formats ---

    def test_iso_format_with_timezone(self) -> None:
        """Test ISO format with timezone offset."""
        result = normalize_datetime("2026-02-04T10:00:00+08:00")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_iso_format_with_z_suffix(self) -> None:
        """Test ISO format with Z suffix (UTC)."""
        result = normalize_datetime("2026-02-04T10:00:00Z")
        assert result == "2026-02-04T10:00:00+00:00"

    def test_iso_format_without_timezone_uses_default(self) -> None:
        """Test ISO format without timezone uses default."""
        default_tz = timezone(timedelta(hours=8))
        result = normalize_datetime("2026-02-04T10:00:00", default_tz=default_tz)
        assert result == "2026-02-04T10:00:00+08:00"

    # --- Space-separated formats ---

    def test_space_separated_datetime(self) -> None:
        """Test space-separated datetime format."""
        default_tz = timezone(timedelta(hours=8))
        result = normalize_datetime("2026-02-04 10:00:00", default_tz=default_tz)
        assert result == "2026-02-04T10:00:00+08:00"

    def test_space_separated_without_seconds(self) -> None:
        """Test space-separated datetime without seconds."""
        default_tz = timezone(timedelta(hours=8))
        result = normalize_datetime("2026-02-04 10:00", default_tz=default_tz)
        assert result == "2026-02-04T10:00:00+08:00"

    # --- Timezone abbreviations ---

    def test_cst_timezone_abbreviation(self) -> None:
        """Test CST timezone abbreviation (China Standard Time)."""
        result = normalize_datetime("2026-02-04 10:00:00 CST")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_utc_timezone_abbreviation(self) -> None:
        """Test UTC timezone abbreviation."""
        result = normalize_datetime("2026-02-04 10:00:00 UTC")
        assert result == "2026-02-04T10:00:00+00:00"

    def test_gmt_timezone_abbreviation(self) -> None:
        """Test GMT timezone abbreviation."""
        result = normalize_datetime("2026-02-04 10:00:00 GMT")
        assert result == "2026-02-04T10:00:00+00:00"

    def test_est_timezone_abbreviation(self) -> None:
        """Test EST timezone abbreviation (Eastern Standard Time)."""
        result = normalize_datetime("2026-02-04 10:00:00 EST")
        assert result == "2026-02-04T10:00:00-05:00"

    def test_jst_timezone_abbreviation(self) -> None:
        """Test JST timezone abbreviation (Japan Standard Time)."""
        result = normalize_datetime("2026-02-04 10:00:00 JST")
        assert result == "2026-02-04T10:00:00+09:00"

    # --- UTC/GMT offset formats ---

    def test_utc_plus_offset(self) -> None:
        """Test UTC+8 format."""
        result = normalize_datetime("2026-02-04 10:00:00 UTC+8")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_utc_plus_offset_two_digits(self) -> None:
        """Test UTC+08 format."""
        result = normalize_datetime("2026-02-04 10:00:00 UTC+08")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_gmt_plus_offset(self) -> None:
        """Test GMT+8 format."""
        result = normalize_datetime("2026-02-04 10:00:00 GMT+8")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_utc_minus_offset(self) -> None:
        """Test UTC-5 format."""
        result = normalize_datetime("2026-02-04 10:00:00 UTC-5")
        assert result == "2026-02-04T10:00:00-05:00"

    # --- Numeric offset formats ---

    def test_numeric_offset_compact(self) -> None:
        """Test +0800 format."""
        result = normalize_datetime("2026-02-04 10:00:00 +0800")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_numeric_offset_with_colon(self) -> None:
        """Test +08:00 format."""
        result = normalize_datetime("2026-02-04 10:00:00 +08:00")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_numeric_offset_negative(self) -> None:
        """Test -05:00 format."""
        result = normalize_datetime("2026-02-04 10:00:00 -05:00")
        assert result == "2026-02-04T10:00:00-05:00"

    # --- Invalid inputs ---

    def test_empty_string_returns_none(self) -> None:
        """Test empty string returns None."""
        assert normalize_datetime("") is None

    def test_none_input_returns_none(self) -> None:
        """Test None input returns None."""
        assert normalize_datetime(None) is None  # type: ignore

    def test_whitespace_only_returns_none(self) -> None:
        """Test whitespace-only string returns None."""
        assert normalize_datetime("   ") is None

    def test_invalid_format_returns_none(self) -> None:
        """Test invalid format returns None."""
        assert normalize_datetime("not a date") is None

    def test_non_string_input_returns_none(self) -> None:
        """Test non-string input returns None."""
        assert normalize_datetime(12345) is None  # type: ignore

    # --- Edge cases ---

    def test_case_insensitive_timezone_abbr(self) -> None:
        """Test timezone abbreviation is case insensitive."""
        result = normalize_datetime("2026-02-04 10:00:00 cst")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_slash_separated_date(self) -> None:
        """Test slash-separated date format."""
        default_tz = timezone(timedelta(hours=8))
        result = normalize_datetime("2026/02/04 10:00:00", default_tz=default_tz)
        assert result == "2026-02-04T10:00:00+08:00"


class TestDomainDataAggregator:
    """Tests for DomainDataAggregator class."""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        """Create a DomainDataAggregator instance."""
        return DomainDataAggregator()

    def test_aggregate_with_empty_raw_data(self, aggregator: DomainDataAggregator) -> None:
        """Test aggregate with empty raw data uses defaults."""
        with patch("app.services.compute.now_business") as mock_now:
            mock_now.return_value = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
            with patch("app.services.compute.CalendarService") as mock_calendar:
                mock_calendar.get_lunar_info.return_value = {
                    "lunar_year": "乙巳年",
                    "lunar_date": "正月初七",
                    "zodiac": "蛇",
                }
                mock_calendar.get_festivals.return_value = {
                    "festival_solar": None,
                    "festival_lunar": None,
                    "legal_holiday": None,
                }
                mock_calendar.get_constellation.return_value = "水瓶座"
                mock_calendar.get_moon_phase.return_value = "上弦月"
                mock_calendar.is_holiday.return_value = False
                mock_calendar.get_solar_term.return_value = {
                    "name": "立春",
                    "name_en": "Beginning of Spring",
                    "date": "2026-02-04",
                    "days_left": 0,
                    "is_today": True,
                }

                result = aggregator.aggregate({})

        assert "date" in result
        assert "weekend" in result
        assert "news_list" in result
        assert result["is_fallback_mode"] is True  # Should be in fallback mode

    def test_compute_stock_indices_with_valid_data(
        self, aggregator: DomainDataAggregator, sample_stock_data: dict[str, Any]
    ) -> None:
        """Test _compute_stock_indices with valid data."""
        raw_data = {"stock_indices": sample_stock_data}
        result = aggregator._compute_stock_indices(raw_data)

        assert result is not None
        assert len(result["indices"]) == 2
        assert result["indices"][0]["name"] == "上证指数"
        assert result["indices"][0]["price"] == "3,200.50"
        assert result["indices"][0]["change_pct"] == "+1.25%"
        assert result["is_data_missing"] is False

    def test_compute_stock_indices_with_none_data(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_stock_indices with None data."""
        result = aggregator._compute_stock_indices({})

        assert result is not None
        assert result["indices"] == []
        assert result["is_data_missing"] is True

    def test_compute_stock_indices_with_invalid_type(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_stock_indices with invalid type."""
        result = aggregator._compute_stock_indices({"stock_indices": "invalid"})

        assert result is not None
        assert result["is_data_missing"] is True

    def test_compute_stock_indices_formats_negative_change(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_stock_indices formats negative change correctly."""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": 100.0, "change_pct": -2.5}],
                "updated": "2026-02-04T10:00:00+08:00",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)

        assert result["indices"][0]["change_pct"] == "-2.50%"

    def test_compute_stock_indices_handles_invalid_price(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_stock_indices handles invalid price."""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": "invalid", "change_pct": 1.0}],
                "updated": "2026-02-04T10:00:00+08:00",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)

        assert result["indices"][0]["price"] == "--"

    def test_compute_stock_indices_normalizes_is_trading_day_string(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_stock_indices normalizes is_trading_day from string."""
        raw_data = {
            "stock_indices": {
                "items": [
                    {"name": "Test1", "is_trading_day": "true"},
                    {"name": "Test2", "is_trading_day": "false"},
                    {"name": "Test3", "is_trading_day": "0"},
                ],
                "updated": "2026-02-04T10:00:00+08:00",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)

        assert result["indices"][0]["is_trading_day"] is True
        assert result["indices"][1]["is_trading_day"] is False
        assert result["indices"][2]["is_trading_day"] is False

    def test_compute_kfc_on_thursday(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_kfc returns content on Thursday."""
        # 2026-02-05 is Thursday (weekday=3)
        thursday = datetime(2026, 2, 5, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        raw_data = {"kfc_copy": "V我50"}

        result = aggregator._compute_kfc(thursday, raw_data)

        assert result is not None
        assert result["title"] == "CRAZY THURSDAY"
        assert result["content"] == "V我50"

    def test_compute_kfc_on_non_thursday(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_kfc returns None on non-Thursday."""
        # 2026-02-04 is Wednesday (weekday=2)
        wednesday = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        raw_data = {"kfc_copy": "V我50"}

        result = aggregator._compute_kfc(wednesday, raw_data)

        assert result is None

    def test_compute_kfc_on_thursday_without_content(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_kfc returns None on Thursday without content."""
        thursday = datetime(2026, 2, 5, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        raw_data = {}

        result = aggregator._compute_kfc(thursday, raw_data)

        assert result is None


class TestTemplateAdapter:
    """Tests for TemplateAdapter class."""

    @pytest.fixture
    def adapter(self) -> TemplateAdapter:
        """Create a TemplateAdapter instance."""
        return TemplateAdapter()

    def test_adapt_fills_default_history(self, adapter: TemplateAdapter) -> None:
        """Test adapt fills default history when missing."""
        domain_data: dict[str, Any] = {
            "date": {"year_month": "2026.02", "day": "4"},
            "weekend": {"days_left": 2, "is_weekend": False},
        }

        result = adapter.adapt(domain_data)

        assert "history" in result
        assert result["history"]["title"] == "🐟 摸鱼小贴士"

    def test_adapt_fills_default_news_list(self, adapter: TemplateAdapter) -> None:
        """Test adapt fills default news_list when missing."""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert "news_list" in result
        assert len(result["news_list"]) > 0

    def test_adapt_preserves_existing_data(self, adapter: TemplateAdapter) -> None:
        """Test adapt preserves existing data."""
        domain_data: dict[str, Any] = {
            "history": {"title": "Custom", "content": "Custom content"},
            "news_list": [{"num": 1, "text": "Custom news"}],
        }

        result = adapter.adapt(domain_data)

        assert result["history"]["title"] == "Custom"
        assert result["news_list"][0]["text"] == "Custom news"

    def test_adapt_sets_fallback_mode_flag(self, adapter: TemplateAdapter) -> None:
        """Test adapt sets is_fallback_mode flag."""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert "is_fallback_mode" in result

    def test_adapt_initializes_empty_holidays(self, adapter: TemplateAdapter) -> None:
        """Test adapt initializes holidays to empty list."""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert result["holidays"] == []

    def test_adapt_initializes_empty_news_meta(self, adapter: TemplateAdapter) -> None:
        """Test adapt initializes news_meta to empty dict."""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert result["news_meta"] == {}
