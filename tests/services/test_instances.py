"""测试 app/services/instances.py 的公共实例发现与路由。"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import GoldPriceSource, InstancesConfig
from app.services.instances import (
    InstanceRouter,
    PublicInstanceProvider,
    parse_instance_list,
)

# 官方文档页结构的最小复刻（含表头、分隔行、失效实例与路径前缀实例）
SAMPLE_MARKDOWN = """\
# 【公开】🚀 公共实例列表

因近期墙加重，主域名在部分地区连通性较差。

**推荐实例**

| 实例域名               | 大陆访问 | HTTPS | 提交人     | 添加时间   |
| ---------------------- | -------- | ----- | ---------- | ---------- |
| ~~60api.09cdn.xyz~~    | ✅        | ✅     | 零九亚太   | 2025-08-28 |
| 60s.crystelf.top       | ✅        | ✅     | Jerry      | 2025-08-29 |
| api.elysiayanyu.top    | ✅        | ✅     | 烟雨       | 2025-08-30 |
| api.cczo.cc/60s        | ✅        | ✅     | 洛菜菜     | 2026-05-09 |
| 60s.crystelf.top       | ✅        | ✅     | Jerry      | 2025-08-29 |

**其他实例** （不保证 HTTPS 可用）

| 实例域名                               | 大陆访问 | HTTPS | 提交人     | 添加时间   |
| -------------------------------------- | -------- | ----- | ---------- | ---------- |
| ~~60s.0.3.e.0.4.8.0.6.0.4.2.ip6.arpa~~ | ✅        | -     | 風間蘇蘇   | 2025-08-28 |
| ~~zzw1.wch1.top:22600~~                | ✅        | -     | 过期的萌新 | 2025-09-03 |

> 如果你也想贡献，欢迎加入反馈 QQ 群 595941841 提交～
"""


class TestParseInstanceList:
    """测试 parse_instance_list。"""

    def test_parses_live_instances_in_order_and_skips_struck_entries(self) -> None:
        """排除表头、分隔行、删除线条目和重复条目。"""
        instances = parse_instance_list(SAMPLE_MARKDOWN)

        assert instances == [
            "https://60s.crystelf.top",
            "https://api.elysiayanyu.top",
            "https://api.cczo.cc/60s",
        ]

    def test_returns_empty_for_non_table_content(self) -> None:
        """没有实例表格的 Markdown 不返回实例。"""
        assert parse_instance_list("# hello\n\nno tables here\n") == []
        assert parse_instance_list("") == []


class TestPublicInstanceProvider:
    """测试 PublicInstanceProvider。"""

    LIST_URL = "https://docs.example.com/list.md"

    @pytest.fixture
    def provider(self, logger: logging.Logger) -> PublicInstanceProvider:
        return PublicInstanceProvider(
            logger=logger, list_url=self.LIST_URL, timeout_sec=5
        )

    @pytest.mark.asyncio
    async def test_injected_client_preserves_timeout_and_stays_open(
        self, logger: logging.Logger
    ) -> None:
        """实例发现借用应用客户端，但不使用其默认超时。"""
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=Response(
            200, text=SAMPLE_MARKDOWN, request=httpx.Request("GET", self.LIST_URL)
        ))
        provider = PublicInstanceProvider(
            logger, self.LIST_URL, timeout_sec=7, http_client=client
        )

        with patch("app.services.instances.create_async_client") as create_client:
            assert await provider.get_instances()

        client.get.assert_awaited_once_with(self.LIST_URL, timeout=httpx.Timeout(7))
        create_client.assert_not_called()
        client.aclose.assert_not_called()
        client.__aexit__.assert_not_called()

    @respx.mock
    @pytest.mark.asyncio
    async def test_discovers_and_caches_instances(
        self, provider: PublicInstanceProvider
    ) -> None:
        """实例只解析一次，TTL 内从缓存返回。"""
        respx.get(self.LIST_URL).mock(return_value=Response(200, text=SAMPLE_MARKDOWN))

        first = await provider.get_instances()
        second = await provider.get_instances()

        assert first == [
            "https://60s.crystelf.top",
            "https://api.elysiayanyu.top",
            "https://api.cczo.cc/60s",
        ]
        assert second == first
        assert respx.calls.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_empty_when_never_discovered(
        self, provider: PublicInstanceProvider
    ) -> None:
        """此前未成功发现实例时，发现失败返回空列表。"""
        respx.get(self.LIST_URL).mock(return_value=Response(500))

        assert await provider.get_instances() == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_keeps_last_result_when_refresh_fails(
        self, logger: logging.Logger
    ) -> None:
        """刷新失败时回退到此前发现的列表。"""
        provider = PublicInstanceProvider(
            logger=logger, list_url=self.LIST_URL, ttl_sec=0, timeout_sec=5
        )
        respx.get(self.LIST_URL).mock(
            side_effect=[
                Response(200, text=SAMPLE_MARKDOWN),
                httpx.ConnectError("boom"),
            ]
        )

        first = await provider.get_instances()
        second = await provider.get_instances()

        assert first == second
        assert "https://60s.crystelf.top" in second

    @respx.mock
    @pytest.mark.asyncio
    async def test_ignores_document_without_instances(
        self, provider: PublicInstanceProvider
    ) -> None:
        """文档解析不到实例时视为发现失败。"""
        respx.get(self.LIST_URL).mock(return_value=Response(200, text="# empty\n"))

        assert await provider.get_instances() == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_allowlist_filters_exact_and_wildcard_hosts(
        self, logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """精确条目只匹配单个主机，通配符只匹配子域名。"""
        markdown = TestInstanceRouterCandidates._markdown(
            "exact.example.net",
            "api.example.com",
            "deep.api.example.com",
            "example.com",
            "badexample.com",
            "evil.example.org",
        )
        provider = PublicInstanceProvider(
            logger,
            self.LIST_URL,
            allowlist=["exact.example.net", "*.example.com"],
        )
        respx.get(self.LIST_URL).mock(return_value=Response(200, text=markdown))

        with caplog.at_level(logging.WARNING, logger="test"):
            instances = await provider.get_instances()

        assert instances == [
            "https://exact.example.net",
            "https://api.example.com",
            "https://deep.api.example.com",
        ]
        dropped_logs = [
            record.message
            for record in caplog.records
            if "Dropped public 60s instance hosts" in record.message
        ]
        assert dropped_logs == [
            "Dropped public 60s instance hosts not in allowlist: "
            "badexample.com, evil.example.org, example.com"
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_none_allowlist_preserves_all_discovered_instances(
        self, logger: logging.Logger
    ) -> None:
        """None 保留向后兼容的无过滤发现行为。"""
        provider = PublicInstanceProvider(logger, self.LIST_URL, allowlist=None)
        respx.get(self.LIST_URL).mock(return_value=Response(200, text=SAMPLE_MARKDOWN))

        assert await provider.get_instances() == [
            "https://60s.crystelf.top",
            "https://api.elysiayanyu.top",
            "https://api.cczo.cc/60s",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_allowlist_caches_successful_empty_result(
        self, logger: logging.Logger
    ) -> None:
        """空白名单拒绝所有发现的主机，并缓存该结果。"""
        provider = PublicInstanceProvider(logger, self.LIST_URL, allowlist=[])
        respx.get(self.LIST_URL).mock(return_value=Response(200, text=SAMPLE_MARKDOWN))

        assert await provider.get_instances() == []
        assert await provider.get_instances() == []
        assert respx.calls.call_count == 1


class TestInstanceRouterCandidates:
    """测试 InstanceRouter 候选列表的构建。"""

    LIST_URL = "https://docs.example.com/instances.md"

    @pytest.mark.asyncio
    async def test_from_config_threads_injected_client_to_discovery(
        self, logger: logging.Logger
    ) -> None:
        """路由器将应用客户端传给文档提供器。"""
        client = MagicMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=Response(
            200, text=SAMPLE_MARKDOWN, request=httpx.Request("GET", self.LIST_URL)
        ))
        router = InstanceRouter.from_config(
            InstancesConfig(list_url=self.LIST_URL),
            logger,
            timeout_sec=6,
            http_client=client,
        )

        with patch("app.services.instances.create_async_client") as create_client:
            assert await router.candidates("https://api.example.com/v2/60s")

        client.get.assert_awaited_once_with(self.LIST_URL, timeout=httpx.Timeout(6))
        create_client.assert_not_called()
        client.aclose.assert_not_called()
        client.__aexit__.assert_not_called()

    @staticmethod
    def _markdown(*domains: str) -> str:
        """为给定域名构建与文档页类似的 Markdown 表格。"""
        rows = "\n".join(
            f"| {domain} | ✅ | ✅ | tester | 2026-01-01 |" for domain in domains
        )
        return (
            "| 实例域名 | 大陆访问 | HTTPS | 提交人 | 添加时间 |\n"
            "| --- | --- | --- | --- | --- |\n"
            f"{rows}\n"
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_reuses_primary_path_and_query(
        self, logger: logging.Logger
    ) -> None:
        """实例端点沿用主源的路径和查询字符串。"""
        router = InstanceRouter(
            logger,
            InstancesConfig(
                urls=["https://inst1.example.com/", "https://inst2.example.com/prefix"]
            ),
        )

        assert await router.candidates("https://api.example.com/v2/60s?force-update=1") == [
            "https://api.example.com/v2/60s?force-update=1",
            "https://inst1.example.com/v2/60s?force-update=1",
            "https://inst2.example.com/prefix/v2/60s?force-update=1",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_uses_root_when_primary_has_no_path(
        self, logger: logging.Logger
    ) -> None:
        """主源 URL 没有路径时，实例映射到根路径。"""
        router = InstanceRouter(
            logger, InstancesConfig(urls=["https://inst.example.com"])
        )

        assert await router.candidates("https://api.example.com") == [
            "https://api.example.com",
            "https://inst.example.com/",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_deduplicates_and_ignores_primary_duplicate(
        self, logger: logging.Logger
    ) -> None:
        """剔除重复实例及与主源相同的实例。"""
        router = InstanceRouter(
            logger,
            InstancesConfig(
                urls=[
                    "https://api.example.com",
                    "https://inst.example.com",
                    "https://inst.example.com/",
                    "",
                ]
            ),
        )

        assert await router.candidates("https://api.example.com/v2/60s") == [
            "https://api.example.com/v2/60s",
            "https://inst.example.com/v2/60s",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_discovers_instances_from_document(
        self, logger: logging.Logger
    ) -> None:
        """发现的实例追加在显式配置的实例之后。"""
        router = InstanceRouter(
            logger,
            InstancesConfig(
                urls=["https://explicit.example.com"], list_url=self.LIST_URL
            ),
        )
        respx.get(self.LIST_URL).mock(
            return_value=Response(200, text=self._markdown("60s.inst.example.com"))
        )

        assert await router.candidates("https://api.example.com/v2/60s") == [
            "https://api.example.com/v2/60s",
            "https://explicit.example.com/v2/60s",
            "https://60s.inst.example.com/v2/60s",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_explicit_urls_bypass_discovery_allowlist(
        self, logger: logging.Logger
    ) -> None:
        """只有从文档发现的实例受白名单限制。"""
        router = InstanceRouter(
            logger,
            InstancesConfig(
                urls=["https://explicit.example.org"],
                list_url=self.LIST_URL,
                allowlist=["*.example.com"],
            ),
        )
        respx.get(self.LIST_URL).mock(
            return_value=Response(
                200,
                text=self._markdown("allowed.example.com", "blocked.example.org"),
            )
        )

        assert await router.candidates("https://api.example.com/v2/60s") == [
            "https://api.example.com/v2/60s",
            "https://explicit.example.org/v2/60s",
            "https://allowed.example.com/v2/60s",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_from_config_uses_global_instance_config(
        self, logger: logging.Logger
    ) -> None:
        """from_config() 使用全局实例配置。"""
        source = GoldPriceSource(url="https://api.example.com/v2/gold-price", timeout_sec=5)
        router = InstanceRouter.from_config(
            InstancesConfig(urls=["https://inst.example.com"]), logger, timeout_sec=5.0
        )

        assert await router.candidates(source.url) == [
            "https://api.example.com/v2/gold-price",
            "https://inst.example.com/v2/gold-price",
        ]


class TestInstanceRouterOrdering:
    """测试 InstanceRouter 按健康状态排序。"""

    @pytest.fixture
    def router(self, logger: logging.Logger) -> InstanceRouter:
        return InstanceRouter(logger)

    def test_prefers_sticky_then_deprioritizes_cooldown(
        self, router: InstanceRouter
    ) -> None:
        """上次成功的端点优先，冷却中的端点排在最后。"""
        endpoints = ["https://a/v2/60s", "https://b/v2/60s", "https://c/v2/60s"]
        router.mark_success("https://c/v2/60s")
        for _ in range(2):
            router.mark_failure("https://a/v2/60s")

        assert router.order(endpoints) == [
            "https://c/v2/60s",
            "https://b/v2/60s",
            "https://a/v2/60s",
        ]

    def test_keeps_cooled_endpoints_as_last_resort(self, router: InstanceRouter) -> None:
        """没有其他可用端点时，仍尝试冷却中的端点。"""
        endpoints = ["https://a/v2/60s", "https://b/v2/60s"]
        for url in endpoints:
            for _ in range(2):
                router.mark_failure(url)

        assert router.order(endpoints) == endpoints

    def test_limits_attempts(self, router: InstanceRouter) -> None:
        """每次调用最多尝试四个端点。"""
        endpoints = [f"https://inst{i}.example.com/v2/60s" for i in range(8)]

        assert router.order(endpoints) == endpoints[:4]

    def test_success_clears_failure_state(self, router: InstanceRouter) -> None:
        """后续成功会重置失败计数和冷却状态。"""
        url = "https://a/v2/60s"
        for _ in range(2):
            router.mark_failure(url)
        assert router.order([url]) == [url]

        router.mark_success(url)

        assert router.order([url]) == [url]


class TestInstanceRouterFetchJson:
    """测试 InstanceRouter.fetch_json。"""

    @respx.mock
    @pytest.mark.asyncio
    async def test_parser_exception_tries_next_endpoint(
        self, logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """解析异常计入端点失败并继续回退，日志中的 URL 保持脱敏。"""
        primary_url = "https://api.example.com/v2/60s?token=private-token"
        instance_url = "https://inst.example.com/v2/60s?token=private-token"
        router = InstanceRouter(
            logger, InstancesConfig(urls=["https://inst.example.com"])
        )
        respx.get(primary_url).mock(return_value=Response(200, json={"data": None}))
        instance_route = respx.get(instance_url).mock(
            return_value=Response(200, json={"data": {"kfc": "V我50"}})
        )

        with patch.object(router, "mark_failure", wraps=router.mark_failure) as failure:
            async with httpx.AsyncClient() as client:
                result = await router.fetch_json(
                    client,
                    primary_url,
                    parse=lambda data: data.get("data", {}).get("kfc"),
                )

        assert result == (instance_url, "V我50")
        assert instance_route.call_count == 1
        failure.assert_called_once_with(primary_url)
        router_logs = "\n".join(
            record.getMessage() for record in caplog.records if record.name == logger.name
        )
        assert "Failed to parse response" in router_logs
        assert "AttributeError" in router_logs
        assert "private-token" not in router_logs

    @respx.mock
    @pytest.mark.asyncio
    async def test_switches_to_instance_when_primary_fails(
        self, logger: logging.Logger
    ) -> None:
        """采用首个可用响应，跳过后续候选端点。"""
        router = InstanceRouter(
            logger,
            InstancesConfig(
                urls=["https://inst1.example.com", "https://inst2.example.com"]
            ),
        )
        respx.get("https://api.example.com/v2/60s").mock(return_value=Response(403))
        respx.get("https://inst1.example.com/v2/60s").mock(
            return_value=Response(200, json={"data": {"kfc": "V我50"}})
        )
        inst2_route = respx.get("https://inst2.example.com/v2/60s")

        async with httpx.AsyncClient() as client:
            result = await router.fetch_json(
                client,
                "https://api.example.com/v2/60s",
                parse=lambda data: data.get("data", {}).get("kfc"),
            )

        assert result == ("https://inst1.example.com/v2/60s", "V我50")
        assert inst2_route.call_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_none_when_every_endpoint_is_unusable(
        self, logger: logging.Logger
    ) -> None:
        """解析结果为 None 时，将端点标记为失败并尝试下一个。"""
        router = InstanceRouter(
            logger, InstancesConfig(urls=["https://inst.example.com"])
        )
        respx.get("https://api.example.com/v2/60s").mock(
            return_value=Response(200, json={"data": {}})
        )
        respx.get("https://inst.example.com/v2/60s").mock(
            return_value=Response(200, json={"data": {"kfc": "   "}})
        )

        async with httpx.AsyncClient() as client:
            result = await router.fetch_json(
                client,
                "https://api.example.com/v2/60s",
                parse=lambda data: (data.get("data", {}).get("kfc") or "").strip() or None,
            )

        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_skips_primary_after_sticky_success(
        self, logger: logging.Logger
    ) -> None:
        """成功的实例在下次调用时优先使用。"""
        router = InstanceRouter(
            logger, InstancesConfig(urls=["https://inst.example.com"])
        )
        primary_route = respx.get("https://api.example.com/v2/60s").mock(
            return_value=Response(403)
        )
        respx.get("https://inst.example.com/v2/60s").mock(
            return_value=Response(200, json={"data": {"kfc": "V我50"}})
        )

        async with httpx.AsyncClient() as client:
            await router.fetch_json(
                client, "https://api.example.com/v2/60s", parse=lambda data: data
            )
            await router.fetch_json(
                client, "https://api.example.com/v2/60s", parse=lambda data: data
            )

        assert primary_route.call_count == 1
