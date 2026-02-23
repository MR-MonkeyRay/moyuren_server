"""Data fetching service module."""

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.config import NewsSource
from app.services.daily_cache import DailyCache


class DataFetcher:
    """Asynchronous data fetcher for news API."""

    def __init__(
        self,
        source: NewsSource,
        logger: logging.Logger,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.source = source
        self.logger = logger
        self._client = http_client

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
    ) -> dict[str, Any] | None:
        """Fetch data using the provided client."""
        response = await client.get(
            str(self.source.url),
            params=self.source.params,
            timeout=httpx.Timeout(self.source.timeout_sec),
        )
        response.raise_for_status()
        data = response.json()
        self.logger.info(f"Successfully fetched from {self.source.type}")
        return data

    async def fetch(self) -> dict[str, Any] | None:
        """Fetch data from the news source.

        Returns:
            The response data as dict, or None if request fails.
        """
        try:
            self.logger.debug(f"Fetching from source: {self.source.type}")

            if self._client is not None:
                return await self._fetch_with_client(self._client)
            async with httpx.AsyncClient() as client:
                return await self._fetch_with_client(client)

        except httpx.TimeoutException:
            self.logger.warning(f"Timeout fetching from {self.source.type}")
            return None
        except httpx.HTTPStatusError as e:
            self.logger.warning(f"HTTP error fetching from {self.source.type}: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            self.logger.warning(f"Request error fetching from {self.source.type}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error fetching from {self.source.type}: {e}")
            return None

    async def fetch_all(self) -> dict[str, dict[str, Any] | None]:
        """Fetch data and return in legacy format for backward compatibility.

        Returns:
            A dictionary mapping source name to fetched data.
        """
        self.logger.info("Fetching news data")
        result = await self.fetch()
        return {"news": result}


class CachedDataFetcher(DailyCache[dict[str, Any]]):
    """带日级缓存的数据获取器。

    继承 DailyCache，为 DataFetcher 提供日级缓存能力。
    缓存在每日零点自动过期，网络获取失败时返回过期缓存。
    """

    def __init__(
        self,
        source: NewsSource,
        logger: logging.Logger,
        cache_dir: Path,
        http_client: httpx.AsyncClient | None = None,
        date_provider: Any = None,
    ) -> None:
        super().__init__("news", cache_dir, logger, date_provider=date_provider)
        self._fetcher = DataFetcher(source, logger, http_client=http_client)

    def _extract_news_date(self, data: dict[str, Any] | None) -> date | None:
        """从数据中提取新闻日期。

        支持多种日期格式：
        - "2026-02-23" (ISO 格式)
        - "2026/02/23" (斜杠分隔)
        - "2026年2月4日" (中文格式)

        Args:
            data: 数据字典，格式为 {"news": {"code": 200, "data": {"date": "2026-02-23", ...}}}

        Returns:
            新闻日期对象，提取失败返回 None
        """
        if data is None:
            return None

        try:
            news_data = data.get("news")
            if news_data is None:
                return None

            date_str = news_data.get("data", {}).get("date")
            if not isinstance(date_str, str):
                return None

            # 尝试 ISO 格式 (YYYY-MM-DD)
            try:
                return date.fromisoformat(date_str)
            except (ValueError, AttributeError):
                pass

            # 尝试斜杠格式 (YYYY/MM/DD)
            try:
                return datetime.strptime(date_str, "%Y/%m/%d").date()
            except ValueError:
                pass

            # 尝试中文格式 (YYYY年M月D日)
            match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
            if match:
                year, month, day = match.groups()
                return date(int(year), int(month), int(day))

            # 都失败
            self.logger.warning(f"无法解析日期格式: {date_str}")
            return None

        except (AttributeError, TypeError, ValueError) as e:
            self.logger.warning(f"提取新闻日期时出错: {e}")
            return None

    async def fetch_fresh(self) -> dict[str, Any] | None:
        """从网络获取新鲜数据。

        Returns:
            获取的数据字典，如果获取失败返回 None
        """
        try:
            result = await self._fetcher.fetch_all()
            # 检查 fetch_all 返回的数据是否有效
            # fetch_all 返回 {"news": <data>}，如果 <data> 是 None 说明获取失败
            if result is not None and result.get("news") is None:
                return None
            return result
        except Exception as e:
            self.logger.error(f"Failed to fetch data: {e}")
            return None

    async def get(self, force_refresh: bool = False) -> dict[str, Any] | None:
        """获取数据（新闻日期感知版本）。

        逻辑:
        1. 如果 force_refresh=True，直接调 API，成功则保存并返回，失败则降级
        2. 如果 force_refresh=False：
           a. 加载本地缓存，提取缓存中的新闻日期
           b. 如果缓存新闻日期 == 今天日期，直接返回缓存
           c. 否则调 API 获取新数据
              - 如果 API 新闻日期 == 今天日期，保存并返回新数据
              - 如果 API 新闻日期 != 今天日期（API 未更新），返回本地缓存（不覆盖）
           d. API 调用失败时，返回本地缓存（降级策略）

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            数据，如果获取失败返回 None
        """
        from app.services.calendar import today_business

        # 获取今天的 date 对象
        provider = self._date_provider or today_business
        today = provider()

        # 1. 强制刷新模式
        if force_refresh:
            self.logger.info("Force refresh mode for %s", self.namespace)
            fresh_data: dict[str, Any] | None = None
            try:
                fresh_data = await self.fetch_fresh()
            except Exception as e:
                self.logger.exception(
                    "Exception while fetching fresh data for %s: %s",
                    self.namespace,
                    e,
                )

            if fresh_data is not None:
                self.save_cache(fresh_data)
                return fresh_data

            # 降级：返回过期缓存
            self.logger.warning(
                "Force refresh failed for %s, trying stale cache",
                self.namespace,
            )
            stale_data = self.load_cache()
            if stale_data is not None:
                self.logger.info(
                    "Using stale cache for %s as fallback",
                    self.namespace,
                )
                return stale_data

            self.logger.error(
                "No data available for %s (fresh fetch failed and no cache)",
                self.namespace,
            )
            return None

        # 2. 正常模式：检查缓存中的新闻日期
        cached_data = self.load_cache()
        cached_news_date = self._extract_news_date(cached_data)

        # 2a. 如果缓存新闻日期 == 今天，直接返回缓存
        if cached_news_date == today:
            self.logger.debug(
                "Cache hit for %s: news date matches today (%s)",
                self.namespace,
                today.isoformat(),
            )
            return cached_data

        # 2b. 缓存新闻日期不是今天（或无缓存），调用 API
        self.logger.info(
            "Fetching fresh data for %s (cached_news_date=%s, today=%s)",
            self.namespace,
            cached_news_date.isoformat() if cached_news_date else None,
            today.isoformat(),
        )
        fresh_data = None
        try:
            fresh_data = await self.fetch_fresh()
        except Exception as e:
            self.logger.exception(
                "Exception while fetching fresh data for %s: %s",
                self.namespace,
                e,
            )

        # 2c. API 调用成功，检查 API 返回的新闻日期
        if fresh_data is not None:
            api_news_date = self._extract_news_date(fresh_data)

            # 无法解析 API 日期
            if api_news_date is None:
                self.logger.warning(
                    "无法从 API 响应中提取日期，保存到缓存（无法判断是否为今天）"
                )
                self.save_cache(fresh_data)
                return fresh_data

            # API 新闻日期 == 今天，保存并返回
            if api_news_date == today:
                self.logger.info(
                    "API news date matches today (%s), saving to cache",
                    today.isoformat(),
                )
                self.save_cache(fresh_data)
                return fresh_data

            # API 新闻日期 != 今天（API 还没更新）
            self.logger.warning(
                "API 新闻日期 (%s) 非今天 (%s)，API 尚未更新",
                api_news_date.isoformat(),
                today.isoformat(),
            )

            # 如果有本地缓存，返回本地缓存（不覆盖）
            if cached_data is not None:
                self.logger.info(
                    "Keeping local cache for %s (not overwriting with stale API data)",
                    self.namespace,
                )
                return cached_data

            # 如果没有本地缓存，保存 API 数据（总比没有好）
            self.logger.info(
                "No local cache, saving API data for %s (better than nothing)",
                self.namespace,
            )
            self.save_cache(fresh_data)
            return fresh_data

        # 2d. API 调用失败（fresh_data is None），降级返回本地缓存
        self.logger.warning(
            "Failed to fetch fresh data for %s, trying stale cache",
            self.namespace,
        )
        if cached_data is not None:
            self.logger.info(
                "Using stale cache for %s as fallback",
                self.namespace,
            )
            return cached_data

        # 都失败
        self.logger.error(
            "No data available for %s (fresh fetch failed and no cache)",
            self.namespace,
        )
        return None
