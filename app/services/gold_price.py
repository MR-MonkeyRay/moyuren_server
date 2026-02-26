"""Gold price service module."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from app.core.config import GoldPriceSource
from app.services.daily_cache import DailyCache


class GoldPriceService:
    """Service for fetching gold price data."""

    def __init__(
        self,
        config: GoldPriceSource,
        http_client: httpx.AsyncClient | None = None,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self._http_client = http_client
        self._logger = logger if logger else logging.getLogger(__name__)

    @asynccontextmanager
    async def _get_client(self):
        """Yield an HTTP client, reusing injected one or creating temporary."""
        if self._http_client:
            yield self._http_client
        else:
            async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
                yield client

    def _parse_response(self, data: Any) -> dict[str, Any] | None:
        """Parse gold price API response.

        Returns:
            Dictionary with today_price, sell_price, unit if found, None otherwise.
        """
        if not isinstance(data, dict):
            self._logger.warning(f"Gold price API returned non-dict response: {type(data).__name__}")
            return None
        metals = data.get("data", {}).get("metals")
        if not isinstance(metals, list):
            self._logger.warning("Unexpected gold price API response structure")
            return None

        for metal in metals:
            if isinstance(metal, dict) and metal.get("name") == "今日金价":
                return {
                    "today_price": metal.get("today_price"),
                    "sell_price": metal.get("sell_price"),
                    "unit": metal.get("unit", "元/克"),
                }

        self._logger.warning("No '今日金价' found in gold price API response")
        return None

    async def fetch_gold_price(self) -> dict[str, Any] | None:
        """Fetch today's gold price from API.

        Returns:
            Dictionary with today_price, sell_price, unit if successful, None otherwise.
        """
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    self.config.url, timeout=self.config.timeout_sec
                )
                resp.raise_for_status()
                return self._parse_response(resp.json())
        except httpx.TimeoutException:
            self._logger.warning("Timeout fetching gold price")
        except httpx.HTTPStatusError as e:
            self._logger.warning(f"HTTP {e.response.status_code} from gold price endpoint")
        except Exception as e:
            self._logger.warning(f"Failed to fetch gold price: {e}")
        return None


class CachedGoldPriceService(DailyCache[dict]):
    """带日级缓存的金价服务。"""

    def __init__(
        self,
        config: GoldPriceSource,
        logger: logging.Logger,
        cache_dir: Path,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__("gold_price", cache_dir, logger)
        self._service = GoldPriceService(config, http_client, logger)

    async def fetch_fresh(self) -> dict[str, Any] | None:
        return await self._service.fetch_gold_price()
