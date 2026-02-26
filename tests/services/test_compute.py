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
        assert "week_progress" in result
        assert "month_progress" in result
        assert "year_progress" in result
        assert isinstance(result["week_progress"], float)
        assert 0.0 <= result["week_progress"] <= 100.0
        assert result["is_fallback_mode"] is True  # Should be in fallback mode

    def test_compute_progress_at_cycle_start_returns_zero(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_progress returns 0.0 at all cycle starts."""
        # 2024-01-01 00:00:00 is a Monday, start of month, start of year
        now = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        result = aggregator._compute_progress(now)

        assert result["week_progress"] == 0.0
        assert result["month_progress"] == 0.0
        assert result["year_progress"] == 0.0

    def test_compute_progress_mid_cycle_returns_expected_values(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_progress returns expected values in mid-cycle."""
        # 2026-07-02 12:00:00+08:00 is Thursday noon
        now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        result = aggregator._compute_progress(now)

        # Week: Monday 00:00 -> next Monday 00:00, elapsed 3.5/7 days = 50.0
        assert result["week_progress"] == 50.0
        # Month: Jul 1 00:00 -> Aug 1 00:00, elapsed 1.5/31 days ≈ 4.84
        assert result["month_progress"] == 4.84
        # Year: Jan 1 00:00 -> Jan 1 next year, elapsed 182.5/365 days = 50.0
        assert result["year_progress"] == 50.0

    def test_compute_progress_end_of_year_rounds_to_hundred(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_progress rounds end-of-year value close to 100."""
        now = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=8)))

        result = aggregator._compute_progress(now)

        assert result["year_progress"] == 100.0
        assert 0.0 <= result["week_progress"] <= 100.0
        assert 0.0 <= result["month_progress"] <= 100.0

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

    def test_adapt_fills_default_progress_values(self, adapter: TemplateAdapter) -> None:
        """Test adapt fills default progress values when missing."""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert result["week_progress"] == 0.0
        assert result["month_progress"] == 0.0
        assert result["year_progress"] == 0.0

    def test_adapt_clamps_invalid_progress_values(self, adapter: TemplateAdapter) -> None:
        """Test adapt clamps invalid progress values."""
        domain_data: dict[str, Any] = {
            "week_progress": "invalid",
            "month_progress": -3.0,
            "year_progress": 123.456,
        }

        result = adapter.adapt(domain_data)

        assert result["week_progress"] == 0.0
        assert result["month_progress"] == 0.0
        assert result["year_progress"] == 100.0

    def test_adapt_handles_nan_inf_bool_progress(self, adapter: TemplateAdapter) -> None:
        """Test adapt handles NaN, Inf, and bool progress values."""
        domain_data: dict[str, Any] = {
            "week_progress": float("nan"),
            "month_progress": float("inf"),
            "year_progress": True,
        }

        result = adapter.adapt(domain_data)

        assert result["week_progress"] == 0.0
        assert result["month_progress"] == 0.0
        assert result["year_progress"] == 0.0


class TestComputeStockIndicesEdgeCases:
    """Supplementary tests for _compute_stock_indices edge cases."""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        return DomainDataAggregator()

    def test_items_not_list_returns_data_missing(self, aggregator: DomainDataAggregator) -> None:
        """Test when items is not a list."""
        raw_data = {"stock_indices": {"items": "not a list", "updated": "2026-02-04"}}
        result = aggregator._compute_stock_indices(raw_data)
        assert result["is_data_missing"] is True
        assert result["indices"] == []

    def test_non_dict_items_filtered(self, aggregator: DomainDataAggregator) -> None:
        """Test non-dict items are filtered from the list."""
        raw_data = {
            "stock_indices": {
                "items": [
                    "string_item",
                    42,
                    {"name": "Valid", "price": 100.0, "change_pct": 1.0},
                ],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert len(result["indices"]) == 1
        assert result["indices"][0]["name"] == "Valid"

    def test_change_pct_invalid_string(self, aggregator: DomainDataAggregator) -> None:
        """Test change_pct with invalid string value shows '--'."""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": 100.0, "change_pct": "abc"}],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert result["indices"][0]["change_pct"] == "--"

    def test_change_pct_none_shows_dashes(self, aggregator: DomainDataAggregator) -> None:
        """Test change_pct None shows '--'."""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": 100.0, "change_pct": None}],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert result["indices"][0]["change_pct"] == "--"

    def test_is_trading_day_none_type(self, aggregator: DomainDataAggregator) -> None:
        """Test is_trading_day with None (non str/int/float) type."""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "is_trading_day": None}],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert result["indices"][0]["is_trading_day"] is False

    def test_price_none_shows_dashes(self, aggregator: DomainDataAggregator) -> None:
        """Test price None shows '--'."""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": None}],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert result["indices"][0]["price"] == "--"


class TestComputeWeekendEdgeCases:
    """Supplementary tests for _compute_weekend."""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        return DomainDataAggregator()

    def test_weekend_saturday(self, aggregator: DomainDataAggregator) -> None:
        """Test Saturday is weekend with days_left=0."""
        sat = datetime(2026, 2, 7, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = aggregator._compute_weekend(sat)
        assert result["is_weekend"] is True
        assert result["days_left"] == 0

    def test_weekend_sunday(self, aggregator: DomainDataAggregator) -> None:
        """Test Sunday is weekend with days_left=0."""
        sun = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = aggregator._compute_weekend(sun)
        assert result["is_weekend"] is True
        assert result["days_left"] == 0


class TestComputeNewsListEdgeCases:
    """Supplementary tests for _compute_news_list and _compute_history."""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        return DomainDataAggregator()

    def test_history_with_fun_content(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_history extracts fun_content data."""
        raw_data = {"fun_content": {"title": "趣味标题", "content": "趣味内容"}}
        result = aggregator._compute_history(raw_data)
        assert result["title"] == "趣味标题"
        assert result["content"] == "趣味内容"

    def test_history_fun_content_missing_title(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_history with missing title falls back to default."""
        raw_data = {"fun_content": {"title": None, "content": "内容"}}
        result = aggregator._compute_history(raw_data)
        assert result["title"] == "🐟 摸鱼小贴士"
        assert result["content"] == "内容"

    def test_news_list_new_api_format(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_news_list with new API dict format."""
        raw_data = {"news": {"data": {"news": ["新闻1", "新闻2", "新闻3"]}}}
        result = aggregator._compute_news_list(raw_data)
        assert len(result) == 3
        assert result[0] == {"num": 1, "text": "新闻1"}
        assert result[2] == {"num": 3, "text": "新闻3"}

    def test_news_list_legacy_list_format(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_news_list with legacy list format."""
        raw_data = {"news": [{"text": "旧闻1"}, {"text": "旧闻2"}]}
        result = aggregator._compute_news_list(raw_data)
        assert len(result) == 2
        assert result[0] == {"num": 1, "text": "旧闻1"}

    def test_news_list_legacy_string_items(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_news_list legacy format with string items."""
        raw_data = {"news": ["字符串新闻1", "字符串新闻2"]}
        result = aggregator._compute_news_list(raw_data)
        assert result[0]["text"] == "字符串新闻1"

    def test_news_meta_with_updated(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_news_meta extracts metadata."""
        raw_data = {
            "news": {
                "data": {
                    "updated": "2026-02-04 10:30:00 CST",
                    "date": "2026-02-04",
                    "updated_at": "10:30",
                }
            }
        }
        result = aggregator._compute_news_meta(raw_data)
        assert result["date"] == "2026-02-04"
        assert result["updated"] is not None
        assert result["updated_at"] == "10:30"

    def test_news_meta_with_api_updated_fallback(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_news_meta falls back to api_updated."""
        raw_data = {
            "news": {
                "data": {
                    "api_updated": "2026-02-04 10:30:00 CST",
                    "date": "2026-02-04",
                    "api_updated_at": "10:30",
                }
            }
        }
        result = aggregator._compute_news_meta(raw_data)
        assert result["updated"] is not None
        assert result["updated_at"] == "10:30"

    def test_news_meta_empty_when_no_news(self, aggregator: DomainDataAggregator) -> None:
        """Test _compute_news_meta returns empty dict when no news data."""
        result = aggregator._compute_news_meta({})
        assert result == {}


class TestComputeHolidays:
    """Tests for _compute_holidays with holiday merging and dedup logic."""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        return DomainDataAggregator()

    @pytest.fixture
    def now(self) -> datetime:
        return datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_legal_holidays_basic(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test basic legal holiday processing."""
        raw_data = {
            "holidays": [
                {
                    "name": "春节",
                    "start_date": "2026-01-29",
                    "end_date": "2026-02-04",
                    "duration": 7,
                    "days_left": 0,
                }
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        assert len(result) == 1
        assert result[0]["name"] == "春节"
        assert result[0]["is_legal_holiday"] is True
        assert result[0]["color"] == "#E67E22"
        assert result[0]["duration"] == 7

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_simplify_special_name(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test special holiday name simplification."""
        raw_data = {
            "holidays": [
                {"name": "广西壮族自治区三月三", "start_date": "2026-04-14", "days_left": 69},
                {"name": "宁夏古尔邦节", "start_date": "2026-06-07", "days_left": 123},
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        names = [h["name"] for h in result]
        assert "三月三" in names
        assert "古尔邦节" in names

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_simplify_generic_prefix(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test generic prefix removal for regional holidays."""
        raw_data = {
            "holidays": [
                {"name": "新疆维吾尔自治区肉孜节", "start_date": "2026-03-20", "days_left": 44},
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        assert result[0]["name"] == "肉孜节"

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch(
        "app.services.compute.CalendarService.get_upcoming_lunar_festivals",
        return_value=[
            {"name": "春节假期", "solar_date": "2026-01-29", "days_left": 0},
            {"name": "元宵节", "solar_date": "2026-02-12", "days_left": 8},
        ],
    )
    def test_dedup_lunar_with_legal(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test lunar festival deduplication against legal holidays."""
        raw_data = {
            "holidays": [
                {"name": "春节", "start_date": "2026-01-29", "end_date": "2026-02-04", "duration": 7, "days_left": 0}
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        names = [h["name"] for h in result]
        # "春节假期" should be deduplicated (core word "春" matches "春节")
        assert "春节" in names
        assert "春节假期" not in names
        # "元宵节" should NOT be deduplicated
        assert "元宵节" in names

    @patch(
        "app.services.compute.CalendarService.get_upcoming_solar_festivals",
        return_value=[
            {"name": "情人节", "solar_date": "2026-02-14", "days_left": 10},
            {"name": "妇女节", "solar_date": "2026-03-08", "days_left": 32},
        ],
    )
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_solar_festivals_added(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test solar festivals are added when no duplicates."""
        result = aggregator._compute_holidays(now, {})
        names = [h["name"] for h in result]
        assert "情人节" in names
        assert "妇女节" in names
        assert all(h["is_legal_holiday"] is False for h in result)

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_non_dict_entries_filtered(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test non-dict entries in holidays are filtered."""
        raw_data = {"holidays": ["invalid", 42, {"name": "清明节", "start_date": "2026-04-05", "days_left": 60}]}
        result = aggregator._compute_holidays(now, raw_data)
        assert len(result) == 1
        assert result[0]["name"] == "清明节"

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_missing_fields_filtered(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test entries missing required fields are filtered."""
        raw_data = {
            "holidays": [
                {"name": "", "start_date": "2026-04-05"},  # empty name
                {"name": "Test", "start_date": ""},  # empty start_date
                {"name": "Valid", "start_date": "2026-04-05", "days_left": 60},
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        assert len(result) == 1
        assert result[0]["name"] == "Valid"

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_is_off_day_normalization(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test is_off_day boolean normalization."""
        raw_data = {
            "holidays": [
                {"name": "Holiday1", "start_date": "2026-03-01", "days_left": 25, "is_off_day": "false"},
                {"name": "Holiday2", "start_date": "2026-03-02", "days_left": 26, "is_off_day": True},
                {"name": "Holiday3", "start_date": "2026-03-03", "days_left": 27, "is_off_day": None},
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        by_name = {h["name"]: h for h in result}
        assert by_name["Holiday1"]["is_off_day"] is False
        assert by_name["Holiday2"]["is_off_day"] is True
        assert by_name["Holiday3"]["is_off_day"] is True  # None defaults to True

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_sorted_by_days_left_and_max_10(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test results are sorted by days_left and limited to 10."""
        raw_data = {
            "holidays": [
                {"name": f"H{i}", "start_date": f"2026-{(i % 12) + 1:02d}-01", "days_left": 15 - i}
                for i in range(15)
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        assert len(result) == 10
        days = [h["days_left"] for h in result]
        assert days == sorted(days)

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_duration_type_coercion(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """Test duration and days_left are coerced to int."""
        raw_data = {
            "holidays": [
                {"name": "Test", "start_date": "2026-03-01", "duration": "3", "days_left": "25"},
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        assert result[0]["duration"] == 3
        assert result[0]["days_left"] == 25


class TestDataComputer:
    """Tests for DataComputer backward-compatible wrapper."""

    def test_default_init(self) -> None:
        """Test default initialization creates DomainDataAggregator and TemplateAdapter."""
        from app.services.compute import DataComputer
        computer = DataComputer()
        assert isinstance(computer.aggregator, DomainDataAggregator)
        assert isinstance(computer.adapter, TemplateAdapter)

    def test_custom_init(self) -> None:
        """Test custom initialization with provided instances."""
        from app.services.compute import DataComputer
        agg = DomainDataAggregator()
        adp = TemplateAdapter()
        computer = DataComputer(aggregator=agg, adapter=adp)
        assert computer.aggregator is agg
        assert computer.adapter is adp

    def test_compute_delegates(self) -> None:
        """Test compute delegates to aggregator and adapter."""
        from app.services.compute import DataComputer
        computer = DataComputer()
        with patch("app.services.compute.now_business") as mock_now:
            mock_now.return_value = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
            with patch("app.services.compute.CalendarService") as mock_cal:
                mock_cal.get_lunar_info.return_value = {
                    "lunar_year": "乙巳年", "lunar_date": "正月初七", "zodiac": "蛇",
                }
                mock_cal.get_festivals.return_value = {
                    "festival_solar": None, "festival_lunar": None, "legal_holiday": None,
                }
                mock_cal.get_constellation.return_value = "水瓶座"
                mock_cal.get_moon_phase.return_value = "上弦月"
                mock_cal.is_holiday.return_value = False
                mock_cal.get_solar_term.return_value = {
                    "name": "立春", "name_en": "Spring", "date": "2026-02-04", "days_left": 0, "is_today": True,
                }
                mock_cal.get_solar_term_info.return_value = {
                    "name": "立春", "name_en": "Spring", "date": "2026-02-04", "days_left": 0, "is_today": True,
                }
                mock_cal.get_yi_ji.return_value = {"yi": ["宜"], "ji": ["忌"]}
                mock_cal.get_upcoming_solar_festivals.return_value = []
                mock_cal.get_upcoming_lunar_festivals.return_value = []
                result = computer.compute({})
        assert "date" in result
        assert "weekend" in result
        assert "is_fallback_mode" in result
