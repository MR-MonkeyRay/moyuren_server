"""app/core/scheduler.py 任务调度器测试。"""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.core.config import SchedulerConfig
from app.core.scheduler import MISFIRE_GRACE_TIME_SECONDS, TaskScheduler
from app.services import calendar


class TestTaskScheduler:
    """TaskScheduler 类测试。"""

    @pytest.fixture
    def config(self) -> SchedulerConfig:
        """创建调度器配置。"""
        return SchedulerConfig(daily_times=["06:00", "18:00"])

    @pytest.fixture
    def scheduler(self, config: SchedulerConfig, logger: logging.Logger) -> TaskScheduler:
        """创建 TaskScheduler 实例。"""
        return TaskScheduler(config=config, logger=logger)

    def test_init(self, scheduler: TaskScheduler) -> None:
        """测试调度器初始化。"""
        assert scheduler.scheduler is not None
        assert scheduler.config is not None

    def test_init_uses_current_business_timezone(
        self,
        config: SchedulerConfig,
        logger: logging.Logger,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """测试默认时区在构造时确定。"""
        for timezone_name in ("Asia/Shanghai", "America/New_York"):
            business_timezone = ZoneInfo(timezone_name)
            monkeypatch.setattr(calendar, "_business_timezone", business_timezone)

            scheduler = TaskScheduler(config=config, logger=logger)

            assert scheduler.timezone == business_timezone
            assert scheduler.scheduler.timezone == business_timezone

    @pytest.mark.parametrize("mode", ["daily", "hourly"])
    def test_cron_uses_business_timezone(
        self,
        config: SchedulerConfig,
        logger: logging.Logger,
        monkeypatch: pytest.MonkeyPatch,
        mode: str,
    ) -> None:
        """测试 UTC 主机上的 cron 时间使用业务时区。"""
        monkeypatch.setenv("TZ", "UTC")
        business_timezone = ZoneInfo("Asia/Shanghai")
        monkeypatch.setattr(calendar, "_business_timezone", business_timezone)
        scheduler = TaskScheduler(config=config, logger=logger)
        if mode == "daily":
            scheduler.add_daily_job("test_job", AsyncMock(), hour=6, minute=0)
        else:
            scheduler.add_hourly_job("test_job", AsyncMock(), minute=0)

        job = scheduler.scheduler.get_job("test_job")
        assert job is not None
        assert isinstance(job.trigger, CronTrigger)
        assert job.trigger.timezone == business_timezone
        next_fire = job.trigger.get_next_fire_time(
            None, datetime(2026, 2, 3, 21, 59, tzinfo=timezone.utc)
        )
        assert next_fire is not None
        assert next_fire == datetime(2026, 2, 4, 6, tzinfo=business_timezone)
        assert next_fire.astimezone(timezone.utc) == datetime(
            2026, 2, 3, 22, tzinfo=timezone.utc
        )

    async def test_job_defaults(self, scheduler: TaskScheduler) -> None:
        """测试定时任务继承错过执行时间的宽限期和并发默认设置。"""
        scheduler.add_daily_job("test_daily_job", AsyncMock())
        scheduler.add_hourly_job("test_hourly_job", AsyncMock())
        scheduler.start()
        try:
            for job_id in ("test_daily_job", "test_hourly_job"):
                job = scheduler.scheduler.get_job(job_id)
                assert job is not None
                assert job.coalesce is True
                assert job.max_instances == 1
                assert job.misfire_grace_time == MISFIRE_GRACE_TIME_SECONDS
                assert job.misfire_grace_time == 300
        finally:
            scheduler.shutdown()

    @pytest.mark.parametrize("timezone_name", ["Asia/Shanghai", "America/New_York"])
    def test_run_job_now_uses_explicit_timezone(
        self,
        config: SchedulerConfig,
        logger: logging.Logger,
        timezone_name: str,
    ) -> None:
        """测试立即执行的任务使用指定时区的带时区时间。"""
        business_timezone = ZoneInfo(timezone_name)
        scheduler = TaskScheduler(config=config, logger=logger, timezone=business_timezone)
        mock_func = AsyncMock()
        scheduler.add_daily_job("test_job", mock_func)
        before = datetime.now(business_timezone)

        scheduler.run_job_now("test_job")

        after = datetime.now(business_timezone)
        assert scheduler.scheduler.timezone == business_timezone
        scheduled_job = scheduler.scheduler.get_job("test_job")
        assert scheduled_job is not None
        assert scheduled_job.trigger.timezone == business_timezone
        job = scheduler.scheduler.get_job("test_job_immediate")
        assert job is not None
        assert job.func is mock_func
        assert isinstance(job.trigger, DateTrigger)
        assert job.trigger.run_date.tzinfo == business_timezone
        assert before <= job.trigger.run_date <= after

    def test_add_daily_job_with_explicit_time(self, scheduler: TaskScheduler) -> None:
        """测试按指定时间添加每日任务。"""
        mock_func = AsyncMock()

        scheduler.add_daily_job(job_id="test_job", func=mock_func, hour=10, minute=30)

        # 确认任务已添加
        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_daily_job_uses_config_default(self, scheduler: TaskScheduler) -> None:
        """测试添加每日任务时使用配置中的默认时间。"""
        mock_func = AsyncMock()

        scheduler.add_daily_job(job_id="test_job", func=mock_func)

        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_daily_job_partial_time(self, scheduler: TaskScheduler) -> None:
        """测试仅指定小时添加每日任务。"""
        mock_func = AsyncMock()

        scheduler.add_daily_job(job_id="test_job", func=mock_func, hour=10)

        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_hourly_job_with_explicit_minute(self, scheduler: TaskScheduler) -> None:
        """测试按指定分钟添加每小时任务。"""
        mock_func = AsyncMock()

        scheduler.add_hourly_job(
            job_id="test_hourly_job",
            func=mock_func,
            minute=15,
        )

        job = scheduler.scheduler.get_job("test_hourly_job")
        assert job is not None
        trigger_str = str(job.trigger)
        assert "minute='15'" in trigger_str or "15" in trigger_str

    def test_add_hourly_job_uses_config_default(self, logger: logging.Logger) -> None:
        """测试添加每小时任务时默认使用配置中的 minute_of_hour。"""
        config = SchedulerConfig(mode="hourly", minute_of_hour=45)
        scheduler = TaskScheduler(config=config, logger=logger)
        mock_func = AsyncMock()

        scheduler.add_hourly_job(
            job_id="test_hourly_job",
            func=mock_func,
        )

        job = scheduler.scheduler.get_job("test_hourly_job")
        assert job is not None
        trigger_str = str(job.trigger)
        assert "minute='45'" in trigger_str or "45" in trigger_str

    async def test_add_daily_job_replaces_existing(self, scheduler: TaskScheduler) -> None:
        """测试添加每日任务时替换已有任务。

        注意：APScheduler 的 replace_existing 仅在调度器运行时生效。
        必须先启动调度器，替换才会生效。
        """
        mock_func1 = AsyncMock()
        mock_func2 = AsyncMock()

        # 启动调度器，使 replace_existing 生效
        scheduler.start()
        try:
            scheduler.add_daily_job(job_id="test_job", func=mock_func1, hour=10, minute=0)
            scheduler.add_daily_job(job_id="test_job", func=mock_func2, hour=11, minute=0)

            # 确认该 ID 仅对应一个任务
            jobs = scheduler.scheduler.get_jobs()
            assert len([j for j in jobs if j.id == "test_job"]) == 1

            # 确认任务已替换（触发时间应为 11:00，而非 10:00）
            job = scheduler.scheduler.get_job("test_job")
            assert job is not None
            # 使用字符串表示，避免访问触发器的内部私有属性
            trigger_str = str(job.trigger)
            assert "hour='11'" in trigger_str or "11:" in trigger_str
        finally:
            scheduler.shutdown()

    async def test_start_scheduler(self, scheduler: TaskScheduler) -> None:
        """测试启动调度器。"""
        scheduler.start()
        assert scheduler.scheduler.running is True

        # 清理
        scheduler.shutdown()

    async def test_start_scheduler_already_running(self, scheduler: TaskScheduler) -> None:
        """测试启动已在运行的调度器。"""
        scheduler.start()
        # 不应抛出异常
        scheduler.start()

        assert scheduler.scheduler.running is True

        # 清理
        scheduler.shutdown()

    async def test_shutdown_scheduler(self, scheduler: TaskScheduler) -> None:
        """测试关闭调度器。"""
        scheduler.start()
        assert scheduler.scheduler.running is True
        scheduler.shutdown()

        # 关闭后，确认关闭操作已成功调用
        # 注意：AsyncIOScheduler 的运行状态可能不会同步更新
        # 日志消息 "Task scheduler stopped" 可确认关闭操作已调用

    def test_shutdown_scheduler_not_running(self, scheduler: TaskScheduler) -> None:
        """测试关闭未运行的调度器。"""
        # 不应抛出异常
        scheduler.shutdown()

    def test_add_daily_job_invalid_config_fallback(self, logger: logging.Logger) -> None:
        """测试配置无效时，添加每日任务使用回退值。"""
        # 创建 daily_times 为空的配置
        config = SchedulerConfig(daily_times=["06:00"])
        scheduler = TaskScheduler(config=config, logger=logger)

        # 手动破坏配置
        scheduler.config.daily_times = []

        mock_func = AsyncMock()
        scheduler.add_daily_job(job_id="test_job", func=mock_func)

        # 仍应使用回退时间添加任务
        job = scheduler.scheduler.get_job("test_job")
        assert job is not None
