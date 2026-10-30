import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.core.network import create_async_client, redact_url, safe_exception_for_log
from app.services.calendar import get_business_timezone
from app.services.daily_cache import DailyCache

# GitHub 原始源（硬编码，不可配置）
GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"
)


def _future_make_up_workdays(values: Any, today: date) -> list[str]:
    """规范化补班日期，过滤无效与过往日期并排序去重。"""
    if not isinstance(values, list):
        return []
    workdays: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            workday = date.fromisoformat(value)
        except ValueError:
            continue
        if workday >= today:
            workdays.add(workday.isoformat())
    return sorted(workdays)


class HolidayService:
    """节假日数据服务，获取并处理中国法定节假日信息"""

    # 缓存 TTL 常量（秒）
    TTL_CURRENT_YEAR = 7 * 24 * 3600  # 7天
    TTL_NEXT_YEAR = 12 * 3600  # 12小时

    def __init__(
        self,
        logger: logging.Logger,
        cache_dir: str | Path = "cache/holidays",
        ghproxy_urls: list[str] | None = None,
        timeout_sec: int = 10,
        proxy_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化节假日数据服务。

        Args:
            logger: 日志记录器。
            cache_dir: 年度节假日原始数据缓存目录。
            ghproxy_urls: GitHub raw 镜像代理前缀列表，按顺序优先尝试。
            timeout_sec: 请求超时时间，单位秒。
            proxy_url: 可选全局代理 URL。
            http_client: 可选外部 HTTP 客户端；由调用方管理生命周期。

        Side Effects:
            创建缓存目录（如果不存在）。
        """
        self._logger = logger
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._ghproxy_urls = ghproxy_urls or []
        self._timeout_sec = timeout_sec
        self._proxy_url = proxy_url
        self._http_client = http_client
        self.make_up_workdays: list[str] = []

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """复用注入客户端，未注入时创建临时客户端。"""
        if self._http_client is not None:
            yield self._http_client
        else:
            async with create_async_client(
                timeout=self._timeout_sec, proxy_url=self._proxy_url
            ) as client:
                yield client

    def _build_urls(self, year: int) -> list[str]:
        """构建 URL 列表：镜像源优先，GitHub 原始源兜底"""
        urls = []
        # 镜像源：前缀 + raw.githubusercontent.com/...
        raw_path = (
            f"raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"
        )
        for proxy_prefix in self._ghproxy_urls:
            # 校验镜像前缀必须包含协议
            if not proxy_prefix.startswith(("http://", "https://")):
                self._logger.warning("跳过无效代理前缀（缺少协议）: %s", redact_url(proxy_prefix))
                continue
            # 确保代理地址以 / 结尾
            prefix = proxy_prefix.rstrip("/") + "/"
            urls.append(f"{prefix}{raw_path}")
        # GitHub 原始源兜底
        urls.append(GITHUB_RAW_URL.format(year=year))
        return urls

    def _get_today(self) -> date:
        """获取业务时区的今天日期"""
        return datetime.now(get_business_timezone()).date()

    def _get_ttl(self, year: int) -> int | None:
        """根据年份获取缓存 TTL（秒），往年返回 None 表示永久有效"""
        current_year = self._get_today().year
        if year < current_year:
            return None  # 往年：永久有效
        elif year == current_year:
            return self.TTL_CURRENT_YEAR  # 当年：7天
        else:
            return self.TTL_NEXT_YEAR  # 次年及以后：12小时

    def _is_cache_valid(self, year: int) -> bool:
        """检查缓存是否有效（基于 mtime）"""
        cache_file = self._cache_dir / f"{year}.json"
        if not cache_file.exists():
            return False

        ttl = self._get_ttl(year)
        if ttl is None:
            # 往年数据：缓存存在即有效
            self._logger.debug("%s 年为往年数据，缓存永久有效", year)
            return True

        # 检查 mtime
        mtime = os.path.getmtime(cache_file)
        age = time.time() - mtime
        # 兜底：系统时间回拨时视为过期
        if age < 0:
            self._logger.warning("%s 年缓存 mtime 异常（系统时间可能回拨），视为过期", year)
            return False
        if age < ttl:
            self._logger.debug(
                "%s 年缓存有效，已缓存 %.1f 小时，TTL %.1f 小时", year, age / 3600, ttl / 3600
            )
            return True
        else:
            self._logger.debug(
                "%s 年缓存已过期，已缓存 %.1f 小时，TTL %.1f 小时", year, age / 3600, ttl / 3600
            )
            return False

    async def fetch_holidays(self) -> list[dict[str, Any]] | None:
        """获取去年、今年和明年的节假日数据，合并处理后返回。

        Returns:
            至少一年获取成功时返回合并列表（可为空），全部失败时返回 None。
        """
        today = self._get_today()
        self.make_up_workdays = []
        current_year = today.year
        prev_year = current_year - 1
        next_year = current_year + 1

        self._logger.info(
            "开始获取 %s、%s 和 %s 年节假日数据", prev_year, current_year, next_year
        )

        async with self._get_client() as client:
            tasks = [
                self._fetch_year_data(client, prev_year),
                self._fetch_year_data(client, current_year),
                self._fetch_year_data(client, next_year),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_data = [data for data in results if isinstance(data, dict)]
        if not valid_data:
            self._logger.error("所有年份节假日数据均获取失败")
            return None

        return self._merge_and_process(valid_data)

    def _is_valid_year_data(self, data: Any) -> bool:
        """校验年度数据包含 days 列表，且每个条目均为字典"""
        return (
            isinstance(data, dict)
            and isinstance(data.get("days"), list)
            and all(isinstance(day, dict) for day in data["days"])
        )

    async def _fetch_year_data(
        self, client: httpx.AsyncClient, year: int
    ) -> dict[str, Any] | None:
        """获取指定年份的节假日数据，支持 TTL 缓存、多源重试和降级"""
        # 1. 检查缓存是否有效
        if self._is_cache_valid(year):
            cached_data = self._load_from_cache(year)
            if cached_data:
                self._logger.debug("使用 %s 年有效缓存数据", year)
                return cached_data

        # 2. 缓存无效或不存在，尝试从网络获取
        urls = self._build_urls(year)

        for url in urls:
            try:
                response = await client.get(
                    url, timeout=httpx.Timeout(self._timeout_sec)
                )
                response.raise_for_status()
                data = response.json()
                if not self._is_valid_year_data(data):
                    self._logger.warning("数据源 %s 返回的 %s 年数据格式无效", redact_url(url), year)
                    continue
                self._logger.debug("成功从 %s 获取 %s 年节假日数据", redact_url(url), year)

                # 成功获取后更新缓存
                self._save_to_cache(year, data)
                return data

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    self._logger.warning("数据源 %s 返回 404", redact_url(url))
                    continue
                self._logger.warning(
                    "数据源 %s 请求失败: %s",
                    redact_url(url),
                    safe_exception_for_log(e, url, self._proxy_url),
                )
            except Exception as e:
                self._logger.warning(
                    "数据源 %s 访问异常: %s",
                    redact_url(url),
                    safe_exception_for_log(e, url, self._proxy_url),
                )

        # 3. 网络请求全部失败，尝试读取过期缓存（降级策略）
        self._logger.warning("所有数据源均无法获取 %s 年数据，尝试读取本地缓存", year)
        cached_data = self._load_from_cache(year)
        if cached_data:
            self._logger.info("成功读取 %s 年本地缓存数据", year)
            return cached_data

        self._logger.error("无法获取 %s 年节假日数据 (网络失败且无缓存)", year)
        return None

    def _save_to_cache(self, year: int, data: dict[str, Any]) -> None:
        """通过临时文件和原子替换保存数据到本地缓存"""
        tmp_path: str | None = None
        try:
            cache_file = self._cache_dir / f"{year}.json"
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._cache_dir,
                delete=False,
                suffix=".tmp",
            ) as tmp_file:
                tmp_path = tmp_file.name
                json.dump(data, tmp_file, ensure_ascii=False, indent=2)
            os.replace(tmp_path, cache_file)
            self._logger.debug("已缓存 %s 年数据到 %s", year, cache_file)
        except Exception as e:
            self._logger.warning("缓存 %s 年数据失败: %s", year, e)
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

    def _load_from_cache(self, year: int) -> dict[str, Any] | None:
        """从本地缓存读取数据，格式无效时返回 None"""
        try:
            cache_file = self._cache_dir / f"{year}.json"
            if not cache_file.exists():
                return None

            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
            if not self._is_valid_year_data(data):
                self._logger.warning("%s 年缓存数据格式无效", year)
                return None
            return data
        except Exception as e:
            self._logger.warning("读取 %s 年缓存失败: %s", year, e)
            return None

    def _merge_and_process(
        self, data_list: Sequence[dict[str, Any] | None | Exception]
    ) -> list[dict[str, Any]]:
        """合并多年数据，过滤休息日，排序后聚合连续假期"""
        all_days: list[dict[str, Any]] = []

        for data in data_list:
            # 过滤异常结果（来自 gather 的 return_exceptions=True）
            if isinstance(data, Exception):
                self._logger.warning("跳过异常结果: %s", data)
                continue
            if data is None:
                continue
            days = data.get("days", [])
            all_days.extend(days)

        # 过滤：仅保留 isOffDay=True 的条目
        off_days = [d for d in all_days if d.get("isOffDay") is True]

        # 检查今天是否是补班日
        today = self._get_today()
        today_str = today.isoformat()
        workdays = [d for d in all_days if d.get("isOffDay") is False]
        self.make_up_workdays = _future_make_up_workdays(
            [day.get("date") for day in workdays], today
        )
        today_workday = next((d for d in workdays if d.get("date") == today_str), None)

        # 按 date 升序排序
        off_days.sort(key=lambda x: x.get("date", ""))

        self._logger.debug("合并后共 %s 个休息日", len(off_days))

        # 处理正常假期
        result = self._group_continuous_holidays(off_days)

        # 如果今天是补班日，在结果开头插入补班日条目
        if today_workday:
            holiday_name = today_workday.get("name", "假期")
            workday_entry = {
                "name": f"{holiday_name}（补班）",
                "start_date": today_str,
                "end_date": today_str,
                "duration": 1,
                "days_left": 0,
                "color": None,
                "is_off_day": False,
            }
            result.insert(0, workday_entry)
            self._logger.info("今天是补班日：%s", holiday_name)

        return result

    def _group_continuous_holidays(
        self, days: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """将连续的同名假期聚合为假期组"""
        if not days:
            return []

        today = self._get_today()
        groups: list[dict[str, Any]] = []
        current_group: list[dict[str, Any]] = []

        for day in days:
            try:
                day_date = date.fromisoformat(day["date"])
                day_name = day.get("name", "")
            except (KeyError, ValueError) as e:
                self._logger.warning("跳过无效日期数据: %s, 错误: %s", day, e)
                continue

            if not current_group:
                current_group = [day]
                continue

            last_day = current_group[-1]
            try:
                last_date = date.fromisoformat(last_day["date"])
                last_name = last_day.get("name", "")
            except (KeyError, ValueError) as e:
                self._logger.warning("跳过无效日期数据: %s, 错误: %s", last_day, e)
                current_group = [day]
                continue

            # 判断连续性：同名 AND 日期连续
            is_continuous = (day_name == last_name) and (
                day_date == last_date + timedelta(days=1)
            )

            if is_continuous:
                current_group.append(day)
            else:
                # 结束当前组，开始新组
                groups.append(self._build_group(current_group))
                current_group = [day]

        # 处理最后一组
        if current_group:
            groups.append(self._build_group(current_group))

        # 后处理过滤：保留未结束或未来的假期
        result = []
        for g in groups:
            try:
                if date.fromisoformat(g["end_date"]) >= today:
                    result.append(g)
            except (KeyError, ValueError) as e:
                self._logger.warning("跳过无效假期组: %s, 错误: %s", g, e)
                continue

        # 计算 days_left
        for group in result:
            try:
                start = date.fromisoformat(group["start_date"])
                group["days_left"] = (start - today).days if start > today else 0
            except (KeyError, ValueError) as e:
                self._logger.warning("无法计算 days_left: %s, 错误: %s", group, e)
                group["days_left"] = 0

        self._logger.info("处理完成，共 %s 个有效假期", len(result))

        return result

    def _build_group(self, group_days: list[dict[str, Any]]) -> dict[str, Any]:
        """从一组连续假期天构建假期组信息"""
        start_date = group_days[0]["date"]
        end_date = group_days[-1]["date"]
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        duration = (end - start).days + 1

        return {
            "name": group_days[0].get("name", ""),
            "start_date": start_date,
            "end_date": end_date,
            "duration": duration,
            "days_left": 0,  # 稍后计算
            "color": None,
            "is_off_day": True,  # 正常假期都是休息日
        }


class CachedHolidayService(DailyCache[dict[str, Any]]):
    """带日级缓存的节假日服务。

    原始年度数据仍保留在 cache/holidays/ 目录，
    聚合后的节假日列表与补班日期缓存在 cache/holidays.json。

    继承 DailyCache，为 HolidayService 提供日级缓存能力。
    缓存在每日零点自动过期，网络获取失败时返回过期缓存。
    """

    def __init__(
        self,
        logger: logging.Logger,
        cache_dir: Path,
        raw_cache_dir: Path,
        ghproxy_urls: list[str] | None = None,
        timeout_sec: int = 10,
        proxy_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化带缓存的节假日服务。

        Args:
            logger: 日志记录器
            cache_dir: 日级缓存目录（如 cache/）
            raw_cache_dir: 原始年度数据目录（如 cache/holidays/）
            ghproxy_urls: 代理源 URL 列表
            timeout_sec: 请求超时时间（秒）
            proxy_url: 可选全局代理 URL
            http_client: 可选外部 HTTP 客户端；由调用方管理生命周期
        """
        super().__init__("holidays", cache_dir, logger)
        self._service = HolidayService(
            logger=logger,
            cache_dir=raw_cache_dir,
            ghproxy_urls=ghproxy_urls,
            timeout_sec=timeout_sec,
            proxy_url=proxy_url,
            http_client=http_client,
        )

    def load_cache(self) -> dict[str, Any] | None:
        """读取日缓存，兼容旧版仅保存节假日列表的 payload。"""
        data = super().load_cache()
        if isinstance(data, list):
            return {"holidays": data, "make_up_workdays": []}
        if not isinstance(data, dict) or not isinstance(data.get("holidays"), list):
            return None
        return {
            "holidays": data["holidays"],
            "make_up_workdays": _future_make_up_workdays(
                data.get("make_up_workdays"), self._service._get_today()
            ),
        }

    async def fetch_fresh(self) -> dict[str, Any] | None:
        """从网络获取新鲜数据。

        Returns:
            节假日列表与补班日期，如果获取失败返回 None
        """
        try:
            holidays = await self._service.fetch_holidays()
            if holidays is None:
                return None
            return {
                "holidays": holidays,
                "make_up_workdays": self._service.make_up_workdays,
            }
        except Exception as e:
            self.logger.error("Failed to fetch holidays: %s", safe_exception_for_log(e))
            return None
