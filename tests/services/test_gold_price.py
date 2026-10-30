"""测试 app/services/gold_price.py 与金价计算。"""

import logging
from pathlib import Path

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import GoldPriceSource, InstancesConfig
from app.services.compute import DomainDataAggregator
from app.services.gold_price import CachedGoldPriceService, GoldPriceService


class TestGoldPriceServiceParseResponse:
    """测试 GoldPriceService._parse_response。"""

    def _make_service(self):
        config = GoldPriceSource(url="https://example.com/gold", timeout_sec=5)
        return GoldPriceService(config=config, logger=logging.getLogger("test"))

    def test_parse_valid_response(self) -> None:
        """测试解析有效的金价响应。"""
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
        """测试非字典响应返回 None。"""
        service = self._make_service()
        assert service._parse_response("not a dict") is None
        assert service._parse_response([1, 2, 3]) is None
        assert service._parse_response(None) is None

    def test_parse_missing_metals(self) -> None:
        """测试 metals 字段缺失或无效的响应。"""
        service = self._make_service()
        assert service._parse_response({"data": None}) is None
        assert service._parse_response({"data": {}}) is None
        assert service._parse_response({"data": {"metals": "not a list"}}) is None

    def test_parse_no_today_gold_price(self) -> None:
        """测试响应中没有“今日金价”条目的情况。"""
        service = self._make_service()
        data = {"data": {"metals": [{"name": "白银", "today_price": "5.00"}]}}
        assert service._parse_response(data) is None

    def test_parse_empty_metals_list(self) -> None:
        """测试 metals 列表为空的响应。"""
        service = self._make_service()
        data = {"data": {"metals": []}}
        assert service._parse_response(data) is None

    def test_parse_default_unit(self) -> None:
        """测试未提供单位时使用默认单位。"""
        service = self._make_service()
        data = {"data": {"metals": [{"name": "今日金价", "today_price": "680.00", "sell_price": "670.00"}]}}
        result = service._parse_response(data)
        assert result["unit"] == "元/克"


class TestComputeGoldPrice:
    """测试 DomainDataAggregator._compute_gold_price。"""

    def _make_aggregator(self):
        return DomainDataAggregator()

    def test_compute_valid_gold_price(self) -> None:
        """测试计算有效的金价数据。"""
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
        """测试金价下跌趋势的计算。"""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "660.00", "sell_price": "670.00", "unit": "元/克"}}
        result = agg._compute_gold_price(raw_data)
        assert result["trend"] == "down"
        assert "-" in result["spread_pct"]

    def test_compute_gold_price_flat(self) -> None:
        """测试金价持平趋势的计算。"""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "670.00", "sell_price": "670.00", "unit": "元/克"}}
        result = agg._compute_gold_price(raw_data)
        assert result["trend"] == "flat"
        assert result["spread_pct"] == "+0.00%"

    def test_compute_gold_price_missing_data(self) -> None:
        """测试金价数据缺失或无效的情况。"""
        agg = self._make_aggregator()
        assert agg._compute_gold_price({}) is None
        assert agg._compute_gold_price({"gold_price": None}) is None
        assert agg._compute_gold_price({"gold_price": "not a dict"}) is None

    def test_compute_gold_price_invalid_values(self) -> None:
        """测试金价包含非数值的情况。"""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "abc", "sell_price": "670.00"}}
        assert agg._compute_gold_price(raw_data) is None

    def test_compute_gold_price_missing_keys(self) -> None:
        """测试金价缺少必需字段的情况。"""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "680.00"}}  # 缺少 sell_price
        assert agg._compute_gold_price(raw_data) is None

    def test_compute_gold_price_zero_sell_price(self) -> None:
        """测试卖出价为零的边界情况。

        趋势取决于差值（今日价减卖出价），因此 680 - 0 = 680 > 0，结果为 "up"。
        sell_price 为 0 时，change_pct 设为 0，避免除以零。
        """
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "680.00", "sell_price": "0"}}
        result = agg._compute_gold_price(raw_data)
        assert result is not None
        assert result["spread_pct"] == "+0.00%"
        assert result["trend"] == "up"  # 差值为 680 > 0，因此为 "up"

    def test_compute_gold_price_default_unit(self) -> None:
        """测试未提供单位时使用默认单位。"""
        agg = self._make_aggregator()
        raw_data = {"gold_price": {"today_price": "680.00", "sell_price": "670.00"}}
        result = agg._compute_gold_price(raw_data)
        assert result["unit"] == "元/克"


class TestGoldPriceServiceFetch:
    """测试 GoldPriceService.fetch_gold_price 异步方法。"""

    def _make_config(self):
        """创建 mock 配置。"""
        return GoldPriceSource(url="https://example.com/gold", timeout_sec=5)

    @respx.mock
    async def test_fetch_success_with_injected_client(self) -> None:
        """测试使用注入的 HTTP 客户端成功获取数据。"""
        config = self._make_config()
        respx.get(config.url).mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "metals": [
                            {"name": "今日金价", "today_price": "680.00", "sell_price": "670.00", "unit": "元/克"}
                        ]
                    }
                },
            )
        )

        async with httpx.AsyncClient() as client:
            service = GoldPriceService(config, http_client=client, logger=logging.getLogger("test"))
            result = await service.fetch_gold_price()

        assert result is not None
        assert result["today_price"] == "680.00"
        assert result["sell_price"] == "670.00"
        assert result["unit"] == "元/克"

    @respx.mock
    async def test_fetch_success_without_injected_client(self) -> None:
        """测试未注入客户端时，创建临时客户端成功获取数据。"""
        config = self._make_config()
        respx.get(config.url).mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "metals": [
                            {"name": "今日金价", "today_price": "690.00", "sell_price": "680.00"}
                        ]
                    }
                },
            )
        )

        service = GoldPriceService(config, logger=logging.getLogger("test"))
        result = await service.fetch_gold_price()

        assert result is not None
        assert result["today_price"] == "690.00"
        assert result["sell_price"] == "680.00"
        assert result["unit"] == "元/克"  # 默认单位

    @respx.mock
    async def test_fetch_timeout_returns_none(self) -> None:
        """测试发生 TimeoutException 时返回 None。"""
        config = self._make_config()
        respx.get(config.url).mock(side_effect=httpx.TimeoutException("timeout"))

        service = GoldPriceService(config, logger=logging.getLogger("test"))
        result = await service.fetch_gold_price()

        assert result is None

    @respx.mock
    async def test_fetch_http_status_error_returns_none(self) -> None:
        """测试发生 HTTPStatusError 时返回 None。"""
        config = self._make_config()
        respx.get(config.url).mock(return_value=Response(500))

        service = GoldPriceService(config, logger=logging.getLogger("test"))
        result = await service.fetch_gold_price()

        assert result is None

    @respx.mock
    async def test_fetch_generic_exception_returns_none(self) -> None:
        """测试发生一般异常时返回 None。"""
        config = self._make_config()
        respx.get(config.url).mock(side_effect=Exception("network error"))

        service = GoldPriceService(config, logger=logging.getLogger("test"))
        result = await service.fetch_gold_price()

        assert result is None

    @respx.mock
    async def test_fetch_with_injected_client_uses_same_client(self) -> None:
        """测试复用注入的客户端，而非创建新客户端。"""
        config = self._make_config()
        route = respx.get(config.url).mock(
            return_value=Response(
                200,
                json={"data": {"metals": [{"name": "今日金价", "today_price": "700.00", "sell_price": "690.00"}]}},
            )
        )

        async with httpx.AsyncClient() as client:
            service = GoldPriceService(config, http_client=client, logger=logging.getLogger("test"))
            # 多次获取数据应复用同一客户端
            await service.fetch_gold_price()
            await service.fetch_gold_price()

        assert route.call_count == 2

    @respx.mock
    async def test_fetch_switches_to_public_instance(self) -> None:
        """测试主源被阻止时回退到公共实例。"""
        config = GoldPriceSource(url="https://api.example.com/gold", timeout_sec=5)
        instances = InstancesConfig(urls=["https://inst.example.com"])
        respx.get("https://api.example.com/gold").mock(return_value=Response(403))
        respx.get("https://inst.example.com/gold").mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "metals": [
                            {"name": "今日金价", "today_price": "680.00", "sell_price": "670.00", "unit": "元/克"}
                        ]
                    }
                },
            )
        )

        async with httpx.AsyncClient() as client:
            service = GoldPriceService(
                config,
                http_client=client,
                logger=logging.getLogger("test"),
                instances=instances,
            )
            result = await service.fetch_gold_price()

        assert result == {"today_price": "680.00", "sell_price": "670.00", "unit": "元/克"}

    @respx.mock
    async def test_null_primary_data_switches_to_public_instance(self) -> None:
        """主源数据为空时回退到可用的公共实例。"""
        config = GoldPriceSource(url="https://api.example.com/gold", timeout_sec=5)
        instances = InstancesConfig(urls=["https://inst.example.com"])
        primary_route = respx.get("https://api.example.com/gold").mock(
            return_value=Response(200, json={"data": None})
        )
        instance_route = respx.get("https://inst.example.com/gold").mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "metals": [
                            {
                                "name": "今日金价",
                                "today_price": "681.00",
                                "sell_price": "671.00",
                                "unit": "元/克",
                            }
                        ]
                    }
                },
            )
        )

        async with httpx.AsyncClient() as client:
            service = GoldPriceService(
                config,
                http_client=client,
                logger=logging.getLogger("test"),
                instances=instances,
            )
            result = await service.fetch_gold_price()

        assert result == {
            "today_price": "681.00",
            "sell_price": "671.00",
            "unit": "元/克",
        }
        assert primary_route.called
        assert instance_route.called

    @respx.mock
    async def test_fetch_returns_none_when_all_endpoints_unusable(self) -> None:
        """测试主源和公共实例均不可用时返回 None。"""
        config = GoldPriceSource(url="https://api.example.com/gold", timeout_sec=5)
        instances = InstancesConfig(urls=["https://inst.example.com"])
        respx.get("https://api.example.com/gold").mock(return_value=Response(403))
        respx.get("https://inst.example.com/gold").mock(
            return_value=Response(200, json={"data": {"metals": []}})
        )

        async with httpx.AsyncClient() as client:
            service = GoldPriceService(
                config,
                http_client=client,
                logger=logging.getLogger("test"),
                instances=instances,
            )
            result = await service.fetch_gold_price()

        assert result is None


class TestCachedGoldPriceService:
    """测试 CachedGoldPriceService。"""

    def _make_config(self):
        """创建 mock 配置。"""
        return GoldPriceSource(url="https://example.com/gold", timeout_sec=5)

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """创建临时缓存目录。"""
        cache_dir = tmp_path / "daily"
        cache_dir.mkdir()
        return cache_dir

    @respx.mock
    def test_init_creates_service(self, cache_dir: Path) -> None:
        """测试构造函数正确初始化内部服务。"""
        config = self._make_config()
        logger = logging.getLogger("test")

        service = CachedGoldPriceService(config, logger, cache_dir)

        assert service._service is not None
        assert isinstance(service._service, GoldPriceService)

    async def test_init_with_http_client(self, cache_dir: Path) -> None:
        """测试构造函数将 HTTP 客户端传递给内部服务。"""
        config = self._make_config()
        logger = logging.getLogger("test")

        async with httpx.AsyncClient() as client:
            service = CachedGoldPriceService(config, logger, cache_dir, http_client=client)
            assert service._service._http_client is client

    @respx.mock
    async def test_fetch_fresh_delegates_to_service(self, cache_dir: Path) -> None:
        """测试 fetch_fresh 委托 GoldPriceService.fetch_gold_price 获取数据。"""
        config = self._make_config()
        respx.get(config.url).mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "metals": [
                            {"name": "今日金价", "today_price": "680.00", "sell_price": "670.00", "unit": "元/克"}
                        ]
                    }
                },
            )
        )

        service = CachedGoldPriceService(config, logging.getLogger("test"), cache_dir)
        result = await service.fetch_fresh()

        assert result is not None
        assert result["today_price"] == "680.00"

    @respx.mock
    async def test_fetch_fresh_returns_none_on_error(self, cache_dir: Path) -> None:
        """测试底层服务失败时 fetch_fresh 返回 None。"""
        config = self._make_config()
        respx.get(config.url).mock(side_effect=httpx.TimeoutException("timeout"))

        service = CachedGoldPriceService(config, logging.getLogger("test"), cache_dir)
        result = await service.fetch_fresh()

        assert result is None
