"""app/services/kfc.py 的 KFC 疯狂星期四服务测试。"""

import logging
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import CrazyThursdaySource, InstancesConfig
from app.services.kfc import CachedKfcService, KfcService


class TestKfcService:
    """KfcService 类测试。"""

    @pytest.fixture
    def enabled_config(self) -> CrazyThursdaySource:
        """创建已启用的 KFC 配置。"""
        return CrazyThursdaySource(enabled=True, url="https://api.example.com/kfc", timeout_sec=5)

    @pytest.fixture
    def disabled_config(self) -> CrazyThursdaySource:
        """创建已禁用的 KFC 配置。"""
        return CrazyThursdaySource(enabled=False, url="https://api.example.com/kfc", timeout_sec=5)

    @pytest.fixture
    def service(self, enabled_config: CrazyThursdaySource) -> KfcService:
        """使用已启用的配置创建 KfcService 实例。"""
        return KfcService(config=enabled_config)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_success_viki_format(self, service: KfcService) -> None:
        """验证成功获取 Viki API 格式的内容。"""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": "V我50"}})
        )

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_reuses_injected_client_with_service_timeout(
        self, enabled_config: CrazyThursdaySource
    ) -> None:
        """注入的客户端保持打开，每次请求使用服务配置的超时时间。"""
        response = MagicMock()
        response.json.return_value = {"code": 200, "data": {"kfc": "V我50"}}
        response.raise_for_status.return_value = None
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=response)
        client.aclose = AsyncMock()
        service = KfcService(enabled_config, http_client=client)

        assert await service.fetch_kfc_copy() == "V我50"
        assert client.get.await_args.kwargs["timeout"] == httpx.Timeout(5)
        client.aclose.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_success_string_data(self, service: KfcService) -> None:
        """验证成功获取字符串格式的 data 内容。"""
        respx.get("https://api.example.com/kfc").mock(return_value=Response(200, json={"code": 200, "data": "V我50"}))

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_success_text_field(self, service: KfcService) -> None:
        """验证成功获取 text 字段中的内容。"""
        respx.get("https://api.example.com/kfc").mock(return_value=Response(200, json={"text": "V我50"}))

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_handles_escaped_newlines(self, service: KfcService) -> None:
        """验证内容中的转义换行符得到正确处理。"""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": "Line1\\nLine2"}})
        )

        result = await service.fetch_kfc_copy()

        assert result == "Line1\nLine2"

    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_disabled_returns_none(self, disabled_config: CrazyThursdaySource) -> None:
        """验证禁用配置时不发送请求并返回 None。"""
        service = KfcService(config=disabled_config)

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_timeout(self, service: KfcService) -> None:
        """验证超时时返回 None。"""
        respx.get("https://api.example.com/kfc").mock(side_effect=httpx.TimeoutException("Timeout"))

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_http_error(self, service: KfcService) -> None:
        """验证 HTTP 错误时返回 None。"""
        respx.get("https://api.example.com/kfc").mock(return_value=Response(500))

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_empty_content(self, service: KfcService) -> None:
        """验证内容为空字符串时返回 None。"""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": ""}})
        )

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_null_content(self, service: KfcService) -> None:
        """验证内容为 null 时返回 None。"""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": None}})
        )

        result = await service.fetch_kfc_copy()

        assert result is None

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            {},
            {"data": None},
            {"data": []},
            {"data": {"kfc": None}},
            {"data": {"kfc": []}},
            {"text": {"value": "V我50"}},
        ],
    )
    def test_parse_kfc_response_rejects_unusable_shapes(
        self, service: KfcService, payload: object
    ) -> None:
        """空值与结构异常的响应返回 None，不抛出异常。"""
        assert service._parse_kfc_response(payload) is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_invalid_json(self, service: KfcService) -> None:
        """验证 JSON 无效时返回 None。"""
        respx.get("https://api.example.com/kfc").mock(return_value=Response(200, content=b"not json"))

        result = await service.fetch_kfc_copy()

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_strips_whitespace(self, service: KfcService) -> None:
        """验证去除内容首尾空白。"""
        respx.get("https://api.example.com/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": "  V我50  "}})
        )

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_kfc_copy_string_response(self, service: KfcService) -> None:
        """验证正确处理字符串响应。"""
        respx.get("https://api.example.com/kfc").mock(return_value=Response(200, json="V我50"))

        result = await service.fetch_kfc_copy()

        assert result == "V我50"


class TestCachedKfcService:
    """CachedKfcService 类测试。"""

    @pytest.fixture
    def config(self) -> CrazyThursdaySource:
        """创建已启用的 KFC 配置。"""
        return CrazyThursdaySource(enabled=True, url="https://api.example.com/kfc", timeout_sec=5)

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """创建临时缓存目录。"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def logger_instance(self) -> logging.Logger:
        """创建日志记录器实例。"""
        return logging.getLogger("test_kfc")

    @pytest.fixture
    def service(
        self, config: CrazyThursdaySource, logger_instance: logging.Logger, cache_dir: Path
    ) -> CachedKfcService:
        """创建 CachedKfcService 实例。"""
        return CachedKfcService(config=config, logger=logger_instance, cache_dir=cache_dir)

    def test_forwards_injected_client(
        self,
        config: CrazyThursdaySource,
        logger_instance: logging.Logger,
        cache_dir: Path,
    ) -> None:
        """缓存包装层将共享客户端传给其网络服务。"""
        client = MagicMock(spec=httpx.AsyncClient)

        service = CachedKfcService(
            config, logger_instance, cache_dir, http_client=client
        )

        assert service._service._http_client is client

    @pytest.mark.asyncio
    async def test_get_returns_none_on_non_thursday(self, service: CachedKfcService) -> None:
        """验证非周四时 get() 返回 None。"""
        # mock today_business，使其返回非周四日期（周一 = 0）
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 2)  # 周一
            result = await service.get()
            assert result is None

    @pytest.mark.asyncio
    async def test_get_fetches_on_thursday(self, service: CachedKfcService) -> None:
        """验证周四时 get() 获取内容。"""
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # 周四
            with patch.object(service._service, "fetch_kfc_copy", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = "V我50"
                result = await service.get()
                assert result == "V我50"
                mock_fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_uses_cache_on_thursday(
        self, config: CrazyThursdaySource, logger_instance: logging.Logger, cache_dir: Path
    ) -> None:
        """验证周四后续调用 get() 时使用缓存。"""
        service = CachedKfcService(config=config, logger=logger_instance, cache_dir=cache_dir)

        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # 周四
            with patch.object(service._service, "fetch_kfc_copy", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = "V我50"

                # 首次调用应获取内容
                result1 = await service.get()
                assert result1 == "V我50"
                assert mock_fetch.call_count == 1

                # 第二次调用应使用缓存
                result2 = await service.get()
                assert result2 == "V我50"
                assert mock_fetch.call_count == 1  # 仍为 1，使用了缓存

    @pytest.mark.asyncio
    async def test_get_force_refresh_on_thursday(
        self, config: CrazyThursdaySource, logger_instance: logging.Logger, cache_dir: Path
    ) -> None:
        """验证周四 get() 使用 force_refresh 时绕过缓存。"""
        service = CachedKfcService(config=config, logger=logger_instance, cache_dir=cache_dir)

        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # 周四
            with patch.object(service._service, "fetch_kfc_copy", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = "V我50"

                # 首次调用
                await service.get()
                assert mock_fetch.call_count == 1

                # 强制刷新
                mock_fetch.return_value = "新文案"
                result = await service.get(force_refresh=True)
                assert result == "新文案"
                assert mock_fetch.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_fresh_returns_none_on_non_thursday(self, service: CachedKfcService) -> None:
        """验证非周四时 fetch_fresh() 返回 None。"""
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 2)  # 周一
            result = await service.fetch_fresh()
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_fresh_fetches_on_thursday(self, service: CachedKfcService) -> None:
        """验证周四时 fetch_fresh() 获取内容。"""
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # 周四
            with patch.object(service._service, "fetch_kfc_copy", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = "V我50"
                result = await service.fetch_fresh()
                assert result == "V我50"

    @pytest.mark.asyncio
    async def test_fetch_fresh_handles_exception(self, service: CachedKfcService) -> None:
        """验证 fetch_fresh() 妥善处理异常。"""
        with patch("app.services.kfc.today_business") as mock_today:
            mock_today.return_value = date(2026, 2, 5)  # 周四
            with patch.object(service._service, "fetch_kfc_copy", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.side_effect = Exception("Network error")
                result = await service.fetch_fresh()
                assert result is None


class TestKfcInstanceSwitching:
    """KfcService 的 60s 公共实例切换测试。"""

    @pytest.fixture
    def config(self) -> CrazyThursdaySource:
        """创建已启用且支持公共实例回退的 KFC 配置。"""
        return CrazyThursdaySource(
            enabled=True,
            url="https://api.example.com/v2/kfc",
            timeout_sec=5,
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_switches_to_instance_when_primary_blocked(
        self, config: CrazyThursdaySource
    ) -> None:
        """验证主端点被阻止时回退到公共实例。"""
        service = KfcService(
            config=config, instances=InstancesConfig(urls=["https://inst.example.com"])
        )
        respx.get("https://api.example.com/v2/kfc").mock(return_value=Response(403))
        respx.get("https://inst.example.com/v2/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {"kfc": "V我50"}})
        )

        result = await service.fetch_kfc_copy()

        assert result == "V我50"

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_none_when_primary_and_instance_fail(
        self, config: CrazyThursdaySource
    ) -> None:
        """验证主端点和公共实例均不可用时返回 None。"""
        service = KfcService(
            config=config, instances=InstancesConfig(urls=["https://inst.example.com"])
        )
        respx.get("https://api.example.com/v2/kfc").mock(return_value=Response(403))
        respx.get("https://inst.example.com/v2/kfc").mock(
            return_value=Response(200, json={"code": 200, "data": {}})
        )

        result = await service.fetch_kfc_copy()

        assert result is None
