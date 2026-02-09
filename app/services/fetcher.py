"""Data fetching service module."""

import logging
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
            self.logger.warning(
                f"HTTP error fetching from {self.source.type}: {e.response.status_code}"
            )
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
    ) -> None:
        super().__init__("news", cache_dir, logger)
        self._fetcher = DataFetcher(source, logger, http_client=http_client)

    async def fetch_fresh(self) -> dict[str, Any] | None:
        """从网络获取新鲜数据。

        Returns:
            获取的数据字典，如果获取失败返回 None
        """
        try:
            return await self._fetcher.fetch_all()
        except Exception as e:
            self.logger.error(f"Failed to fetch data: {e}")
            return None
