"""Tests for app/services/gold_price.py and gold price computation."""

import logging
from unittest.mock import MagicMock

import pytest

from app.services.compute import DomainDataAggregator
from app.services.gold_price import CachedGoldPriceService, GoldPriceService


class TestGoldPriceServiceParseResponse:
    """Tests for GoldPriceService._parse_response"""

    def _make_service(self):
        config = MagicMock()
        config.timeout_sec = 5
        config.url = "https://example.com/gold"
        return GoldPriceService(config=config, logger=logging.getLogger("test"))

    def test_parse_valid_response(self) -> None:
        """Test parsing valid gold price response."""
        service = self._make_service()
        data = {
            "data": {
                "metals": [
                    {"name": "今日金价", "today_price": "680.00", "sell_price": "670.00", "unit": "元/克"},
                    {"name": "其他", "today_price": "100.00"},
                ]
            }
        }
        result = service._parse_response(data)
        assert result == {"today_price": "680.00", "sell_price": "670.00", "unit": "元/克"}

    def test_parse_non_dict_response(self) -> None:
        """Test parsing non-dict response returns None."""
        service = self._make_service()
        assert service._parse_response("not a dict") is None
        assert service._parse_response([1, 2, 3]) is None
        assert service._parse_response(None) is None

    def test_parse_missing_metals(self) -> None:
        """Test parsing response with missing or invalid metals field."""
        service = self._make_service()
        assert service._parse_response({"data": {}}) is None
        assert service._parse_response({"data": {"metals": "not a list"}}) is None

    def test_parse_no_today_gold_price(self) -> None:
        """Test parsing response without '今日金价' entry."""
        service = self._make_service()
        data = {"data": {"metals": [{"name": "白银", "today_price": "5.00"}]}}
        assert service._parse_response(data) is None

    def test_parse_empty_metals_list(self) -> None:
        """Test parsing response with empty metals list."""
        service = self._make_service()
        data = {"data": {"metals": []}}
        assert service._parse_response(data) is None

    def test_parse_default_unit(self) -> None:
        """Test parsing response uses default unit when not provided."""
        service = self._make_service()
        data = {"data": {"metals": [{"name": "今日金价", "today_price": "680.00", "sell_price": "670.00"}]}}
        result = service._parse_response(data)
        assert result["unit"] == "元/克"


class TestComputeGoldPrice:
    """Tests for DomainDataAggregator._compute_gold_price"""

    def _make_aggregator(self):
        return DomainDataAggregator()

    def test_compute_valid_gold_price(self) -> None:
        """Test computing valid gold price data."""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "680.00", "sell_price": "670.00", "unit": "元/克"}}
        result = agg._compute_gold_price(raw_data)
        assert result is not None
        assert result["name"] == "今日金价"
        assert result["price"] == "680.00"
        assert result["unit"] == "元/克"
        assert result["trend"] == "up"
        assert "+" in result["spread_pct"]

    def test_compute_gold_price_down(self) -> None:
        """Test computing gold price with downward trend."""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "660.00", "sell_price": "670.00", "unit": "元/克"}}
        result = agg._compute_gold_price(raw_data)
        assert result["trend"] == "down"
        assert "-" in result["spread_pct"]

    def test_compute_gold_price_flat(self) -> None:
        """Test computing gold price with flat trend."""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "670.00", "sell_price": "670.00", "unit": "元/克"}}
        result = agg._compute_gold_price(raw_data)
        assert result["trend"] == "flat"
        assert result["spread_pct"] == "+0.00%"

    def test_compute_gold_price_missing_data(self) -> None:
        """Test computing gold price with missing or invalid data."""
        agg = self._make_aggregator()
        assert agg._compute_gold_price({}) is None
        assert agg._compute_gold_price({"gold_price": None}) is None
        assert agg._compute_gold_price({"gold_price": "not a dict"}) is None

    def test_compute_gold_price_invalid_values(self) -> None:
        """Test computing gold price with non-numeric values."""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "abc", "sell_price": "670.00"}}
        assert agg._compute_gold_price(raw_data) is None

    def test_compute_gold_price_missing_keys(self) -> None:
        """Test computing gold price with missing required keys."""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "680.00"}}  # missing sell_price
        assert agg._compute_gold_price(raw_data) is None

    def test_compute_gold_price_zero_sell_price(self) -> None:
        """Test computing gold price with zero sell price (edge case).

        Note: trend is based on change (today - sell), so 680 - 0 = 680 > 0 = "up".
        change_pct is 0 when sell_price is 0 to avoid division by zero.
        """
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "680.00", "sell_price": "0"}}
        result = agg._compute_gold_price(raw_data)
        assert result is not None
        assert result["spread_pct"] == "+0.00%"
        assert result["trend"] == "up"  # change=680 > 0, so "up"

    def test_compute_gold_price_default_unit(self) -> None:
        """Test computing gold price uses default unit when not provided."""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "680.00", "sell_price": "670.00"}}
        result = agg._compute_gold_price(raw_data)
        assert result["unit"] == "元/克"
