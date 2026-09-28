"""Tests for app/services/instances.py - public instance discovery and routing."""

import logging

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
    """Tests for parse_instance_list."""

    def test_parses_live_instances_in_order_and_skips_struck_entries(self) -> None:
        """Header, separator, struck-through and duplicate entries are excluded."""
        instances = parse_instance_list(SAMPLE_MARKDOWN)

        assert instances == [
            "https://60s.crystelf.top",
            "https://api.elysiayanyu.top",
            "https://api.cczo.cc/60s",
        ]

    def test_returns_empty_for_non_table_content(self) -> None:
        """Markdown without instance tables yields no instances."""
        assert parse_instance_list("# hello\n\nno tables here\n") == []
        assert parse_instance_list("") == []


class TestPublicInstanceProvider:
    """Tests for PublicInstanceProvider."""

    LIST_URL = "https://docs.example.com/list.md"

    @pytest.fixture
    def provider(self, logger: logging.Logger) -> PublicInstanceProvider:
        return PublicInstanceProvider(
            logger=logger, list_url=self.LIST_URL, timeout_sec=5
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_discovers_and_caches_instances(
        self, provider: PublicInstanceProvider
    ) -> None:
        """Instances are parsed once and served from cache within the TTL."""
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
        """A failing discovery with no prior success yields no instances."""
        respx.get(self.LIST_URL).mock(return_value=Response(500))

        assert await provider.get_instances() == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_keeps_last_result_when_refresh_fails(
        self, logger: logging.Logger
    ) -> None:
        """A failed refresh falls back to the previously discovered list."""
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
        """A document that parses to nothing is treated as a failed discovery."""
        respx.get(self.LIST_URL).mock(return_value=Response(200, text="# empty\n"))

        assert await provider.get_instances() == []


class TestInstanceRouterCandidates:
    """Tests for InstanceRouter candidate construction."""

    LIST_URL = "https://docs.example.com/instances.md"

    @staticmethod
    def _markdown(*domains: str) -> str:
        """Build a docs-page-like markdown table for the given domains."""
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
        """Instance endpoints mirror the primary path and query string."""
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
        """A primary URL without a path maps instances to their root."""
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
        """Duplicate instances and instances equal to the primary are dropped."""
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
        """Discovered instances are appended after the explicitly configured ones."""
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
    async def test_from_config_uses_global_instance_config(
        self, logger: logging.Logger
    ) -> None:
        """from_config() uses the global instance configuration."""
        source = GoldPriceSource(url="https://api.example.com/v2/gold-price", timeout_sec=5)
        router = InstanceRouter.from_config(
            InstancesConfig(urls=["https://inst.example.com"]), logger, timeout_sec=5.0
        )

        assert await router.candidates(source.url) == [
            "https://api.example.com/v2/gold-price",
            "https://inst.example.com/v2/gold-price",
        ]


class TestInstanceRouterOrdering:
    """Tests for InstanceRouter health-based ordering."""

    @pytest.fixture
    def router(self, logger: logging.Logger) -> InstanceRouter:
        return InstanceRouter(logger)

    def test_prefers_sticky_then_deprioritizes_cooldown(
        self, router: InstanceRouter
    ) -> None:
        """Sticky endpoint comes first; cooled-down endpoints go last."""
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
        """Cooled-down endpoints are still attempted when nothing else is available."""
        endpoints = ["https://a/v2/60s", "https://b/v2/60s"]
        for url in endpoints:
            for _ in range(2):
                router.mark_failure(url)

        assert router.order(endpoints) == endpoints

    def test_limits_attempts(self, router: InstanceRouter) -> None:
        """At most four endpoints are attempted per call."""
        endpoints = [f"https://inst{i}.example.com/v2/60s" for i in range(8)]

        assert router.order(endpoints) == endpoints[:4]

    def test_success_clears_failure_state(self, router: InstanceRouter) -> None:
        """A later success resets the failure counter and cooldown."""
        url = "https://a/v2/60s"
        for _ in range(2):
            router.mark_failure(url)
        assert router.order([url]) == [url]

        router.mark_success(url)

        assert router.order([url]) == [url]


class TestInstanceRouterFetchJson:
    """Tests for InstanceRouter.fetch_json."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_switches_to_instance_when_primary_fails(
        self, logger: logging.Logger
    ) -> None:
        """The first usable response wins and later candidates are skipped."""
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
        """A None parser result marks the endpoint failed and the next one is tried."""
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
        """A successful instance is preferred on the next call."""
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
