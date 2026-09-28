"""Data fetching service module."""

import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.core.config import InstancesConfig, NewsSource
from app.core.network import create_async_client, safe_exception_for_log
from app.services.calendar import today_business
from app.services.daily_cache import DailyCache
from app.services.instances import InstanceRouter

# 60s 静态镜像（GitHub 原始源，硬编码，与 holiday 服务的镜像策略保持一致）。
# 60s.viki.moe 的 /v2/60s 对数据中心/海外出口 IP 会返回 Cloudflare 403，
# 而静态镜像由 Vercel / jsDelivr 承载，可直接访问。
_NEWS_STATIC_RAW_PATH = (
    "raw.githubusercontent.com/vikiboss/60s-static-host/main/static/60s/{date}.json"
)
_NEWS_STATIC_CDN_URLS = (
    "https://cdn.jsdmirror.com/gh/vikiboss/60s-static-host/static/60s/{date}.json",
    "https://60s-static.viki.moe/60s/{date}.json",
    "https://raw.githubusercontent.com/vikiboss/60s-static-host/main/static/60s/{date}.json",
)


class DataFetcher:
    """Asynchronous data fetcher for news API."""

    def __init__(
        self,
        source: NewsSource,
        logger: logging.Logger,
        http_client: httpx.AsyncClient | None = None,
        proxy_url: str | None = None,
        ghproxy_urls: list[str] | None = None,
        date_provider: Callable[[], date] | None = None,
        instances: InstancesConfig | None = None,
    ) -> None:
        """初始化新闻数据获取器。

        Args:
            source: 新闻源配置，包含请求地址、参数与超时时间。
            logger: 日志记录器。
            http_client: 可选外部 HTTP 客户端；提供时复用该客户端发送请求。
            proxy_url: 可选全局代理 URL；仅在未注入 HTTP 客户端时生效。
            ghproxy_urls: GitHub raw 镜像代理前缀列表；主源不可用时用于构建静态镜像兜底地址。
            date_provider: 可选日期提供器，用于确定兜底镜像请求的日期；默认使用业务日期。
            instances: 全局 60s 公共实例配置；主源不可用时按健康度切换。
        """
        self.source = source
        self.logger = logger
        self._client = http_client
        self._proxy_url = proxy_url
        self._ghproxy_urls = ghproxy_urls or []
        self._date_provider = date_provider
        self._router = InstanceRouter.from_config(
            instances,
            logger,
            proxy_url=proxy_url,
            timeout_sec=float(source.timeout_sec),
        )

    async def fetch(self) -> dict[str, Any] | None:
        """Fetch data from the news source.

        依次尝试主源、公共实例（智能切换），最后回退到 60s 静态镜像，
        避免新闻长期停留在旧日期。

        Returns:
            与主源一致的 API 信封字典；所有来源均失败时返回 None。
        """
        try:
            if self._client is not None:
                return await self._fetch_with_fallbacks(self._client)
            async with create_async_client(proxy_url=self._proxy_url) as client:
                return await self._fetch_with_fallbacks(client)
        except httpx.RequestError as e:
            self.logger.warning(
                "News fetch request failed: %s",
                safe_exception_for_log(e, str(self.source.url), self._proxy_url),
            )
            return None

    async def _fetch_with_fallbacks(
        self, client: httpx.AsyncClient
    ) -> dict[str, Any] | None:
        """依次尝试 API 端点与静态镜像。

        Args:
            client: 用于请求的 HTTP 客户端。

        Returns:
            首个成功来源的 JSON 字典；全部失败时返回 None。
        """
        result = await self._router.fetch_json(
            client,
            str(self.source.url),
            parse=lambda data: data if isinstance(data, dict) else None,
            params=self.source.params,
            timeout=httpx.Timeout(self.source.timeout_sec),
        )
        if result is not None:
            return result[1]

        self.logger.warning(
            f"All news endpoints unavailable for {self.source.type}, trying static mirror"
        )
        return await self._fetch_static_with_client(client)

    def _build_static_urls(self, target_date: date) -> list[str]:
        """构建指定日期的静态镜像地址列表。

        Args:
            target_date: 需要获取的新闻日期。

        Returns:
            按优先级排序的镜像地址；优先使用 CDN 直连，其次为 GitHub 代理前缀，最后为 GitHub 原始源。
        """
        date_str = target_date.isoformat()
        urls = [url.format(date=date_str) for url in _NEWS_STATIC_CDN_URLS]
        for prefix in self._ghproxy_urls:
            if not prefix.startswith(("http://", "https://")):
                continue
            urls.append(
                f"{prefix.rstrip('/')}/{_NEWS_STATIC_RAW_PATH.format(date=date_str)}"
            )
        return urls

    async def _fetch_static_with_client(
        self, client: httpx.AsyncClient
    ) -> dict[str, Any] | None:
        """按日期倒序尝试静态镜像，返回首个成功结果（包装为主源信封格式）。

        Args:
            client: 用于请求的 HTTP 客户端。

        Returns:
            包装为 ``{"code": 200, "message": "success", "data": ...}`` 的字典；
            所有镜像均失败时返回 None。
        """
        provider = self._date_provider or today_business
        today = provider()

        for target_date in (today, today - timedelta(days=1)):
            for url in self._build_static_urls(target_date):
                try:
                    response = await client.get(
                        url, timeout=httpx.Timeout(self.source.timeout_sec)
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError) as e:
                    self.logger.debug(
                        "Static news mirror failed: %s",
                        safe_exception_for_log(e, url, self._proxy_url),
                    )
                    continue

                news_items = payload.get("news") if isinstance(payload, dict) else None
                if isinstance(news_items, list) and news_items:
                    self.logger.info(
                        "Fetched news from static mirror for %s",
                        target_date.isoformat(),
                    )
                    return {"code": 200, "message": "success", "data": payload}

        self.logger.warning(
            "All static news mirrors failed (today=%s)", today.isoformat()
        )
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
        proxy_url: str | None = None,
        ghproxy_urls: list[str] | None = None,
        date_provider: Callable[[], date] | None = None,
        instances: InstancesConfig | None = None,
    ) -> None:
        """初始化带日级缓存的新闻数据获取器。

        Args:
            source: 新闻源配置。
            logger: 日志记录器。
            cache_dir: 日级缓存目录。
            http_client: 可选外部 HTTP 客户端。
            proxy_url: 可选全局代理 URL；仅在未注入 HTTP 客户端时生效。
            ghproxy_urls: GitHub raw 镜像代理前缀列表；主源不可用时用于静态镜像兜底。
            date_provider: 可选日期提供器，用于测试或替换业务日期来源。
            instances: 全局 60s 公共实例配置；主源不可用时按健康度切换。

        Side Effects:
            初始化 DailyCache 命名空间并创建内部 DataFetcher。
        """
        super().__init__("news", cache_dir, logger, date_provider=date_provider)
        self._fetcher = DataFetcher(
            source,
            logger,
            http_client=http_client,
            proxy_url=proxy_url,
            ghproxy_urls=ghproxy_urls,
            date_provider=date_provider,
            instances=instances,
        )

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
