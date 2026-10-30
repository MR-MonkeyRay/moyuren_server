"""趣味内容获取服务模块。"""

import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.core.config import FunContentEndpoint, FunContentSource, InstancesConfig
from app.core.network import create_async_client, safe_exception_for_log
from app.services.calendar import today_business
from app.services.daily_cache import DailyCache
from app.services.instances import InstanceRouter

logger = logging.getLogger(__name__)


class FunContentService:
    """从多个 API 获取趣味内容的服务。"""

    _DEFAULT_CONTENT = {
        "title": "🐟 摸鱼小贴士",
        "content": "工作再忙，也要记得摸鱼。适当休息，效率更高！",
        "type": "default",
    }

    def __init__(
        self,
        config: FunContentSource,
        proxy_url: str | None = None,
        instances: InstancesConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """用配置初始化服务。

        Args:
            config: 趣味内容配置。
            proxy_url: 可选的出站代理 URL。
            instances: 全局 60s 公共实例配置。
            http_client: 可选的外部 HTTP 客户端，由调用方管理。
        """
        self.config = config
        self._http_client = http_client
        self._proxy_url = proxy_url
        self._router = InstanceRouter.from_config(
            instances,
            logger,
            http_client=http_client,
            proxy_url=proxy_url,
            timeout_sec=float(config.timeout_sec),
        )

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """复用注入的客户端，否则临时创建并管理客户端。"""
        if self._http_client is not None:
            yield self._http_client
        else:
            async with create_async_client(
                timeout=self.config.timeout_sec, proxy_url=self._proxy_url
            ) as client:
                yield client

    async def fetch_content(self, target_date: date) -> dict[str, str]:
        """按日期随机选择并获取趣味内容。

        以日期为随机种子，保证同一天选择一致；主端点失败时回退到其他端点。

        Args:
            target_date: 用作随机种子的日期。

        Returns:
            含 'title'、'content'、'type' 键的字典。
        """
        endpoints = self._shuffle_by_date(target_date)

        async with self._get_client() as client:
            for endpoint in endpoints:
                result = await self._fetch_endpoint(client, endpoint)
                if result:
                    logger.info("Successfully fetched content from %s", endpoint.name)
                    return result

        logger.warning("All fun content endpoints failed, using default")
        return self._DEFAULT_CONTENT.copy()

    def _shuffle_by_date(self, target_date: date) -> list[FunContentEndpoint]:
        """以日期为种子打乱端点顺序，保证每日选择稳定。

        Args:
            target_date: 用作随机种子的日期。

        Returns:
            打乱后的端点配置列表。
        """
        seed = int(target_date.strftime("%Y%m%d"))
        rng = random.Random(seed)  # nosec B311 - 不用于安全/加密场景
        endpoints = list(self.config.endpoints)
        rng.shuffle(endpoints)
        return endpoints

    async def _fetch_endpoint(
        self, client: httpx.AsyncClient, endpoint: FunContentEndpoint
    ) -> dict[str, str] | None:
        """从单个端点获取内容。

        主源不可用时按健康度切换到 60s 公共实例的同一路径端点。

        Args:
            client: 用于请求的 HTTP 客户端。
            endpoint: 端点配置。

        Returns:
            成功返回含 'title'、'content'、'type' 的字典，否则 None。
        """
        result = await self._router.fetch_json(
            client,
            endpoint.url,
            parse=lambda data: self._extract_content(data, endpoint),
            timeout=httpx.Timeout(self.config.timeout_sec),
        )
        if result is None:
            return None
        return {
            "title": endpoint.display_title,
            "content": result[1],
            "type": endpoint.name,
        }

    def _extract_content(self, data: Any, endpoint: FunContentEndpoint) -> str | None:
        """从端点响应中提取可用文案.

        Args:
            data: 端点返回的 JSON 数据.
            endpoint: 端点配置, 提供 data_path.

        Returns:
            去空白后的文案字符串; 缺失或为空时返回 None.
        """
        if not isinstance(data, dict):
            logger.debug("No valid content from %s", endpoint.name)
            return None

        content = self._extract_by_path(data, endpoint.data_path)
        if isinstance(content, str) and content.strip():
            return content.strip()
        logger.debug("No valid content from %s", endpoint.name)
        return None

    def _extract_by_path(self, data: Any, path: str) -> Any:
        """按点分隔路径从嵌套字典中提取值。

        Args:
            data: 待提取的数据；路径中间值可能为任意类型。
            path: 点分隔路径，例如 ``data.content``。

        Returns:
            提取到的值；路径无效或中间结构不可用时返回 None。
        """
        for key in path.split("."):
            if isinstance(data, dict):
                data = data.get(key)
            else:
                return None
        return data


class CachedFunContentService(DailyCache[dict[str, str]]):
    """带日级缓存的趣味内容服务。

    继承 DailyCache，为 FunContentService 提供日级缓存能力。
    缓存在每日零点自动过期，网络获取失败时返回过期缓存。
    """

    def __init__(
        self,
        config: FunContentSource,
        logger: logging.Logger,
        cache_dir: Path,
        proxy_url: str | None = None,
        instances: InstancesConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化带缓存的趣味内容服务。

        Args:
            config: 趣味内容配置
            logger: 日志记录器
            cache_dir: 缓存目录路径
            proxy_url: 可选全局代理 URL
            instances: 全局 60s 公共实例配置；主源不可用时按健康度切换
            http_client: 可选外部 HTTP 客户端；由调用方管理生命周期
        """
        super().__init__("fun_content", cache_dir, logger)
        self._proxy_url = proxy_url
        self._service = FunContentService(
            config,
            http_client=http_client,
            proxy_url=proxy_url,
            instances=instances,
        )

    async def fetch_fresh(self) -> dict[str, str] | None:
        """从网络获取新鲜数据。

        Returns:
            趣味内容字典（包含 title、content 和 type），如果获取失败返回 None
        """
        try:
            return await self._service.fetch_content(today_business())
        except Exception as e:
            self.logger.error(
                "Failed to fetch fun content: %s",
                safe_exception_for_log(e, self._proxy_url),
            )
            return None
