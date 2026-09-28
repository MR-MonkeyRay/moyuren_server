"""60s 公共实例列表的发现与解析。

官方文档（https://docs.60s-api.viki.moe/7306811m0）维护了社区公共实例清单，
主域名不可用（如 Cloudflare 对数据中心出口 IP 返回 403）时可按序切换。
本模块解析该文档的 Markdown 表格并带 TTL 缓存结果。
"""

import logging
import re
import time

import httpx

from app.core.network import create_async_client, safe_exception_for_log

# 官方文档的 Markdown 版本（追加 .md 后缀返回纯 Markdown，便于解析表格）
DEFAULT_INSTANCE_LIST_URL = "https://docs.60s-api.viki.moe/7306811m0.md"
# 实例列表刷新间隔（秒）
DEFAULT_TTL_SEC = 6 * 3600

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_STRUCK_RE = re.compile(r"^~~.+~~$")
# 形如 60s.example.com、api.example.com/60s、host:port 的实例地址
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:/[^\s|]*)?$")


def parse_instance_list(markdown: str) -> list[str]:
    """从官方文档 Markdown 中解析公共实例地址。

    仅取每张表格的首列；表头、分隔行、以及被 ``~~`` 划掉的失效实例会被跳过。

    Args:
        markdown: 官方文档的 Markdown 文本。

    Returns:
        按文档顺序去重的实例基地址列表，例如 ``["https://60s.crystelf.top"]``。
    """
    instances: list[str] = []
    for line in markdown.splitlines():
        row = _TABLE_ROW_RE.match(line.strip())
        if row is None:
            continue
        cells = row.group(1).split("|")
        if not cells:
            continue
        candidate = cells[0].strip()
        if not candidate or candidate.startswith("-") or _STRUCK_RE.match(candidate):
            continue
        if _HOST_RE.match(candidate) is None:
            continue
        url = f"https://{candidate.rstrip('/')}"
        if url not in instances:
            instances.append(url)
    return instances


class PublicInstanceProvider:
    """带 TTL 缓存的 60s 公共实例发现器。

    发现失败时沿用上一次成功结果（即使已过期），避免文档页偶发不可用导致实例列表丢失。
    """

    def __init__(
        self,
        logger: logging.Logger,
        list_url: str = DEFAULT_INSTANCE_LIST_URL,
        ttl_sec: int = DEFAULT_TTL_SEC,
        timeout_sec: float = 10.0,
        proxy_url: str | None = None,
    ) -> None:
        """初始化公共实例发现器。

        Args:
            logger: 日志记录器。
            list_url: 实例列表文档地址。
            ttl_sec: 缓存有效期（秒），超期后重新拉取。
            timeout_sec: 拉取文档的超时时间（秒）。
            proxy_url: 可选全局代理 URL。
        """
        self.logger = logger
        self._list_url = list_url
        self._ttl_sec = ttl_sec
        self._timeout_sec = timeout_sec
        self._proxy_url = proxy_url
        self._instances: list[str] | None = None
        self._fetched_at = 0.0

    async def get_instances(self) -> list[str]:
        """获取公共实例列表（必要时刷新缓存）。

        Returns:
            实例基地址列表；从未成功获取过时返回空列表。
        """
        now = time.monotonic()
        if self._instances is not None and now - self._fetched_at < self._ttl_sec:
            return list(self._instances)

        fetched = await self._fetch_instances()
        if fetched is None:
            return list(self._instances) if self._instances is not None else []

        self._instances = fetched
        self._fetched_at = now
        return list(fetched)

    async def _fetch_instances(self) -> list[str] | None:
        """拉取并解析实例列表文档。

        Returns:
            解析出的实例地址列表；请求失败或未解析到实例时返回 None。
        """
        try:
            async with create_async_client(proxy_url=self._proxy_url) as client:
                response = await client.get(
                    self._list_url, timeout=httpx.Timeout(self._timeout_sec)
                )
                response.raise_for_status()
                instances = parse_instance_list(response.text)
        except httpx.HTTPError as e:
            self.logger.warning(
                "Failed to fetch public instance list: %s",
                safe_exception_for_log(e, self._list_url, self._proxy_url),
            )
            return None

        if not instances:
            self.logger.warning(
                "No public instances parsed from %s", self._list_url
            )
            return None

        self.logger.info("Discovered %d public 60s instances", len(instances))
        return instances
