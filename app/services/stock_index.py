"""股票指数服务模块。"""

import asyncio
import logging
from datetime import datetime
from json import JSONDecodeError
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import httpx

from app.core.config import StockIndexSource
from app.core.metrics import FetchOutcome, record_source_fetch
from app.core.network import create_async_client, safe_exception_for_log

logger = logging.getLogger(__name__)

# 市场与交易所日历的映射
MARKET_CALENDAR_MAP = {
    "A": "XSHG",  # 上海证券交易所
    "HK": "XHKG",  # 香港交易所
    "US": "XNYS",  # 纽约证券交易所
}

# 回退使用的默认时区
DEFAULT_TIMEZONE = "Asia/Shanghai"


class StockIndexService:
    """获取股票市场指数数据的服务。"""

    def __init__(
        self,
        config: StockIndexSource,
        proxy_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """根据配置初始化服务。

        Args:
            config: 股票指数配置。
            proxy_url: 可选的出站代理 URL。
            http_client: 可选的外部 HTTP 客户端，由调用方管理生命周期。
        """
        self.config = config
        self._cache: dict[str, Any] = {"data": None, "fetched_at": 0}
        self._calendars: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = http_client
        self._owns_http_client = False
        self._proxy_url = proxy_url
        self.namespace = "stock_index"
        self.last_outcome: FetchOutcome | None = None
        self._init_calendars()

    def _record_outcome(self, outcome: FetchOutcome) -> None:
        """记录本次获取结果并更新指标。"""
        self.last_outcome = outcome
        record_source_fetch(self.namespace, outcome)

    def _init_calendars(self) -> None:
        """为配置的市场初始化交易所日历。"""
        for market in dict.fromkeys(item.market for item in self.config.secids):
            calendar_code = MARKET_CALENDAR_MAP.get(market)
            if calendar_code is None:
                continue
            try:
                self._calendars[market] = xcals.get_calendar(calendar_code)
                logger.debug("Initialized calendar for %s: %s", market, calendar_code)
            except Exception as e:
                logger.warning("Failed to initialize calendar for %s: %s", market, e)
                self._calendars[market] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端以复用连接。"""
        if self._http_client is None or (
            self._owns_http_client and self._http_client.is_closed
        ):
            self._http_client = create_async_client(
                timeout=self.config.timeout_sec, proxy_url=self._proxy_url
            )
            self._owns_http_client = True
        return self._http_client

    async def close(self) -> None:
        """关闭由服务自身管理的 HTTP 客户端。"""
        if (
            self._owns_http_client
            and self._http_client
            and not self._http_client.is_closed
        ):
            await self._http_client.aclose()
            self._http_client = None
            self._owns_http_client = False

    def _get_timezone(self, market: str) -> ZoneInfo:
        """获取市场时区，无效时回退到默认时区。"""
        tz_name = self.config.market_timezones.get(market, DEFAULT_TIMEZONE)
        try:
            return ZoneInfo(tz_name)
        except Exception:
            logger.warning(
                "Invalid timezone '%s' for market %s, using %s", tz_name, market, DEFAULT_TIMEZONE
            )
            return ZoneInfo(DEFAULT_TIMEZONE)

    async def fetch_indices(self, now: datetime | None = None) -> dict[str, Any]:
        """获取股票指数数据。

        Args:
            now: 用于缓存和交易日判断的当前时间。

        Returns:
            包含指数数据和元数据的字典。
        """
        if now is None:
            tz = self._get_timezone("A")
            now = datetime.now(tz)
        elif now.tzinfo is None:
            # 未携带时区的时间按默认时区处理
            tz = self._get_timezone("A")
            now = now.replace(tzinfo=tz)

        # 检查缓存
        cache_age = now.timestamp() - self._cache.get("fetched_at", 0)
        if cache_age < self.config.cache_ttl_sec and self._cache.get("data"):
            logger.debug("Returning cached stock index data")
            self._record_outcome("cache")
            return self._cache["data"]

        async with self._lock:
            # 获取锁后再次检查缓存
            cache_age = now.timestamp() - self._cache.get("fetched_at", 0)
            if cache_age < self.config.cache_ttl_sec and self._cache.get("data"):
                self._record_outcome("cache")
                return self._cache["data"]

            # 获取数据
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
                logger.info("Fetched %s stock indices", len(items))
                self._record_outcome("fresh")
                return result

            except Exception as e:
                logger.warning(
                    "Failed to fetch stock indices: %s",
                    safe_exception_for_log(e, self.config.quote_url, self._proxy_url),
                )
                # 有过期缓存时返回过期缓存
                if self._cache.get("data"):
                    stale_data = self._cache["data"].copy()
                    stale_data["is_stale"] = True
                    self._record_outcome("stale")
                    return stale_data
                # 返回占位数据
                self._record_outcome("failed")
                return self._get_placeholder_data(now)

    async def _fetch_quotes(self) -> list[dict[str, Any]]:
        """从东方财富 API 获取行情。"""
        secids = ",".join(item.secid for item in self.config.secids)
        url = f"{self.config.quote_url}?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids}"

        client = await self._get_http_client()
        resp = await client.get(url, timeout=httpx.Timeout(self.config.timeout_sec))
        resp.raise_for_status()

        try:
            data = resp.json()
        except JSONDecodeError as e:
            logger.error("Failed to parse API response as JSON: %s", e)
            raise ValueError("Invalid JSON response from stock API") from e

        if data.get("rc") != 0:
            raise ValueError(f"API returned error: {data.get('msg', 'unknown')}")

        return data.get("data", {}).get("diff", [])

    def _get_trading_days(self, now: datetime) -> dict[str, bool]:
        """使用 exchange_calendars 获取各市场的交易日状态。

        Args:
            now: 当前时间。

        Returns:
            市场代码到交易日状态的映射字典。
        """
        trading_days: dict[str, bool] = {}

        for market in dict.fromkeys(item.market for item in self.config.secids):
            # 按各市场时区获取当地日期
            tz = self._get_timezone(market)
            local_now = now.astimezone(tz)
            local_date = local_now.date()

            calendar = self._calendars.get(market)
            if calendar is not None:
                try:
                    trading_days[market] = calendar.is_session(local_date)
                except Exception as e:
                    logger.warning("Failed to check trading day for %s: %s", market, e)
                    trading_days[market] = local_now.weekday() < 5
            else:
                trading_days[market] = local_now.weekday() < 5

        return trading_days

    def _fallback_trading_day_check(self, now: datetime, market: str) -> bool:
        """交易日历不可用时，仅按星期几判断交易日。

        Args:
            now: 当前时间。
            market: 市场代码。

        Returns:
            周一至周五返回 True，其余返回 False。
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
        """按配置顺序处理行情，始终使用配置的名称。"""
        # 建立按代码查询的映射
        quote_map = {q.get("f12"): q for q in quotes}

        items = []
        for item in self.config.secids:
            code = item.code
            quote = quote_map.get(code)
            market = item.market
            is_trading_day = trading_days.get(market, True)

            if not quote:
                # 为缺失的指数添加占位数据
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

            # 安全转换数值后判断涨跌趋势
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
        """API 失败时获取占位数据。"""
        return {
            "items": [],
            "updated": now.strftime("%Y/%m/%d %H:%M:%S"),
            "updated_at": int(now.timestamp() * 1000),
            "trading_day": self._get_trading_days(now),
            "is_stale": True,
            "is_data_missing": True,
        }
