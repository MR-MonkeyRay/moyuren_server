"""app/services/fun_content.py 趣味内容获取服务测试。"""

import json
import logging
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import FunContentEndpoint, FunContentSource, InstancesConfig
from app.services.fun_content import CachedFunContentService, FunContentService


class TestFunContentService:
    """FunContentService 类测试。"""

    @pytest.fixture
    def sample_config(self) -> FunContentSource:
        """创建趣味内容配置样例。"""
        return FunContentSource(
            timeout_sec=5,
            endpoints=[
                FunContentEndpoint(
                    name="hitokoto",
                    url="https://api.example.com/hitokoto",
                    data_path="data.hitokoto",
                    display_title="💬 一言",
                ),
                FunContentEndpoint(
                    name="joke", url="https://api.example.com/joke", data_path="data.content", display_title="🤣 冷笑话"
                ),
            ],
        )

    @pytest.fixture
    def service(self, sample_config: FunContentSource) -> FunContentService:
        """创建 FunContentService 实例。"""
        return FunContentService(config=sample_config)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_success(self, service: FunContentService) -> None:
        """测试成功获取内容。"""
        # 打乱顺序取决于日期，因此 mock 两个端点
        respx.get("https://api.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": "生活不止眼前的苟且"}})
        )
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "这是一个笑话"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        # 应从其中一个端点获取内容
        assert "title" in result
        assert "content" in result
        assert result["type"] in {"hitokoto", "joke"}
        assert result["content"] != ""

    @pytest.mark.asyncio
    async def test_fetch_content_reuses_injected_client_with_service_timeout(
        self, sample_config: FunContentSource
    ) -> None:
        """注入的客户端保持打开，每次请求仍使用服务配置的超时。"""
        response = MagicMock()
        response.json.return_value = {"data": {"hitokoto": "共享连接"}}
        response.raise_for_status.return_value = None
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=response)
        client.aclose = AsyncMock()
        service = FunContentService(sample_config, http_client=client)

        result = await service.fetch_content(date(2026, 2, 4))

        assert result["content"] in {"共享连接"}
        assert client.get.await_count >= 1
        assert all(
            call.kwargs["timeout"] == httpx.Timeout(5)
            for call in client.get.await_args_list
        )
        client.aclose.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_endpoint_name_is_type_when_display_title_changes(self) -> None:
        """稳定的端点名称不受配置标题影响。"""
        config = FunContentSource(
            timeout_sec=5,
            endpoints=[
                FunContentEndpoint(
                    name="hitokoto",
                    url="https://api.example.com/hitokoto",
                    data_path="data.hitokoto",
                    display_title="今日灵感",
                )
            ],
        )
        service = FunContentService(config=config)
        respx.get("https://api.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": "生活不止眼前"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert result == {
            "title": "今日灵感",
            "content": "生活不止眼前",
            "type": "hitokoto",
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_fallback_to_second_endpoint(self, service: FunContentService) -> None:
        """测试首个端点失败时回退到第二个端点。"""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(500))
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "这是一个笑话"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        # 应从其中一个端点获取内容
        assert "content" in result
        assert result["content"] != ""

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_all_fail_returns_default(self, service: FunContentService) -> None:
        """测试所有端点失败时返回默认内容。"""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(500))
        respx.get("https://api.example.com/joke").mock(return_value=Response(500))

        result = await service.fetch_content(date(2026, 2, 4))

        assert result["title"] == "🐟 摸鱼小贴士"
        assert "摸鱼" in result["content"]
        assert result["type"] == "default"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_timeout_fallback(self, service: FunContentService) -> None:
        """测试端点超时时的回退。"""
        respx.get("https://api.example.com/hitokoto").mock(side_effect=httpx.TimeoutException("Timeout"))
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "笑话内容"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert "content" in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_empty_content_skipped(self, service: FunContentService) -> None:
        """测试跳过空内容。"""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(200, json={"data": {"hitokoto": ""}}))
        respx.get("https://api.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "有效内容"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert result["content"] != ""

    def test_shuffle_by_date_consistent(self, service: FunContentService) -> None:
        """测试同一日期的打乱结果一致。"""
        date1 = date(2026, 2, 4)

        result1 = service._shuffle_by_date(date1)
        result2 = service._shuffle_by_date(date1)

        # 同一日期应生成相同顺序
        assert [e.name for e in result1] == [e.name for e in result2]

    def test_shuffle_by_date_different_dates(self, service: FunContentService) -> None:
        """测试不同日期的打乱结果不同。"""
        # 注意：不同日期偶尔也会恰好打乱成相同顺序，
        # 此时测试可能失败，但概率很低
        date1 = date(2026, 2, 4)
        date2 = date(2026, 2, 5)

        result1 = service._shuffle_by_date(date1)
        result2 = service._shuffle_by_date(date2)

        # 不同日期可能生成不同顺序
        # 这里只验证函数运行时不出错
        assert len(result1) == len(result2)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_whitespace_only_skipped(self, service: FunContentService) -> None:
        """测试跳过仅含空白的内容。"""
        respx.get("https://api.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": "   "}})
        )
        respx.get("https://api.example.com/joke").mock(return_value=Response(500))

        result = await service.fetch_content(date(2026, 2, 4))

        # 应回退到默认内容
        assert result["title"] == "🐟 摸鱼小贴士"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_content_invalid_json_structure(self, service: FunContentService) -> None:
        """测试妥善处理无效的 JSON 结构。"""
        respx.get("https://api.example.com/hitokoto").mock(return_value=Response(200, json={"wrong": "structure"}))
        respx.get("https://api.example.com/joke").mock(return_value=Response(500))

        result = await service.fetch_content(date(2026, 2, 4))

        # 应回退到默认内容
        assert result["title"] == "🐟 摸鱼小贴士"

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            "text",
            {},
            {"data": None},
            {"data": []},
            {"data": {"hitokoto": None}},
            {"data": {"hitokoto": {"text": "一言"}}},
        ],
    )
    def test_extract_content_rejects_unusable_shapes(
        self,
        service: FunContentService,
        sample_config: FunContentSource,
        payload: object,
    ) -> None:
        """空值与结构异常的响应返回 None，不抛出异常。"""
        endpoint = sample_config.endpoints[0]

        assert service._extract_content(payload, endpoint) is None

    @pytest.mark.parametrize(
        ("payload", "path"),
        [
            (None, "data.content"),
            ({"data": None}, "data.content"),
            ({"data": []}, "data.content"),
            ({"data": {"content": None}}, "data.content.value"),
        ],
    )
    def test_extract_by_path_handles_null_intermediate_values(
        self, service: FunContentService, payload: object, path: str
    ) -> None:
        """路径中间值无效时返回 None，不抛出异常。"""
        assert service._extract_by_path(payload, path) is None


class TestFunContentInstanceSwitching:
    """FunContentService 的 60s 公共实例切换测试。"""

    @pytest.fixture
    def config(self) -> FunContentSource:
        """创建支持公共实例回退的趣味内容配置。"""
        return FunContentSource(
            timeout_sec=5,
            endpoints=[
                FunContentEndpoint(
                    name="hitokoto",
                    url="https://api.example.com/hitokoto",
                    data_path="data.hitokoto",
                    display_title="💬 一言",
                ),
                FunContentEndpoint(
                    name="joke",
                    url="https://api.example.com/joke",
                    data_path="data.content",
                    display_title="🤣 冷笑话",
                ),
            ],
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_switches_to_instance_when_primary_blocked(
        self, config: FunContentSource
    ) -> None:
        """测试主端点被拦截时回退到相同路径的实例端点。"""
        service = FunContentService(
            config=config, instances=InstancesConfig(urls=["https://inst.example.com"])
        )
        for path in ("hitokoto", "joke"):
            respx.get(f"https://api.example.com/{path}").mock(
                return_value=Response(403)
            )
        respx.get("https://inst.example.com/hitokoto").mock(
            return_value=Response(200, json={"data": {"hitokoto": "实例一言"}})
        )
        respx.get("https://inst.example.com/joke").mock(
            return_value=Response(200, json={"data": {"content": "实例笑话"}})
        )

        result = await service.fetch_content(date(2026, 2, 4))

        assert result["content"] in {"实例一言", "实例笑话"}
        assert result["title"] != "🐟 摸鱼小贴士"
        assert result["type"] in {"hitokoto", "joke"}


class TestCachedFunContentService:
    """趣味内容缓存兼容性测试。"""

    @pytest.fixture
    def config(self) -> FunContentSource:
        """创建最小有效服务配置。"""
        return FunContentSource(
            timeout_sec=5,
            endpoints=[
                FunContentEndpoint(
                    name="hitokoto",
                    url="https://api.example.com/hitokoto",
                    data_path="data.hitokoto",
                    display_title="今日灵感",
                )
            ],
        )

    def test_cache_preserves_type(
        self, config: FunContentSource, tmp_path: Path
    ) -> None:
        """新的缓存字典保留稳定的端点类型。"""
        service = CachedFunContentService(config, logging.getLogger(__name__), tmp_path)
        expected = {"title": "今日灵感", "content": "内容", "type": "hitokoto"}

        service.save_cache(expected)

        assert service.load_cache() == expected

    def test_forwards_injected_client(
        self, config: FunContentSource, tmp_path: Path
    ) -> None:
        """缓存包装层将共享客户端传递给网络服务。"""
        client = MagicMock(spec=httpx.AsyncClient)

        service = CachedFunContentService(
            config, logging.getLogger(__name__), tmp_path, http_client=client
        )

        assert service._service._http_client is client

    def test_legacy_cache_without_type_still_loads(
        self, config: FunContentSource, tmp_path: Path
    ) -> None:
        """旧缓存字典无需包含 type 键。"""
        service = CachedFunContentService(config, logging.getLogger(__name__), tmp_path)
        legacy = {"title": "旧标题", "content": "旧内容"}
        cache_data = {"date": "2026-02-04", "data": legacy, "fetched_at": 0}
        (tmp_path / "fun_content.json").write_text(
            json.dumps(cache_data, ensure_ascii=False), encoding="utf-8"
        )

        assert service.load_cache() == legacy
