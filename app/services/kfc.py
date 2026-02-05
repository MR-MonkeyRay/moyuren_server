"""KFC Crazy Thursday service module."""

import logging
import httpx
from pathlib import Path
from typing import Any

from app.core.config import CrazyThursdayConfig
from app.services.calendar import today_business
from app.services.daily_cache import DailyCache

logger = logging.getLogger(__name__)


class KfcService:
    """Service for fetching KFC Crazy Thursday content."""

    def __init__(self, config: CrazyThursdayConfig):
        """Initialize the service with configuration.

        Args:
            config: Crazy Thursday configuration.
        """
        self.config = config

    async def fetch_kfc_copy(self) -> str | None:
        """Fetch KFC crazy thursday copy.
        
        Returns:
            The content string if successful, None otherwise.
        """
        if not self.config.enabled:
            return None

        async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
            try:
                resp = await client.get(self.config.url)
                resp.raise_for_status()
                data = resp.json()
                
                # Viki API structure handling
                # Expected format: {"code": 200, "data": {"kfc": "..."}}
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

                if content:
                    # Handle escaped newlines in the text
                    content = str(content).strip().replace("\\n", "\n")
                    return content
                
                logger.warning("Empty content received from KFC endpoint")
                return None
                
            except httpx.TimeoutException:
                logger.warning("Timeout fetching KFC content")
            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP {e.response.status_code} from KFC endpoint")
            except Exception as e:
                logger.warning(f"Failed to fetch KFC content: {e}")

        return None


class CachedKfcService(DailyCache[str]):
    """带日级缓存的 KFC 服务。

    继承 DailyCache，为 KfcService 提供日级缓存能力。
    仅在周四获取 KFC 文案，缓存在每日零点自动过期。
    """

    def __init__(
        self,
        config: CrazyThursdayConfig,
        logger: logging.Logger,
        cache_dir: Path,
    ) -> None:
        """初始化带缓存的 KFC 服务。

        Args:
            config: 疯狂星期四配置
            logger: 日志记录器
            cache_dir: 缓存目录路径
        """
        super().__init__("kfc", cache_dir, logger)
        self._service = KfcService(config)

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
            self.logger.error(f"Failed to fetch KFC content: {e}")
            return None

