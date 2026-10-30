"""测试 app/services/compute.py 的日期时间标准化与数据聚合。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from jinja2 import Environment, FileSystemLoader

from app.core.config import TextLimitsConfig
from app.services.calendar import CalendarService
from app.services.compute import (
    DataComputer,
    DomainDataAggregator,
    TemplateAdapter,
    normalize_datetime,
)


class TestNormalizeDatetime:
    """测试 normalize_datetime 函数。"""

    # --- 有效的 ISO 格式 ---

    def test_iso_format_with_timezone(self) -> None:
        """测试带时区偏移量的 ISO 格式。"""
        result = normalize_datetime("2026-02-04T10:00:00+08:00")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_iso_format_with_z_suffix(self) -> None:
        """测试带 Z 后缀（UTC）的 ISO 格式。"""
        result = normalize_datetime("2026-02-04T10:00:00Z")
        assert result == "2026-02-04T10:00:00+00:00"

    def test_iso_format_without_timezone_uses_default(self) -> None:
        """测试不含时区的 ISO 格式使用默认时区。"""
        default_tz = timezone(timedelta(hours=8))
        result = normalize_datetime("2026-02-04T10:00:00", default_tz=default_tz)
        assert result == "2026-02-04T10:00:00+08:00"

    def test_naive_datetime_preserves_utc_business_timezone(self) -> None:
        """测试不带时区的输入保留零偏移量的业务时区。"""
        with patch("app.services.compute.get_business_timezone", return_value=timezone.utc):
            assert normalize_datetime("2026-02-04 10:00:00") == "2026-02-04T10:00:00+00:00"

    # --- 空格分隔的格式 ---

    def test_space_separated_datetime(self) -> None:
        """测试空格分隔的日期时间格式。"""
        default_tz = timezone(timedelta(hours=8))
        result = normalize_datetime("2026-02-04 10:00:00", default_tz=default_tz)
        assert result == "2026-02-04T10:00:00+08:00"

    def test_space_separated_without_seconds(self) -> None:
        """测试空格分隔且不含秒的日期时间。"""
        default_tz = timezone(timedelta(hours=8))
        result = normalize_datetime("2026-02-04 10:00", default_tz=default_tz)
        assert result == "2026-02-04T10:00:00+08:00"

    # --- 时区缩写 ---

    def test_cst_timezone_abbreviation(self) -> None:
        """测试 CST 时区缩写（中国标准时间）。"""
        result = normalize_datetime("2026-02-04 10:00:00 CST")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_utc_timezone_abbreviation(self) -> None:
        """测试 UTC 时区缩写。"""
        result = normalize_datetime("2026-02-04 10:00:00 UTC")
        assert result == "2026-02-04T10:00:00+00:00"

    def test_gmt_timezone_abbreviation(self) -> None:
        """测试 GMT 时区缩写。"""
        result = normalize_datetime("2026-02-04 10:00:00 GMT")
        assert result == "2026-02-04T10:00:00+00:00"

    def test_est_timezone_abbreviation(self) -> None:
        """测试 EST 时区缩写（北美东部标准时间）。"""
        result = normalize_datetime("2026-02-04 10:00:00 EST")
        assert result == "2026-02-04T10:00:00-05:00"

    def test_jst_timezone_abbreviation(self) -> None:
        """测试 JST 时区缩写（日本标准时间）。"""
        result = normalize_datetime("2026-02-04 10:00:00 JST")
        assert result == "2026-02-04T10:00:00+09:00"

    # --- UTC/GMT 偏移量格式 ---

    def test_utc_plus_offset(self) -> None:
        """测试 UTC+8 格式。"""
        result = normalize_datetime("2026-02-04 10:00:00 UTC+8")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_utc_plus_offset_two_digits(self) -> None:
        """测试 UTC+08 格式。"""
        result = normalize_datetime("2026-02-04 10:00:00 UTC+08")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_gmt_plus_offset(self) -> None:
        """测试 GMT+8 格式。"""
        result = normalize_datetime("2026-02-04 10:00:00 GMT+8")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_utc_minus_offset(self) -> None:
        """测试 UTC-5 格式。"""
        result = normalize_datetime("2026-02-04 10:00:00 UTC-5")
        assert result == "2026-02-04T10:00:00-05:00"

    # --- 数字偏移量格式 ---

    def test_numeric_offset_compact(self) -> None:
        """测试 +0800 格式。"""
        result = normalize_datetime("2026-02-04 10:00:00 +0800")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_numeric_offset_with_colon(self) -> None:
        """测试 +08:00 格式。"""
        result = normalize_datetime("2026-02-04 10:00:00 +08:00")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_numeric_offset_negative(self) -> None:
        """测试 -05:00 格式。"""
        result = normalize_datetime("2026-02-04 10:00:00 -05:00")
        assert result == "2026-02-04T10:00:00-05:00"

    # --- 无效输入 ---

    def test_empty_string_returns_none(self) -> None:
        """测试空字符串返回 None。"""
        assert normalize_datetime("") is None

    def test_none_input_returns_none(self) -> None:
        """测试 None 输入返回 None。"""
        assert normalize_datetime(None) is None  # type: ignore

    def test_whitespace_only_returns_none(self) -> None:
        """测试仅含空白的字符串返回 None。"""
        assert normalize_datetime("   ") is None

    def test_invalid_format_returns_none(self) -> None:
        """测试无效格式返回 None。"""
        assert normalize_datetime("not a date") is None

    def test_non_string_input_returns_none(self) -> None:
        """测试非字符串输入返回 None。"""
        assert normalize_datetime(12345) is None  # type: ignore

    # --- 边界情况 ---

    def test_case_insensitive_timezone_abbr(self) -> None:
        """测试时区缩写不区分大小写。"""
        result = normalize_datetime("2026-02-04 10:00:00 cst")
        assert result == "2026-02-04T10:00:00+08:00"

    def test_slash_separated_date(self) -> None:
        """测试斜杠分隔的日期格式。"""
        default_tz = timezone(timedelta(hours=8))
        result = normalize_datetime("2026/02/04 10:00:00", default_tz=default_tz)
        assert result == "2026-02-04T10:00:00+08:00"


class TestDomainDataAggregator:
    """测试 DomainDataAggregator 类。"""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        """创建 DomainDataAggregator 实例。"""
        return DomainDataAggregator()

    def test_aggregate_with_empty_raw_data(self, aggregator: DomainDataAggregator) -> None:
        """测试聚合空原始数据时使用默认值。"""
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
        assert result["is_fallback_mode"] is True  # 应处于降级模式

    def test_compute_progress_at_cycle_start_returns_zero(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_progress 在各周期起点均返回 0.0。"""
        # 2024-01-01 00:00:00 同时是周一、月初和年初
        now = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        result = aggregator._compute_progress(now)

        assert result["week_progress"] == 0.0
        assert result["month_progress"] == 0.0
        assert result["year_progress"] == 0.0

    def test_compute_progress_mid_cycle_returns_expected_values(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_progress 在周期中途返回预期值。"""
        # 2026-07-02 12:00:00+08:00 是周四中午
        now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        result = aggregator._compute_progress(now)

        # 周：周一 00:00 至下周一 00:00，已过 3.5/7 天 = 50.0
        assert result["week_progress"] == 50.0
        # 月：7 月 1 日 00:00 至 8 月 1 日 00:00，已过 1.5/31 天 ≈ 4.84
        assert result["month_progress"] == 4.84
        # 年：1 月 1 日 00:00 至次年 1 月 1 日，已过 182.5/365 天 = 50.0
        assert result["year_progress"] == 50.0

    def test_compute_progress_end_of_year_rounds_to_hundred(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_progress 将年末接近 100 的值四舍五入。"""
        now = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=8)))

        result = aggregator._compute_progress(now)

        assert result["year_progress"] == 100.0
        assert 0.0 <= result["week_progress"] <= 100.0
        assert 0.0 <= result["month_progress"] <= 100.0

    def test_compute_stock_indices_with_valid_data(
        self, aggregator: DomainDataAggregator, sample_stock_data: dict[str, Any]
    ) -> None:
        """测试 _compute_stock_indices 处理有效数据。"""
        raw_data = {"stock_indices": sample_stock_data}
        result = aggregator._compute_stock_indices(raw_data)

        assert result is not None
        assert len(result["indices"]) == 2
        assert result["indices"][0]["name"] == "上证指数"
        assert result["indices"][0]["price"] == "3,200.50"
        assert result["indices"][0]["change_pct"] == "+1.25%"
        assert result["is_data_missing"] is False

    def test_compute_stock_indices_with_none_data(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_stock_indices 处理 None 数据。"""
        result = aggregator._compute_stock_indices({})

        assert result is not None
        assert result["indices"] == []
        assert result["is_data_missing"] is True

    def test_compute_stock_indices_with_invalid_type(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_stock_indices 处理无效类型。"""
        result = aggregator._compute_stock_indices({"stock_indices": "invalid"})

        assert result is not None
        assert result["is_data_missing"] is True

    def test_compute_stock_indices_formats_negative_change(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_stock_indices 正确格式化负涨跌幅。"""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": 100.0, "change_pct": -2.5}],
                "updated": "2026-02-04T10:00:00+08:00",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)

        assert result["indices"][0]["change_pct"] == "-2.50%"

    def test_compute_stock_indices_handles_invalid_price(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_stock_indices 处理无效价格。"""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": "invalid", "change_pct": 1.0}],
                "updated": "2026-02-04T10:00:00+08:00",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)

        assert result["indices"][0]["price"] == "--"

    def test_compute_stock_indices_normalizes_is_trading_day_string(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_stock_indices 将字符串 is_trading_day 标准化。"""
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
        """测试 _compute_kfc 在周四返回内容。"""
        # 2026-02-05 是周四（weekday=3）
        thursday = datetime(2026, 2, 5, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        raw_data = {"kfc_copy": "V我50"}

        result = aggregator._compute_kfc(thursday, raw_data)

        assert result is not None
        assert result["title"] == "CRAZY THURSDAY"
        assert result["content"] == "V我50"

    def test_compute_kfc_on_non_thursday(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_kfc 在非周四返回 None。"""
        # 2026-02-04 是周三（weekday=2）
        wednesday = datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        raw_data = {"kfc_copy": "V我50"}

        result = aggregator._compute_kfc(wednesday, raw_data)

        assert result is None

    def test_compute_kfc_on_thursday_without_content(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_kfc 在周四无内容时返回 None。"""
        thursday = datetime(2026, 2, 5, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        raw_data = {}

        result = aggregator._compute_kfc(thursday, raw_data)

        assert result is None


class TestTemplateAdapter:
    """测试 TemplateAdapter 类。"""

    @pytest.fixture
    def adapter(self) -> TemplateAdapter:
        """创建 TemplateAdapter 实例。"""
        return TemplateAdapter()

    def test_adapt_fills_default_history(self, adapter: TemplateAdapter) -> None:
        """测试 adapt 在缺少 history 时填充默认值。"""
        domain_data: dict[str, Any] = {
            "date": {"year_month": "2026.02", "day": "4"},
            "weekend": {"days_left": 2, "is_weekend": False},
        }

        result = adapter.adapt(domain_data)

        assert "history" in result
        assert result["history"]["title"] == "🐟 摸鱼小贴士"

    def test_adapt_fills_default_news_list(self, adapter: TemplateAdapter) -> None:
        """测试 adapt 在缺少 news_list 时填充默认值。"""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert "news_list" in result
        assert len(result["news_list"]) > 0

    def test_adapt_preserves_existing_data(self, adapter: TemplateAdapter) -> None:
        """测试 adapt 保留已有数据。"""
        domain_data: dict[str, Any] = {
            "history": {"title": "Custom", "content": "Custom content"},
            "news_list": [{"num": 1, "text": "Custom news"}],
        }

        result = adapter.adapt(domain_data)

        assert result["history"]["title"] == "Custom"
        assert result["news_list"][0]["text"] == "Custom news"

    def test_adapt_sets_fallback_mode_flag(self, adapter: TemplateAdapter) -> None:
        """测试 adapt 设置 is_fallback_mode 标记。"""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert "is_fallback_mode" in result

    def test_adapt_initializes_empty_holidays(self, adapter: TemplateAdapter) -> None:
        """测试 adapt 将 holidays 初始化为空列表。"""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert result["holidays"] == []

    def test_adapt_initializes_empty_news_meta(self, adapter: TemplateAdapter) -> None:
        """测试 adapt 将 news_meta 初始化为空字典。"""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert result["news_meta"] == {}

    def test_adapt_fills_default_progress_values(self, adapter: TemplateAdapter) -> None:
        """测试 adapt 在缺少进度值时填充默认值。"""
        domain_data: dict[str, Any] = {}

        result = adapter.adapt(domain_data)

        assert result["week_progress"] == 0.0
        assert result["month_progress"] == 0.0
        assert result["year_progress"] == 0.0

    def test_adapt_clamps_invalid_progress_values(self, adapter: TemplateAdapter) -> None:
        """测试 adapt 将无效进度值限制在有效范围内。"""
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
        """测试 adapt 处理 NaN、Inf 和布尔类型的进度值。"""
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
    """补充测试 _compute_stock_indices 的边界情况。"""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        return DomainDataAggregator()

    @pytest.mark.parametrize("items", [[], [None, "invalid", 42]])
    def test_empty_or_non_dict_items_mean_failure(
        self, aggregator: DomainDataAggregator, items: list[Any]
    ) -> None:
        """处理后的空列表不能表示休市日。"""
        result = aggregator._compute_stock_indices({"stock_indices": {"items": items}})
        assert result["is_data_missing"] is True
        assert result["indices"] == []

    def test_explicit_data_missing_flag_is_preserved(
        self, aggregator: DomainDataAggregator
    ) -> None:
        """服务的失败标记优先于已有条目。"""
        result = aggregator._compute_stock_indices(
            {"stock_indices": {"items": [{"name": "Test"}], "is_data_missing": True}}
        )
        assert result["is_data_missing"] is True

    def test_closed_market_items_are_not_missing(
        self, aggregator: DomainDataAggregator
    ) -> None:
        """真实的休市日数据仍包含已配置的指数条目。"""
        result = aggregator._compute_stock_indices(
            {"stock_indices": {"items": [{"name": "Test", "is_trading_day": False}]}}
        )
        assert result["is_data_missing"] is False
        assert result["indices"][0]["is_trading_day"] is False

    def test_items_not_list_returns_data_missing(self, aggregator: DomainDataAggregator) -> None:
        """测试 items 不是列表的情况。"""
        raw_data = {"stock_indices": {"items": "not a list", "updated": "2026-02-04"}}
        result = aggregator._compute_stock_indices(raw_data)
        assert result["is_data_missing"] is True
        assert result["indices"] == []

    def test_non_dict_items_filtered(self, aggregator: DomainDataAggregator) -> None:
        """测试过滤列表中的非字典条目。"""
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
        """测试 change_pct 为无效字符串时显示 '--'。"""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": 100.0, "change_pct": "abc"}],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert result["indices"][0]["change_pct"] == "--"

    def test_change_pct_none_shows_dashes(self, aggregator: DomainDataAggregator) -> None:
        """测试 change_pct 为 None 时显示 '--'。"""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": 100.0, "change_pct": None}],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert result["indices"][0]["change_pct"] == "--"

    def test_is_trading_day_none_type(self, aggregator: DomainDataAggregator) -> None:
        """测试 is_trading_day 为 None（非 str/int/float）的情况。"""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "is_trading_day": None}],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert result["indices"][0]["is_trading_day"] is False

    def test_price_none_shows_dashes(self, aggregator: DomainDataAggregator) -> None:
        """测试 price 为 None 时显示 '--'。"""
        raw_data = {
            "stock_indices": {
                "items": [{"name": "Test", "price": None}],
                "updated": "2026-02-04",
            }
        }
        result = aggregator._compute_stock_indices(raw_data)
        assert result["indices"][0]["price"] == "--"


class TestComputeWeekendEdgeCases:
    """补充测试 _compute_weekend。"""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        return DomainDataAggregator()

    def test_weekend_saturday(self, aggregator: DomainDataAggregator) -> None:
        """测试周六为周末且 days_left=0。"""
        sat = datetime(2026, 2, 7, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = aggregator._compute_weekend(sat)
        assert result["is_weekend"] is True
        assert result["days_left"] == 0

    def test_weekend_sunday(self, aggregator: DomainDataAggregator) -> None:
        """测试周日为周末且 days_left=0。"""
        sun = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        result = aggregator._compute_weekend(sun)
        assert result["is_weekend"] is True
        assert result["days_left"] == 0


class TestComputeNewsListEdgeCases:
    """补充测试 _compute_news_list 和 _compute_history。"""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        return DomainDataAggregator()

    def test_history_with_fun_content(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_history 提取 fun_content 数据。"""
        raw_data = {
            "fun_content": {
                "title": "趣味标题",
                "content": "趣味内容",
                "type": "hitokoto",
            }
        }
        result = aggregator._compute_history(raw_data)
        assert result["title"] == "趣味标题"
        assert result["content"] == "趣味内容"
        assert result["type"] == "hitokoto"

    def test_history_fun_content_missing_title(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_history 在缺少标题时回退到默认值。"""
        raw_data = {"fun_content": {"title": None, "content": "内容"}}
        result = aggregator._compute_history(raw_data)
        assert result["title"] == "🐟 摸鱼小贴士"
        assert result["content"] == "内容"
        assert "type" not in result

    def test_news_list_new_api_format(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_news_list 处理新 API 的字典格式。"""
        raw_data = {"news": {"data": {"news": ["新闻1", "新闻2", "新闻3"]}}}
        result = aggregator._compute_news_list(raw_data)
        assert len(result) == 3
        assert result[0] == {"num": 1, "text": "新闻1"}
        assert result[2] == {"num": 3, "text": "新闻3"}

    @pytest.mark.parametrize("include_source_time", [False, True])
    def test_news_meta_prefers_source_time_then_api_refresh_time(
        self, aggregator: DomainDataAggregator, include_source_time: bool
    ) -> None:
        """当前上游响应同时包含来源时间戳和 API 刷新时间戳。"""
        data: dict[str, Any] = {
            "date": "2026-02-04",
            "api_updated": "2026-02-04T11:00:00+08:00",
            "api_updated_at": 1770174000000,
        }
        if include_source_time:
            data.update({
                "updated": "2026-02-04T10:30:00+08:00",
                "updated_at": 1770172200000,
            })

        result = aggregator._compute_news_meta({"news": {"data": data}})

        assert result == {
            "date": "2026-02-04",
            "updated": (
                "2026-02-04T10:30:00+08:00" if include_source_time
                else "2026-02-04T11:00:00+08:00"
            ),
            "updated_at": 1770172200000 if include_source_time else 1770174000000,
        }

    def test_news_meta_empty_when_no_news(self, aggregator: DomainDataAggregator) -> None:
        """测试 _compute_news_meta 在无新闻数据时返回空字典。"""
        result = aggregator._compute_news_meta({})
        assert result == {}


class TestComputeHolidays:
    """测试 _compute_holidays 的节假日合并与去重逻辑。"""

    @pytest.fixture
    def aggregator(self) -> DomainDataAggregator:
        return DomainDataAggregator()

    @pytest.fixture
    def now(self) -> datetime:
        return datetime(2026, 2, 4, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    @pytest.mark.parametrize("kind", ["lunar", "solar"])
    def test_festival_fields_are_validated_before_use(
        self, aggregator: DomainDataAggregator, now: datetime, kind: str
    ) -> None:
        """跳过字段缺失的节日，数字字符串仍可正常使用。"""
        festivals = [
            {"name": None, "solar_date": "2026-02-14", "days_left": 10},
            {"name": 42, "solar_date": "2026-02-14", "days_left": 10},
            {"name": "Missing date", "solar_date": None, "days_left": 10},
            {"name": "Missing countdown", "solar_date": "2026-02-14", "days_left": None},
            {"name": "Bad countdown", "solar_date": "2026-02-14", "days_left": "bad"},
            {"name": "Valid", "solar_date": "2026-02-04", "days_left": "0"},
        ]
        with (
            patch(
                "app.services.compute.CalendarService.get_upcoming_lunar_festivals",
                return_value=festivals if kind == "lunar" else [],
            ),
            patch(
                "app.services.compute.CalendarService.get_upcoming_solar_festivals",
                return_value=festivals if kind == "solar" else [],
            ),
        ):
            result = aggregator._compute_holidays(now, {})

        assert len(result) == 1
        assert result[0]["name"] == "Valid"
        assert result[0]["days_left"] == 0

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_legal_holidays_basic(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """测试法定节假日的基本处理。"""
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
        """测试特殊节假日名称的简化。"""
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
        """测试移除地区性节假日的通用前缀。"""
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
        """测试农历节日与法定节假日之间的去重。"""
        raw_data = {
            "holidays": [
                {"name": "春节", "start_date": "2026-01-29", "end_date": "2026-02-04", "duration": 7, "days_left": 0}
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        names = [h["name"] for h in result]
        # “春节假期”应被去重（核心字“春”与“春节”匹配）
        assert "春节" in names
        assert "春节假期" not in names
        # “元宵节”不应被去重
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
        """测试添加不重复的公历节日。"""
        result = aggregator._compute_holidays(now, {})
        names = [h["name"] for h in result]
        assert "情人节" in names
        assert "妇女节" in names
        assert all(h["is_legal_holiday"] is False for h in result)

    @patch(
        "app.services.compute.CalendarService.get_upcoming_solar_festivals",
        return_value=[{"name": "植树节", "solar_date": "2026-03-12", "days_left": 36}],
    )
    @patch(
        "app.services.compute.CalendarService.get_upcoming_lunar_festivals",
        return_value=[{"name": "元宵节", "solar_date": "2026-03-03", "days_left": 27}],
    )
    def test_festivals_are_not_off_days_but_legal_holidays_keep_flag(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """测试只有法定节假日数据才能将条目标记为休息日。"""
        raw_data = {
            "holidays": [
                {
                    "name": "春节",
                    "start_date": "2026-02-17",
                    "days_left": 13,
                    "is_off_day": True,
                },
                {
                    "name": "春节调休",
                    "start_date": "2026-02-14",
                    "days_left": 10,
                    "is_off_day": False,
                },
            ]
        }

        result = aggregator._compute_holidays(now, raw_data)
        by_name = {holiday["name"]: holiday for holiday in result}

        assert by_name["元宵节"]["is_off_day"] is False
        assert by_name["植树节"]["is_off_day"] is False
        assert by_name["春节"]["is_off_day"] is True
        assert by_name["春节调休"]["is_off_day"] is False

    @pytest.mark.parametrize("reverse_order", [False, True])
    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_duplicate_simplified_legal_names_keep_nearest_occurrence(
        self,
        mock_lunar: Any,
        mock_solar: Any,
        aggregator: DomainDataAggregator,
        now: datetime,
        reverse_order: bool,
    ) -> None:
        """测试简化后的法定节假日重名时保留最近的条目及其元数据。"""
        nearer = {
            "name": "宁夏古尔邦节",
            "start_date": "2026-06-01",
            "end_date": "2026-06-02",
            "duration": "2",
            "days_left": "117",
            "is_off_day": False,
        }
        farther = {
            "name": "新疆古尔邦节",
            "start_date": "2027-05-21",
            "end_date": "2027-05-23",
            "duration": 3,
            "days_left": 471,
            "is_off_day": True,
        }
        holidays = [farther, nearer] if reverse_order else [nearer, farther]

        result = aggregator._compute_holidays(now, {"holidays": holidays})

        assert result == [
            {
                "name": "古尔邦节",
                "start_date": "2026-06-01",
                "end_date": "2026-06-02",
                "duration": 2,
                "days_left": 117,
                "is_legal_holiday": True,
                "color": "#E67E22",
                "is_off_day": False,
            }
        ]

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_non_dict_entries_filtered(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """测试过滤 holidays 中的非字典条目。"""
        raw_data = {"holidays": ["invalid", 42, {"name": "清明节", "start_date": "2026-04-05", "days_left": 60}]}
        result = aggregator._compute_holidays(now, raw_data)
        assert len(result) == 1
        assert result[0]["name"] == "清明节"

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_missing_fields_filtered(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """测试过滤缺少必填字段的条目。"""
        raw_data = {
            "holidays": [
                {"name": "", "start_date": "2026-04-05"},  # 名称为空
                {"name": "Test", "start_date": ""},  # start_date 为空
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
        """测试 is_off_day 的布尔值标准化。"""
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
        assert by_name["Holiday3"]["is_off_day"] is True  # None 默认视为 True

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_sorted_by_days_left_and_max_10(
        self, mock_lunar: Any, mock_solar: Any, aggregator: DomainDataAggregator, now: datetime
    ) -> None:
        """测试结果按 days_left 排序且最多保留 10 条。"""
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
        """测试将 duration 和 days_left 转为整数。"""
        raw_data = {
            "holidays": [
                {"name": "Test", "start_date": "2026-03-01", "duration": "3", "days_left": "25"},
            ]
        }
        result = aggregator._compute_holidays(now, raw_data)
        assert result[0]["duration"] == 3
        assert result[0]["days_left"] == 25


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
@pytest.mark.parametrize("state", ["empty", "invalid", "flagged", "closed"])
def test_stock_templates_distinguish_failure_from_closed_markets(
    template_name: str, state: str
) -> None:
    """两套模板均显示获取失败，同时保留真实的休市日条目。"""
    from app.services.compute import DataComputer
    from app.services.renderer import format_datetime

    now = datetime(2026, 3, 12, 10, tzinfo=timezone(timedelta(hours=8)))
    item = {"name": "测试指数", "price": 100, "is_trading_day": False}
    items = [] if state == "empty" else ([None, "invalid"] if state == "invalid" else [item])
    raw_data = {
        "holidays": [],
        "stock_indices": {"items": items, "is_data_missing": state == "flagged"},
    }
    data = DataComputer().compute(raw_data, now=now)
    data["holidays"] = []
    data["show_stock"] = True
    templates_dir = Path(__file__).resolve().parents[2] / "templates"
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
    env.filters["format_datetime"] = format_datetime

    html = env.get_template(template_name).render(data)

    assert ("数据更新失败" in html) is (state != "closed")
    assert ("测试指数" in html) is (state == "closed")
    assert "今日休市" not in html
    assert "股市休息中" not in html
    if state == "closed":
        assert "休市" in html


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
@pytest.mark.parametrize(("is_legal", "is_off_day"), [(False, False), (True, False), (True, True)])
def test_holiday_templates_distinguish_festivals_from_makeup_work_days(
    template_name: str, is_legal: bool, is_off_day: bool
) -> None:
    """测试两套节假日布局仅为法定补班日显示补班标签。"""
    from app.services.compute import DataComputer
    from app.services.renderer import format_datetime

    now = datetime(2026, 3, 12, 10, tzinfo=timezone(timedelta(hours=8)))
    data = DataComputer().compute({}, now=now)
    data["holidays"] = [
        {
            "name": name,
            "start_date": "2026-03-12",
            "end_date": "2026-03-12",
            "duration": 1,
            "days_left": 0,
            "is_legal_holiday": is_legal,
            "is_off_day": is_off_day,
        }
        for name in ("植树节", "教师节")
    ]
    templates_dir = Path(__file__).resolve().parents[2] / "templates"
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
    env.filters["format_datetime"] = format_datetime

    html = env.get_template(template_name).render(data)

    makeup_label = 'aria-label="补班"' if template_name == "moyuren.html" else ">补班</span>"
    assert (makeup_label in html) is (is_legal and not is_off_day)
    if template_name == "moyuren.html":
        assert ('class="holiday-item workday' in html) is (is_legal and not is_off_day)
        assert ("假期中" in html) is is_off_day
        assert ("今日节日" in html) is (not is_legal)
    else:
        style = "normal" if not is_legal else ("legal" if is_off_day else "work")
        assert f'class="holiday-hero hc-{style}"' in html
        assert f'class="holiday-card hc-{style}"' in html


class TestDataComputer:
    """测试模板上下文计算。"""

    def test_compute_uses_provided_snapshot(self) -> None:
        """测试计算使用传入的日期，不读取当前时间。"""
        from app.services.compute import DataComputer

        snapshot = datetime(2026, 2, 5, 2, tzinfo=timezone(timedelta(hours=8)))
        with patch("app.services.compute.now_business") as mock_now:
            result = DataComputer().compute({"kfc_copy": "Thursday content"}, now=snapshot)

        assert result["date"]["day"] == "5"
        assert result["date"]["week_cn"] == "星期四"
        assert result["kfc_content"]["content"] == "Thursday content"
        mock_now.assert_not_called()

    def test_default_init(self) -> None:
        """测试默认初始化创建 DomainDataAggregator 和 TemplateAdapter。"""
        from app.services.compute import DataComputer
        computer = DataComputer()
        assert isinstance(computer.aggregator, DomainDataAggregator)
        assert isinstance(computer.adapter, TemplateAdapter)

    @pytest.mark.parametrize(
        ("value", "limit", "expected"),
        [
            ("cat", 5, "cat"),
            ("exact", 5, "exact"),
            ("longer", 5, "long…"),
            ("longer", 1, "…"),
        ],
    )
    def test_truncate_boundary_cases(self, value: str, limit: int, expected: str) -> None:
        """截断时保留较短和等长文本，并将省略号计入长度上限。"""
        assert DomainDataAggregator._truncate(value, limit) == expected

    def test_custom_init(self) -> None:
        """测试使用传入实例进行自定义初始化。"""
        from app.services.compute import DataComputer
        agg = DomainDataAggregator()
        adp = TemplateAdapter()
        computer = DataComputer(aggregator=agg, adapter=adp)
        assert computer.aggregator is agg
        assert computer.adapter is adp

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_external_text_caps_cover_rendered_and_stored_fields(
        self, mock_lunar: Any, mock_solar: Any
    ) -> None:
        """持久化前，对所有外部文本字段应用配置的长度上限。"""
        limits = TextLimitsConfig(
            news_item=5,
            news_items=2,
            fun_content=6,
            kfc_text=7,
            daily_english_definition=8,
            daily_english_translation=9,
            holiday_name=4,
        )
        raw_data = {
            "news": {"data": {"news": ["abcdefgh", "short", "ignored"]}},
            "fun_content": {"title": "long title", "content": "long content"},
            "kfc_copy": "Thursday content",
            "daily_english": {
                "word": "hello",
                "definition": "definition text",
                "translation": "translation text",
            },
            "holidays": [
                {"name": "Long Holiday", "start_date": "2026-02-05", "days_left": 0}
            ],
        }
        snapshot = datetime(2026, 2, 5, 2, tzinfo=timezone(timedelta(hours=8)))

        with patch.object(
            CalendarService,
            "get_festivals",
            return_value={
                "festival_solar": "Solar Festival",
                "festival_lunar": "Lunar Festival",
                "legal_holiday": "Legal Holiday",
            },
        ):
            result = DataComputer(text_limits=limits).compute(raw_data, now=snapshot)

        assert result["news_list"] == [
            {"num": 1, "text": "abcd…"},
            {"num": 2, "text": "short"},
        ]
        assert result["history"] == {"title": "long …", "content": "long …"}
        assert result["kfc_content"]["content"] == "Thursd…"
        assert result["daily_english"]["definition"] == "definit…"
        assert result["daily_english"]["translation"] == "translat…"
        assert result["daily_english"]["word"] == "hello"
        assert result["holidays"][0]["name"] == "Lon…"
        assert result["date"]["festival_solar"] == "Sol…"
        assert result["date"]["festival_lunar"] == "Lun…"
        assert result["date"]["legal_holiday"] == "Leg…"
        assert raw_data["news"]["data"]["news"][0] == "abcdefgh"
        assert raw_data["daily_english"]["definition"] == "definition text"
        assert raw_data["holidays"][0]["name"] == "Long Holiday"

    @patch("app.services.compute.CalendarService.get_upcoming_solar_festivals", return_value=[])
    @patch("app.services.compute.CalendarService.get_upcoming_lunar_festivals", return_value=[])
    def test_default_text_limits_and_short_values_are_preserved(
        self, mock_lunar: Any, mock_solar: Any
    ) -> None:
        """各类文本均使用默认长度上限，较短的文本保持不变。"""
        limits = TextLimitsConfig()
        raw_data = {
            "news": {
                "data": {
                    "news": [
                        "short news",
                        "n" * limits.news_item,
                        "n" * (limits.news_item + 1),
                        *[f"item-{index}" for index in range(limits.news_items)],
                    ]
                }
            },
            "fun_content": {
                "title": "short title",
                "content": "f" * (limits.fun_content + 1),
            },
            "kfc_copy": "k" * (limits.kfc_text + 1),
            "daily_english": {
                "word": "hello",
                "definition": "definition",
                "translation": "t" * (limits.daily_english_translation + 1),
            },
            "holidays": [
                {"name": "Holiday", "start_date": "2026-02-05", "days_left": 0},
                {
                    "name": "h" * (limits.holiday_name + 1),
                    "start_date": "2026-02-06",
                    "days_left": 1,
                },
            ],
        }
        snapshot = datetime(2026, 2, 5, 2, tzinfo=timezone(timedelta(hours=8)))

        with patch.object(
            CalendarService,
            "get_festivals",
            return_value={
                "festival_solar": "Festival",
                "festival_lunar": "l" * (limits.holiday_name + 1),
                "legal_holiday": None,
            },
        ):
            result = DataComputer().compute(raw_data, now=snapshot)

        assert len(result["news_list"]) == limits.news_items
        assert result["news_list"][0]["text"] == "short news"
        assert result["news_list"][1]["text"] == "n" * limits.news_item
        assert result["news_list"][2]["text"] == "n" * (limits.news_item - 1) + "…"
        assert result["history"]["title"] == "short title"
        assert result["history"]["content"] == "f" * (limits.fun_content - 1) + "…"
        assert result["kfc_content"]["content"] == "k" * (limits.kfc_text - 1) + "…"
        assert result["daily_english"]["definition"] == "definition"
        assert result["daily_english"]["translation"] == (
            "t" * (limits.daily_english_translation - 1) + "…"
        )
        assert result["holidays"][0]["name"] == "Holiday"
        assert result["holidays"][1]["name"] == "h" * (limits.holiday_name - 1) + "…"
        assert result["date"]["festival_solar"] == "Festival"
        assert result["date"]["festival_lunar"] == (
            "l" * (limits.holiday_name - 1) + "…"
        )
        assert result["date"]["legal_holiday"] is None

    def test_compute_delegates(self) -> None:
        """测试 compute 委托聚合器和适配器处理。"""
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
