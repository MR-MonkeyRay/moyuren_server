"""Tests for app/services/news_instances.py - public instance discovery."""

import logging

import httpx
import pytest
import respx
from httpx import Response

from app.services.news_instances import (
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
        respx.get(self.LIST_URL).mock(
            return_value=Response(200, text=SAMPLE_MARKDOWN)
        )

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
        respx.get(self.LIST_URL).mock(
            return_value=Response(200, text="# empty\n")
        )

        assert await provider.get_instances() == []
