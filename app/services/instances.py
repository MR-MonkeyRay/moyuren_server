"""60s 公共实例的发现与智能切换。

官方文档（https://docs.60s-api.viki.moe/7306811m0）维护了社区公共实例清单。
60s 主域名（``60s.viki.moe``）对数据中心/海外出口 IP 会返回 Cloudflare 403，
公共实例提供完全等价的 ``/v2/*`` 端点，可按健康度智能切换。

本模块提供三个层次的能力：

- ``parse_instance_list``：解析文档 Markdown 表格中的实例地址；
- ``PublicInstanceProvider``：带 TTL 缓存的实例发现；
- ``InstanceRouter``：在配置的主源与公共实例之间按健康度路由请求。
"""

import logging
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx

from app.core.config import InstancesConfig
from app.core.network import create_async_client, redact_url, safe_exception_for_log

# 实例列表刷新间隔（秒）
DEFAULT_TTL_SEC = 6 * 3600
# 单个端点连续失败达到该次数后进入冷却
_ENDPOINT_FAILURE_THRESHOLD = 2
# 端点冷却时长（秒）
_ENDPOINT_COOLDOWN_SEC = 600.0
# 单次请求最多尝试的端点数量（主源 + 公共实例），用于限制最坏耗时
_MAX_ENDPOINT_ATTEMPTS = 4

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_STRUCK_RE = re.compile(r"^~~.+~~$")
# 形如 60s.example.com、api.example.com/60s、host:port 的实例地址
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:/[^\s|]*)?$")

T = TypeVar("T")


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
        list_url: str,
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
            self.logger.warning("No public instances parsed from %s", self._list_url)
            return None

        self.logger.info("Discovered %d public 60s instances", len(instances))
        return instances


class InstanceRouter:
    """在等价端点（主源 + 公共实例）之间按健康度智能切换。

    所有候选端点提供完全相同的接口路径与响应结构，因此可视为等价：

    - **粘性优先**：最近成功的端点下次排在最前，避免每次请求都探测已知不可用的主源；
    - **失败冷却**：连续失败 ``failure_threshold`` 次的端点降级到最后尝试，冷却
      ``cooldown_sec`` 秒后恢复；冷却中的端点不会被剔除，以免全部端点不可用时无端点可试；
    - **尝试上限**：单次调用最多尝试 ``max_attempts`` 个端点，限制最坏耗时。
    """

    def __init__(
        self,
        logger: logging.Logger,
        instances: InstancesConfig | None = None,
        proxy_url: str | None = None,
        timeout_sec: float = 10.0,
        max_attempts: int = _MAX_ENDPOINT_ATTEMPTS,
        failure_threshold: int = _ENDPOINT_FAILURE_THRESHOLD,
        cooldown_sec: float = _ENDPOINT_COOLDOWN_SEC,
    ) -> None:
        """初始化端点路由器。

        Args:
            logger: 日志记录器。
            instances: 全局 60s 公共实例配置；为空表示不使用公共实例。
            proxy_url: 可选全局代理 URL。
            timeout_sec: 拉取实例列表文档的超时时间（秒）。
            max_attempts: 单次请求最多尝试的端点数量。
            failure_threshold: 端点连续失败多少次后进入冷却。
            cooldown_sec: 端点冷却时长（秒）。
        """
        self._logger = logger
        self._instance_urls = list((instances.urls if instances else None) or [])
        self._max_attempts = max_attempts
        self._failure_threshold = failure_threshold
        self._cooldown_sec = cooldown_sec
        list_url = instances.list_url if instances else None
        self._provider = (
            PublicInstanceProvider(
                logger=logger,
                list_url=list_url,
                timeout_sec=timeout_sec,
                proxy_url=proxy_url,
            )
            if list_url
            else None
        )
        self._failures: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        self._preferred_endpoint: str | None = None

    @classmethod
    def from_config(
        cls,
        instances: InstancesConfig | None,
        logger: logging.Logger,
        proxy_url: str | None = None,
        timeout_sec: float = 10.0,
    ) -> "InstanceRouter":
        """按全局实例配置创建路由器。

        Args:
            instances: 全局 60s 公共实例配置。
            logger: 日志记录器。
            proxy_url: 可选全局代理 URL。
            timeout_sec: 拉取实例列表文档的超时时间（秒）。

        Returns:
            使用全局实例配置初始化的路由器。
        """
        return cls(
            logger,
            instances,
            proxy_url=proxy_url,
            timeout_sec=timeout_sec,
        )

    async def instance_bases(self) -> list[str]:
        """返回去重后的公共实例基地址（显式配置在前，自动发现结果在后）。

        Returns:
            规范化（去掉末尾斜杠）后的实例基地址列表。
        """
        bases = list(self._instance_urls)
        if self._provider is not None:
            bases.extend(await self._provider.get_instances())

        unique: list[str] = []
        for base in bases:
            normalized = base.rstrip("/")
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique

    async def candidates(self, primary_url: str) -> list[str]:
        """构建候选端点列表：主源在前，随后是同一路径的公共实例端点。

        Args:
            primary_url: 主源端点地址。

        Returns:
            去重后的候选端点地址列表。
        """
        primary = urlparse(primary_url)
        path = primary.path or "/"
        suffix = f"{path}?{primary.query}" if primary.query else path

        endpoints = [primary_url]
        for base in await self.instance_bases():
            url = f"{base}{suffix}"
            if url not in endpoints:
                endpoints.append(url)
        return endpoints

    def order(self, endpoints: list[str]) -> list[str]:
        """按健康状况排序候选端点。

        优先使用最近成功的端点（粘性），冷却期内的端点降级到最后尝试，并限制尝试数量。

        Args:
            endpoints: 候选端点地址列表。

        Returns:
            本次请求实际尝试的端点顺序。
        """
        now = time.monotonic()
        available = [
            url for url in endpoints if self._cooldown_until.get(url, 0.0) <= now
        ]
        cooling = [url for url in endpoints if self._cooldown_until.get(url, 0.0) > now]
        if self._preferred_endpoint in available:
            available.remove(self._preferred_endpoint)
            available.insert(0, self._preferred_endpoint)
        return (available + cooling)[: self._max_attempts]

    def mark_success(self, url: str) -> None:
        """记录端点请求成功，并将其设为优先端点。

        Args:
            url: 请求成功的端点地址。
        """
        self._failures.pop(url, None)
        self._cooldown_until.pop(url, None)
        self._preferred_endpoint = url

    def mark_failure(self, url: str) -> None:
        """记录端点请求失败，连续失败超阈值时进入冷却。

        Args:
            url: 请求失败的端点地址。
        """
        failures = self._failures.get(url, 0) + 1
        self._failures[url] = failures
        if failures >= self._failure_threshold:
            self._cooldown_until[url] = time.monotonic() + self._cooldown_sec
            self._logger.warning(
                "Endpoint %s failed %d times, cooling down for %.0fs",
                redact_url(url),
                failures,
                self._cooldown_sec,
            )
        if self._preferred_endpoint == url:
            self._preferred_endpoint = None

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> Any | None:
        """请求单个端点并解析 JSON。

        Args:
            client: 用于请求的 HTTP 客户端。
            url: 端点地址。
            params: 可选查询参数。
            timeout: 可选请求超时；未提供时使用客户端默认值。

        Returns:
            解析后的 JSON 数据；请求失败或响应非法 JSON 时返回 None。
        """
        kwargs: dict[str, Any] = {}
        if params:
            kwargs["params"] = params
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            self._logger.warning("Timeout fetching from %s", redact_url(url))
        except httpx.HTTPStatusError as e:
            self._logger.warning(
                "HTTP %s from %s", e.response.status_code, redact_url(url)
            )
        except httpx.RequestError as e:
            self._logger.warning(
                "Request error fetching from %s: %s",
                redact_url(url),
                safe_exception_for_log(e, url),
            )
        except ValueError as e:
            self._logger.warning(
                "Invalid JSON from %s: %s", redact_url(url), safe_exception_for_log(e, url)
            )
        return None

    async def fetch_json(
        self,
        client: httpx.AsyncClient,
        primary_url: str,
        parse: Callable[[Any], T | None],
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> tuple[str, T] | None:
        """按健康度顺序请求候选端点，返回首个解析成功的结果。

        Args:
            client: 用于请求的 HTTP 客户端。
            primary_url: 主源端点地址。
            parse: 响应解析器；返回 None 表示该端点响应不可用，继续尝试下一个端点。
            params: 可选查询参数。
            timeout: 可选请求超时。

        Returns:
            ``(端点地址, 解析结果)``；所有候选端点均失败时返回 None。
        """
        endpoints = await self.candidates(primary_url)
        ordered = self.order(endpoints)
        self._logger.debug(
            "Trying %d/%d endpoints for %s",
            len(ordered),
            len(endpoints),
            redact_url(primary_url),
        )

        for url in ordered:
            data = await self._request_json(
                client, url, params=params, timeout=timeout
            )
            if data is None:
                self.mark_failure(url)
                continue

            parsed = parse(data)
            if parsed is None:
                self._logger.warning("Unusable response from %s", redact_url(url))
                self.mark_failure(url)
                continue

            self._logger.info("Fetched from %s", redact_url(url))
            self.mark_success(url)
            return url, parsed

        return None
