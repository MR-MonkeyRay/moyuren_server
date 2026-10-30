"""KFC 疯狂星期四服务模块。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from app.core.config import CrazyThursdaySource, InstancesConfig
from app.core.network import create_async_client, safe_exception_for_log
from app.services.calendar import today_business
from app.services.daily_cache import DailyCache
from app.services.instances import InstanceRouter

logger = logging.getLogger(__name__)


class KfcService:
    """获取 KFC 疯狂星期四文案的服务。"""

    def __init__(
        self,
        config: CrazyThursdaySource,
        proxy_url: str | None = None,
        instances: InstancesConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """用配置初始化服务。

        Args:
            config: 疯狂星期四配置。
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

    async def fetch_kfc_copy(self) -> str | None:
        """获取 KFC 疯狂星期四文案。

        主源不可用时按健康度切换到 60s 公共实例。

        Returns:
            成功返回文案字符串，失败返回 None。
        """
        if not self.config.enabled:
            return None

        try:
            async with self._get_client() as client:
                result = await self._router.fetch_json(
                    client,
                    self.config.url,
                    parse=self._parse_kfc_response,
                    timeout=httpx.Timeout(self.config.timeout_sec),
                )
        except httpx.RequestError as e:
            logger.warning(
                "Failed to fetch KFC content: %s",
                safe_exception_for_log(e, self.config.url, self._proxy_url),
            )
            return None

        if result is None:
            logger.warning("Empty content received from KFC endpoint")
            return None
        return result[1]

    def _parse_kfc_response(self, data: Any) -> str | None:
        """从 60s KFC 接口响应中提取文案.

        Args:
            data: 接口返回的 JSON 数据, 期望格式 ``{"code": 200, "data": {"kfc": "..."}}``.

        Returns:
            去空白并还原换行后的文案; 缺失或为空时返回 None.
        """
        content = None
        if isinstance(data, dict):
            data_field = data.get("data")
            if isinstance(data_field, dict):
                content = data_field.get("kfc")
            elif isinstance(data_field, str):
                content = data_field
            else:
                content = data.get("text")
        elif isinstance(data, str):
            content = data

        if not isinstance(content, str) or not content.strip():
            return None
        # 处理文案中的转义换行符
        return content.strip().replace("\\n", "\n")


class CachedKfcService(DailyCache[str]):
    """带日级缓存的 KFC 服务。

    继承 DailyCache，为 KfcService 提供日级缓存能力。
    仅在周四获取 KFC 文案，缓存在每日零点自动过期。
    """

    def __init__(
        self,
        config: CrazyThursdaySource,
        logger: logging.Logger,
        cache_dir: Path,
        proxy_url: str | None = None,
        instances: InstancesConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化带缓存的 KFC 服务。

        Args:
            config: 疯狂星期四配置
            logger: 日志记录器
            cache_dir: 缓存目录路径
            proxy_url: 可选全局代理 URL
            instances: 全局 60s 公共实例配置；主源不可用时按健康度切换
            http_client: 可选外部 HTTP 客户端；由调用方管理生命周期
        """
        super().__init__("kfc", cache_dir, logger)
        self.config = config
        self._proxy_url = proxy_url
        self._service = KfcService(
            config,
            http_client=http_client,
            proxy_url=proxy_url,
            instances=instances,
        )

    async def fetch_fresh(self) -> str | None:
        """从网络获取新鲜数据（仅周四获取）。

        Returns:
            KFC 文案字符串，如果不是周四或获取失败返回 None
        """
        # 仅周四获取 KFC 文案
        if today_business().weekday() != 3:
            return None
        try:
            return await self._service.fetch_kfc_copy()
        except Exception as e:
            self.logger.error(
                "Failed to fetch KFC content: %s",
                safe_exception_for_log(e, self.config.url, self._proxy_url),
            )
            return None

    async def get(self, force_refresh: bool = False) -> str | None:
        """获取 KFC 文案（非周四直接返回 None，不走 stale 回退）"""
        if today_business().weekday() != 3:
            self.logger.debug("Not Thursday, skipping KFC content")
            return None
        return await super().get(force_refresh)
