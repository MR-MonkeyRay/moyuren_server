"""Stock index service module."""

import asyncio
import logging
from datetime import datetime
from json import JSONDecodeError
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import httpx

from app.core.config import StockIndexSource
from app.core.network import create_async_client, safe_exception_for_log

logger = logging.getLogger(__name__)

# Market to exchange calendar mapping
MARKET_CALENDAR_MAP = {
    "A": "XSHG",  # 上海证券交易所
    "HK": "XHKG",  # 香港交易所
    "US": "XNYS",  # 纽约证券交易所
}

# Default timezone for fallback
DEFAULT_TIMEZONE = "Asia/Shanghai"


class StockIndexService:
    """Service for fetching stock market index data."""

    def __init__(
        self,
        config: StockIndexSource,
        proxy_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the service with configuration.

        Args:
            config: Stock index configuration.
            proxy_url: Optional outbound proxy URL.
            http_client: Optional external HTTP client owned by the caller.
        """
        self.config = config
        self._cache: dict[str, Any] = {"data": None, "fetched_at": 0}
        self._calendars: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = http_client
        self._owns_http_client = False
        self._proxy_url = proxy_url
        self._init_calendars()

    def _init_calendars(self) -> None:
        """Initialize exchange calendars for configured markets."""
        for market in dict.fromkeys(item.market for item in self.config.secids):
            calendar_code = MARKET_CALENDAR_MAP.get(market)
            if calendar_code is None:
                continue
            try:
                self._calendars[market] = xcals.get_calendar(calendar_code)
                logger.debug(f"Initialized calendar for {market}: {calendar_code}")
            except Exception as e:
                logger.warning(f"Failed to initialize calendar for {market}: {e}")
                self._calendars[market] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for connection reuse."""
        if self._http_client is None or (
            self._owns_http_client and self._http_client.is_closed
        ):
            self._http_client = create_async_client(
                timeout=self.config.timeout_sec, proxy_url=self._proxy_url
            )
            self._owns_http_client = True
        return self._http_client

    async def close(self) -> None:
        """Close an internally owned HTTP client."""
        if (
            self._owns_http_client
            and self._http_client
            and not self._http_client.is_closed
        ):
            await self._http_client.aclose()
            self._http_client = None
            self._owns_http_client = False

    def _get_timezone(self, market: str) -> ZoneInfo:
        """Get timezone for market with fallback protection."""
        tz_name = self.config.market_timezones.get(market, DEFAULT_TIMEZONE)
        try:
            return ZoneInfo(tz_name)
        except Exception:
            logger.warning(
                f"Invalid timezone '{tz_name}' for market {market}, using {DEFAULT_TIMEZONE}"
            )
            return ZoneInfo(DEFAULT_TIMEZONE)

    async def fetch_indices(self, now: datetime | None = None) -> dict[str, Any]:
        """Fetch stock index data.

        Args:
            now: Current datetime for cache and trading day check.

        Returns:
            Dictionary containing index data and metadata.
        """
        if now is None:
            tz = self._get_timezone("A")
            now = datetime.now(tz)
        elif now.tzinfo is None:
            # Handle naive datetime by assuming default timezone
            tz = self._get_timezone("A")
            now = now.replace(tzinfo=tz)

        # Check cache
        cache_age = now.timestamp() - self._cache.get("fetched_at", 0)
        if cache_age < self.config.cache_ttl_sec and self._cache.get("data"):
            logger.debug("Returning cached stock index data")
            return self._cache["data"]

        async with self._lock:
            # Double-check cache after acquiring lock
            cache_age = now.timestamp() - self._cache.get("fetched_at", 0)
            if cache_age < self.config.cache_ttl_sec and self._cache.get("data"):
                return self._cache["data"]

            # Fetch data
            try:
                quotes = await self._fetch_quotes()
                trading_days = self._get_trading_days(now)

                items = self._process_quotes(quotes, trading_days, now)

                result = {
                    "items": items,
                    "updated": now.strftime("%Y/%m/%d %H:%M:%S"),
                    "updated_at": int(now.timestamp() * 1000),
                    "trading_day": trading_days,
                    "is_stale": False,
                }

                self._cache = {"data": result, "fetched_at": now.timestamp()}
                logger.info(f"Fetched {len(items)} stock indices")
                return result

            except Exception as e:
                logger.warning(
                    f"Failed to fetch stock indices: {safe_exception_for_log(e, self.config.quote_url, self._proxy_url)}"
                )
                # Return stale cache if available
                if self._cache.get("data"):
                    stale_data = self._cache["data"].copy()
                    stale_data["is_stale"] = True
                    return stale_data
                # Return placeholder data
                return self._get_placeholder_data(now)

    async def _fetch_quotes(self) -> list[dict[str, Any]]:
        """Fetch quotes from Eastmoney API."""
        secids = ",".join(item.secid for item in self.config.secids)
        url = f"{self.config.quote_url}?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids}"

        client = await self._get_http_client()
        resp = await client.get(url, timeout=httpx.Timeout(self.config.timeout_sec))
        resp.raise_for_status()

        try:
            data = resp.json()
        except JSONDecodeError as e:
            logger.error(f"Failed to parse API response as JSON: {e}")
            raise ValueError("Invalid JSON response from stock API") from e

        if data.get("rc") != 0:
            raise ValueError(f"API returned error: {data.get('msg', 'unknown')}")

        return data.get("data", {}).get("diff", [])

    def _get_trading_days(self, now: datetime) -> dict[str, bool]:
        """Get trading day status for each market using exchange_calendars.

        Args:
            now: Current datetime.

        Returns:
            Dictionary mapping market code to trading day status.
        """
        trading_days: dict[str, bool] = {}

        for market in dict.fromkeys(item.market for item in self.config.secids):
            # Get local date for each market's timezone
            tz = self._get_timezone(market)
            local_now = now.astimezone(tz)
            local_date = local_now.date()

            calendar = self._calendars.get(market)
            if calendar is not None:
                try:
                    trading_days[market] = calendar.is_session(local_date)
                except Exception as e:
                    logger.warning(f"Failed to check trading day for {market}: {e}")
                    trading_days[market] = local_now.weekday() < 5
            else:
                trading_days[market] = local_now.weekday() < 5

        return trading_days

    def _fallback_trading_day_check(self, now: datetime, market: str) -> bool:
        """Fallback trading day check using weekday only.

        Args:
            now: Current datetime.
            market: Market code.

        Returns:
            True if weekday (Mon-Fri), False otherwise.
        """
        tz = self._get_timezone(market)
        local_now = now.astimezone(tz)
        return local_now.weekday() < 5

    def _process_quotes(
        self,
        quotes: list[dict[str, Any]],
        trading_days: dict[str, bool],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Process quotes in config order, always using configured names."""
        # Create lookup by code
        quote_map = {q.get("f12"): q for q in quotes}

        items = []
        for item in self.config.secids:
            code = item.code
            quote = quote_map.get(code)
            market = item.market
            is_trading_day = trading_days.get(market, True)

            if not quote:
                # Add placeholder for missing index
                items.append(
                    {
                        "code": code,
                        "name": item.name,
                        "price": None,
                        "change": None,
                        "change_pct": None,
                        "trend": "flat",
                        "market": market,
                        "is_trading_day": is_trading_day,
                    }
                )
                continue

            price = quote.get("f2")
            change = quote.get("f4")
            change_pct = quote.get("f3")

            # Determine trend with type safety
            trend = "flat"
            if change_pct is not None:
                try:
                    pct_value = float(change_pct)
                    if pct_value > 0:
                        trend = "up"
                    elif pct_value < 0:
                        trend = "down"
                except (TypeError, ValueError):
                    pass

            items.append(
                {
                    "code": code,
                    "name": item.name,
                    "price": price,
                    "change": change,
                    "change_pct": change_pct,
                    "trend": trend,
                    "market": market,
                    "is_trading_day": is_trading_day,
                }
            )

        return items

    def _get_placeholder_data(self, now: datetime) -> dict[str, Any]:
        """Get placeholder data when API fails."""
        return {
            "items": [],
            "updated": now.strftime("%Y/%m/%d %H:%M:%S"),
            "updated_at": int(now.timestamp() * 1000),
            "trading_day": self._get_trading_days(now),
            "is_stale": True,
            "is_data_missing": True,
        }
