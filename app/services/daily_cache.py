"""日级缓存抽象基类模块。

提供基于日期的缓存机制，支持自动过期和降级策略。
"""

import json
import logging
import os
import tempfile
import time
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Callable, Generic, TypeVar

from app.services.calendar import today_business

T = TypeVar("T")


class DailyCache(ABC, Generic[T]):
    """日级缓存抽象基类。

    提供基于日期的缓存机制，缓存在每日零点自动过期。
    子类需实现 fetch_fresh() 方法来获取新鲜数据。

    缓存文件格式:
    {
        "date": "2026-02-05",
        "data": { ... },
        "fetched_at": 1738713600000
    }

    Attributes:
        namespace: 缓存命名空间（如 "news", "fun_content", "kfc"）
        cache_dir: 缓存目录路径
        logger: 日志记录器
    """

    def __init__(
        self,
        namespace: str,
        cache_dir: Path,
        logger: logging.Logger,
        date_provider: Callable[[], date] | None = None,
    ) -> None:
        """初始化日级缓存。

        Args:
            namespace: 缓存命名空间
            cache_dir: 缓存目录路径
            logger: 日志记录器
            date_provider: 日期提供函数，默认使用 today_business
        """
        self.namespace = namespace
        self.cache_dir = cache_dir
        self.logger = logger
        self._date_provider = date_provider

        # 确保缓存目录存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_file(self) -> Path:
        """获取缓存文件路径。

        Returns:
            缓存文件路径
        """
        return self.cache_dir / f"{self.namespace}.json"

    def cache_key(self) -> str:
        """获取缓存键（子类可覆盖）。

        默认使用 date_provider 提供的日期作为缓存边界。

        Returns:
            缓存键字符串（默认：YYYY-MM-DD）
        """
        provider = self._date_provider or today_business
        return provider().isoformat()

    def is_cache_valid(self) -> bool:
        """检查缓存是否有效（未过期）。

        Returns:
            True 如果缓存存在且日期为今天，否则 False
        """
        cache_file = self._get_cache_file()
        if not cache_file.exists():
            return False

        try:
            with cache_file.open("r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # 校验缓存数据必须是 dict
            if not isinstance(cache_data, dict):
                self.logger.warning(
                    "Invalid cache format for %s: expected dict, got %s",
                    self.namespace,
                    type(cache_data).__name__,
                )
                return False

            cache_date = cache_data.get("date")
            today = self.cache_key()

            return cache_date == today
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(
                "Failed to validate cache for %s: %s",
                self.namespace,
                e,
            )
            return False

    def load_cache(self) -> T | None:
        """从缓存文件加载数据。

        Returns:
            缓存的数据，如果加载失败返回 None
        """
        cache_file = self._get_cache_file()
        if not cache_file.exists():
            return None

        try:
            with cache_file.open("r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # 校验缓存数据必须是 dict
            if not isinstance(cache_data, dict):
                self.logger.warning(
                    "Invalid cache format for %s: expected dict, got %s",
                    self.namespace,
                    type(cache_data).__name__,
                )
                return None

            return cache_data.get("data")
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(
                "Failed to load cache for %s: %s",
                self.namespace,
                e,
            )
            return None

    def save_cache(self, data: T) -> None:
        """保存数据到缓存文件（原子写入）。

        使用临时文件 + rename 实现原子写入，防止并发写入冲突。

        Args:
            data: 要缓存的数据
        """
        cache_file = self._get_cache_file()
        tmp_path: str | None = None

        cache_data = {
            "date": self.cache_key(),
            "data": data,
            "fetched_at": int(time.time() * 1000),  # 毫秒时间戳
        }

        try:
            # 使用临时文件实现原子写入
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.cache_dir,
                delete=False,
                suffix=".tmp",
            ) as tmp_file:
                tmp_path = tmp_file.name
                json.dump(cache_data, tmp_file, ensure_ascii=False, indent=2)

            # 原子性地替换目标文件
            os.replace(tmp_path, cache_file)

            self.logger.info(
                "Saved cache for %s (date: %s)",
                self.namespace,
                cache_data["date"],
            )
        except (OSError, TypeError, ValueError) as e:
            self.logger.error(
                "Failed to save cache for %s: %s",
                self.namespace,
                e,
            )
            # 清理临时文件
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @abstractmethod
    async def fetch_fresh(self) -> T | None:
        """从网络获取新鲜数据。

        子类必须实现此方法。

        Returns:
            获取的数据，如果获取失败返回 None
        """
        pass

    async def get(self, force_refresh: bool = False) -> T | None:
        """获取数据（主入口方法）。

        逻辑:
        1. 如果 force_refresh=False 且缓存有效，返回缓存数据
        2. 否则调用 fetch_fresh() 获取新数据
        3. 如果获取成功，保存到缓存并返回
        4. 如果获取失败，尝试返回过期缓存（降级策略）
        5. 都失败返回 None

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            数据，如果获取失败返回 None
        """
        # 1. 检查缓存
        if not force_refresh and self.is_cache_valid():
            cached_data = self.load_cache()
            if cached_data is not None:
                self.logger.debug(
                    "Using valid cache for %s",
                    self.namespace,
                )
                return cached_data

        # 2. 获取新鲜数据
        self.logger.info(
            "Fetching fresh data for %s",
            self.namespace,
        )
        fresh_data: T | None = None
        try:
            fresh_data = await self.fetch_fresh()
        except Exception as e:
            self.logger.exception(
                "Exception while fetching fresh data for %s: %s",
                self.namespace,
                e,
            )

        # 3. 保存并返回新数据
        if fresh_data is not None:
            self.save_cache(fresh_data)
            return fresh_data

        # 4. 降级策略：返回过期缓存
        self.logger.warning(
            "Failed to fetch fresh data for %s, trying stale cache",
            self.namespace,
        )
        stale_data = self.load_cache()
        if stale_data is not None:
            self.logger.info(
                "Using stale cache for %s as fallback",
                self.namespace,
            )
            return stale_data

        # 5. 都失败
        self.logger.error(
            "No data available for %s (fresh fetch failed and no cache)",
            self.namespace,
        )
        return None
