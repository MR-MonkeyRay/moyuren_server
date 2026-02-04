"""Tests for app/core/scheduler.py - task scheduler."""

import logging
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.core.config import SchedulerConfig
from app.core.scheduler import TaskScheduler


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

    def test_add_daily_job_with_explicit_time(self, scheduler: TaskScheduler) -> None:
        """Test add daily job with explicit time."""
        mock_func = AsyncMock()

        scheduler.add_daily_job(
            job_id="test_job",
            func=mock_func,
            hour=10,
            minute=30
        )

        # Verify job was added
        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_daily_job_uses_config_default(self, scheduler: TaskScheduler) -> None:
        """Test add daily job uses config default time."""
        mock_func = AsyncMock()

        scheduler.add_daily_job(
            job_id="test_job",
            func=mock_func
        )

        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_daily_job_partial_time(self, scheduler: TaskScheduler) -> None:
        """Test add daily job with partial time (only hour)."""
        mock_func = AsyncMock()

        scheduler.add_daily_job(
            job_id="test_job",
            func=mock_func,
            hour=10
        )

        job = scheduler.scheduler.get_job("test_job")
        assert job is not None

    def test_add_daily_job_replaces_existing(self, scheduler: TaskScheduler) -> None:
        """Test add daily job replaces existing job."""
        mock_func1 = AsyncMock()
        mock_func2 = AsyncMock()

        scheduler.add_daily_job(job_id="test_job", func=mock_func1, hour=10, minute=0)
        scheduler.add_daily_job(job_id="test_job", func=mock_func2, hour=11, minute=0)

        # Should only have one job
        jobs = scheduler.scheduler.get_jobs()
        assert len([j for j in jobs if j.id == "test_job"]) == 1

    def test_start_scheduler(self, scheduler: TaskScheduler) -> None:
        """Test start scheduler."""
        scheduler.start()
        assert scheduler.scheduler.running is True

        # Cleanup
        scheduler.shutdown()

    def test_start_scheduler_already_running(self, scheduler: TaskScheduler) -> None:
        """Test start scheduler when already running."""
        scheduler.start()
        # Should not raise
        scheduler.start()

        assert scheduler.scheduler.running is True

        # Cleanup
        scheduler.shutdown()

    def test_shutdown_scheduler(self, scheduler: TaskScheduler) -> None:
        """Test shutdown scheduler."""
        scheduler.start()
        scheduler.shutdown()

        assert scheduler.scheduler.running is False

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
