import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.services.calendar import get_business_timezone
from app.services.daily_cache import DailyCache

# GitHub 原始源（硬编码，不可配置）
GITHUB_RAW_URL = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"


class HolidayService:
    """节假日数据服务，获取并处理中国法定节假日信息"""

    # 缓存 TTL 常量（秒）
    TTL_CURRENT_YEAR = 7 * 24 * 3600  # 7天
    TTL_NEXT_YEAR = 12 * 3600  # 12小时

    def __init__(
        self,
        logger: logging.Logger,
        cache_dir: str | Path = "state/holidays",
        mirror_urls: list[str] | None = None,
        timeout_sec: int = 10,
    ) -> None:
        self._logger = logger
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._mirror_urls = mirror_urls or []
        self._timeout_sec = timeout_sec

    def _build_urls(self, year: int) -> list[str]:
        """构建 URL 列表：镜像源优先，GitHub 原始源兜底"""
        urls = []
        # 镜像源：前缀 + raw.githubusercontent.com/...
        raw_path = f"raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"
        for mirror in self._mirror_urls:
            # 校验镜像前缀必须包含协议
            if not mirror.startswith(("http://", "https://")):
                self._logger.warning(f"跳过无效镜像前缀（缺少协议）: {mirror}")
                continue
            # 确保镜像地址以 / 结尾
            prefix = mirror.rstrip("/") + "/"
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
            self._logger.debug(f"{year} 年为往年数据，缓存永久有效")
            return True

        # 检查 mtime
        mtime = os.path.getmtime(cache_file)
        age = time.time() - mtime
        # 兜底：系统时间回拨时视为过期
        if age < 0:
            self._logger.warning(f"{year} 年缓存 mtime 异常（系统时间可能回拨），视为过期")
            return False
        if age < ttl:
            self._logger.debug(f"{year} 年缓存有效，已缓存 {age / 3600:.1f} 小时，TTL {ttl / 3600:.1f} 小时")
            return True
        else:
            self._logger.debug(f"{year} 年缓存已过期，已缓存 {age / 3600:.1f} 小时，TTL {ttl / 3600:.1f} 小时")
            return False

    async def fetch_holidays(self) -> list[dict[str, Any]]:
        """获取去年、今年和明年的节假日数据，合并处理后返回"""
        today = self._get_today()
        current_year = today.year
        prev_year = current_year - 1
        next_year = current_year + 1

        self._logger.info(f"开始获取 {prev_year}、{current_year} 和 {next_year} 年节假日数据")

        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            tasks = [
                self._fetch_year_data(client, prev_year),
                self._fetch_year_data(client, current_year),
                self._fetch_year_data(client, next_year),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        return self._merge_and_process(list(results))

    async def _fetch_year_data(self, client: httpx.AsyncClient, year: int) -> dict[str, Any] | None:
        """获取指定年份的节假日数据，支持 TTL 缓存、多源重试和降级"""
        # 1. 检查缓存是否有效
        if self._is_cache_valid(year):
            cached_data = self._load_from_cache(year)
            if cached_data:
                self._logger.debug(f"使用 {year} 年有效缓存数据")
                return cached_data

        # 2. 缓存无效或不存在，尝试从网络获取
        urls = self._build_urls(year)

        for url in urls:
            try:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                self._logger.debug(f"成功从 {url} 获取 {year} 年节假日数据")

                # 成功获取后更新缓存
                self._save_to_cache(year, data)
                return data

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    self._logger.warning(f"数据源 {url} 返回 404")
                    continue
                self._logger.warning(f"数据源 {url} 请求失败: {e}")
            except Exception as e:
                self._logger.warning(f"数据源 {url} 访问异常: {e}")

        # 3. 网络请求全部失败，尝试读取过期缓存（降级策略）
        self._logger.warning(f"所有数据源均无法获取 {year} 年数据，尝试读取本地缓存")
        cached_data = self._load_from_cache(year)
        if cached_data:
            self._logger.info(f"成功读取 {year} 年本地缓存数据")
            return cached_data

        self._logger.error(f"无法获取 {year} 年节假日数据 (网络失败且无缓存)")
        return None

    def _save_to_cache(self, year: int, data: dict[str, Any]) -> None:
        """保存数据到本地缓存"""
        try:
            cache_file = self._cache_dir / f"{year}.json"
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._logger.debug(f"已缓存 {year} 年数据到 {cache_file}")
        except Exception as e:
            self._logger.warning(f"缓存 {year} 年数据失败: {e}")

    def _load_from_cache(self, year: int) -> dict[str, Any] | None:
        """从本地缓存读取数据"""
        try:
            cache_file = self._cache_dir / f"{year}.json"
            if not cache_file.exists():
                return None

            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self._logger.warning(f"读取 {year} 年缓存失败: {e}")
            return None

    def _merge_and_process(self, data_list: list[dict[str, Any] | None | Exception]) -> list[dict[str, Any]]:
        """合并多年数据，过滤休息日，排序后聚合连续假期"""
        all_days: list[dict[str, Any]] = []

        for data in data_list:
            # 过滤异常结果（来自 gather 的 return_exceptions=True）
            if isinstance(data, Exception):
                self._logger.warning(f"跳过异常结果: {data}")
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
        today_workday = next((d for d in workdays if d.get("date") == today_str), None)

        # 按 date 升序排序
        off_days.sort(key=lambda x: x.get("date", ""))

        self._logger.debug(f"合并后共 {len(off_days)} 个休息日")

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
            self._logger.info(f"今天是补班日：{holiday_name}")

        return result

    def _group_continuous_holidays(self, days: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                self._logger.warning(f"跳过无效日期数据: {day}, 错误: {e}")
                continue

            if not current_group:
                current_group = [day]
                continue

            last_day = current_group[-1]
            try:
                last_date = date.fromisoformat(last_day["date"])
                last_name = last_day.get("name", "")
            except (KeyError, ValueError) as e:
                self._logger.warning(f"跳过无效日期数据: {last_day}, 错误: {e}")
                current_group = [day]
                continue

            # 判断连续性：同名 AND 日期连续
            is_continuous = (day_name == last_name) and (day_date == last_date + timedelta(days=1))

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
                self._logger.warning(f"跳过无效假期组: {g}, 错误: {e}")
                continue

        # 计算 days_left
        for group in result:
            try:
                start = date.fromisoformat(group["start_date"])
                group["days_left"] = (start - today).days if start > today else 0
            except (KeyError, ValueError) as e:
                self._logger.warning(f"无法计算 days_left: {group}, 错误: {e}")
                group["days_left"] = 0

        self._logger.info(f"处理完成，共 {len(result)} 个有效假期")

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


class CachedHolidayService(DailyCache[list[dict[str, Any]]]):
    """带日级缓存的节假日服务。

    原始年度数据仍保留在 state/holidays/ 目录，
    聚合后的节假日列表缓存在 state/cache/holidays.json。

    继承 DailyCache，为 HolidayService 提供日级缓存能力。
    缓存在每日零点自动过期，网络获取失败时返回过期缓存。
    """

    def __init__(
        self,
        logger: logging.Logger,
        cache_dir: Path,
        raw_cache_dir: Path,
        mirror_urls: list[str] | None = None,
        timeout_sec: int = 10,
    ) -> None:
        """初始化带缓存的节假日服务。

        Args:
            logger: 日志记录器
            cache_dir: 日级缓存目录（如 state/cache/）
            raw_cache_dir: 原始年度数据目录（如 state/holidays/）
            mirror_urls: 镜像源 URL 列表
            timeout_sec: 请求超时时间（秒）
        """
        super().__init__("holidays", cache_dir, logger)
        self._service = HolidayService(
            logger=logger,
            cache_dir=raw_cache_dir,
            mirror_urls=mirror_urls,
            timeout_sec=timeout_sec,
        )

    async def fetch_fresh(self) -> list[dict[str, Any]] | None:
        """从网络获取新鲜数据。

        Returns:
            聚合后的节假日列表，如果获取失败返回 None
        """
        try:
            return await self._service.fetch_holidays()
        except Exception as e:
            self.logger.error(f"Failed to fetch holidays: {e}")
            return None
