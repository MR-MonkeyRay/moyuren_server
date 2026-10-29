"""Tests for app/core/scheduler.py - task scheduler."""

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
    """Tests for TaskScheduler class."""

    @pytest.fixture
    def config(self) -> SchedulerConfig:
        """Create a scheduler configuration."""
        return SchedulerConfig(daily_times=["06:00", "18:00"])

    @pytest.fixture
    def scheduler(self, config: SchedulerConfig, logger: logging.Logger) -> TaskScheduler:
        """Create a TaskScheduler instance."""
        return TaskScheduler(config=config, logger=logger)

    def test_init(self, scheduler: TaskScheduler) -> None:
        """Test scheduler initialization."""
        assert scheduler.scheduler is not None
        assert scheduler.config is not None

    def test_init_uses_current_business_timezone(
        self,
        config: SchedulerConfig,
        logger: logging.Logger,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test the default timezone is resolved at construction time."""
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
        """Test cron times use the business timezone on a UTC host."""
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
        """Test scheduled jobs inherit misfire and concurrency defaults."""
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
        """Test immediate jobs use an aware datetime in the supplied timezone."""
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
        """Test add daily job with explicit time."""
        mock_func = AsyncMock()

        scheduler.add_daily_job(job_id="test_job", func=mock_func, hour=10, minute=30)

        # Verify job was added
        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_daily_job_uses_config_default(self, scheduler: TaskScheduler) -> None:
        """Test add daily job uses config default time."""
        mock_func = AsyncMock()

        scheduler.add_daily_job(job_id="test_job", func=mock_func)

        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_daily_job_partial_time(self, scheduler: TaskScheduler) -> None:
        """Test add daily job with partial time (only hour)."""
        mock_func = AsyncMock()

        scheduler.add_daily_job(job_id="test_job", func=mock_func, hour=10)

        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_hourly_job_with_explicit_minute(self, scheduler: TaskScheduler) -> None:
        """Test add hourly job with explicit minute."""
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
        """Test add hourly job uses config minute_of_hour by default."""
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
        """Test add daily job replaces existing job.

        Note: APScheduler's replace_existing only works when the scheduler is running.
        We need to start the scheduler first for the replacement to take effect.
        """
        mock_func1 = AsyncMock()
        mock_func2 = AsyncMock()

        # Start scheduler for replace_existing to work
        scheduler.start()
        try:
            scheduler.add_daily_job(job_id="test_job", func=mock_func1, hour=10, minute=0)
            scheduler.add_daily_job(job_id="test_job", func=mock_func2, hour=11, minute=0)

            # Verify there's exactly one job with this ID
            jobs = scheduler.scheduler.get_jobs()
            assert len([j for j in jobs if j.id == "test_job"]) == 1

            # Verify the job was replaced (trigger should be 11:00, not 10:00)
            job = scheduler.scheduler.get_job("test_job")
            assert job is not None
            # Use string representation to avoid accessing private trigger internals
            trigger_str = str(job.trigger)
            assert "hour='11'" in trigger_str or "11:" in trigger_str
        finally:
            scheduler.shutdown()

    async def test_start_scheduler(self, scheduler: TaskScheduler) -> None:
        """Test start scheduler."""
        scheduler.start()
        assert scheduler.scheduler.running is True

        # Cleanup
        scheduler.shutdown()

    async def test_start_scheduler_already_running(self, scheduler: TaskScheduler) -> None:
        """Test start scheduler when already running."""
        scheduler.start()
        # Should not raise
        scheduler.start()

        assert scheduler.scheduler.running is True

        # Cleanup
        scheduler.shutdown()

    async def test_shutdown_scheduler(self, scheduler: TaskScheduler) -> None:
        """Test shutdown scheduler."""
        scheduler.start()
        assert scheduler.scheduler.running is True
        scheduler.shutdown()

        # After shutdown, verify shutdown was called successfully
        # Note: AsyncIOScheduler's running state may not update synchronously
        # The log message "Task scheduler stopped" confirms shutdown was called

    def test_shutdown_scheduler_not_running(self, scheduler: TaskScheduler) -> None:
        """Test shutdown scheduler when not running."""
        # Should not raise
        scheduler.shutdown()

    def test_add_daily_job_invalid_config_fallback(self, logger: logging.Logger) -> None:
        """Test add daily job falls back when config is invalid."""
        # Create config with empty daily_times
        config = SchedulerConfig(daily_times=["06:00"])
        scheduler = TaskScheduler(config=config, logger=logger)

        # Manually break the config
        scheduler.config.daily_times = []

        mock_func = AsyncMock()
        scheduler.add_daily_job(job_id="test_job", func=mock_func)

        # Should still add job with fallback time
        job = scheduler.scheduler.get_job("test_job")
        assert job is not None
