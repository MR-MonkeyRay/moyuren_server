import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

SHANGHAI_TZ = timezone(timedelta(hours=8))
# GitHub 原始源（硬编码，不可配置）
GITHUB_RAW_URL = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"


class HolidayService:
    """节假日数据服务，获取并处理中国法定节假日信息"""

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
        """获取上海时区的今天日期"""
        return datetime.now(SHANGHAI_TZ).date()

    async def fetch_holidays(self) -> list[dict[str, Any]]:
        """获取去年、今年和明年的节假日数据，合并处理后返回"""
        today = self._get_today()
        current_year = today.year
        prev_year = current_year - 1
        next_year = current_year + 1

        self._logger.info(
            f"开始获取 {prev_year}、{current_year} 和 {next_year} 年节假日数据"
        )

        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            tasks = [
                self._fetch_year_data(client, prev_year),
                self._fetch_year_data(client, current_year),
                self._fetch_year_data(client, next_year),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        return self._merge_and_process(list(results))

    async def _fetch_year_data(
        self, client: httpx.AsyncClient, year: int
    ) -> dict[str, Any] | None:
        """获取指定年份的节假日数据，支持多源重试和本地缓存"""
        # 构建 URL 列表：镜像源优先，GitHub 原始源兜底
        urls = self._build_urls(year)

        # 1. 尝试从网络获取
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

        # 2. 网络请求全部失败，尝试读取缓存
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
            
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self._logger.warning(f"读取 {year} 年缓存失败: {e}")
            return None

    def _merge_and_process(
        self, data_list: list[dict[str, Any] | None | Exception]
    ) -> list[dict[str, Any]]:
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

        # 按 date 升序排序
        off_days.sort(key=lambda x: x.get("date", ""))

        self._logger.debug(f"合并后共 {len(off_days)} 个休息日")

        return self._group_continuous_holidays(off_days)

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
        }
