"""Task scheduler module using APScheduler."""

import logging
from collections.abc import Callable
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.core.config import SchedulerConfig


class TaskScheduler:
    """Async task scheduler using APScheduler."""

    def __init__(self, config: SchedulerConfig, logger: logging.Logger) -> None:
        """Initialize the task scheduler.

        Args:
            config: Scheduler configuration including default times.
            logger: Logger instance for logging scheduler events.
        """
        self.config = config
        self.logger = logger

        # Initialize AsyncIOScheduler with local timezone
        self.scheduler = AsyncIOScheduler()

    def add_daily_job(
        self,
        job_id: str,
        func: Callable,
        hour: int | None = None,
        minute: int | None = None,
    ) -> None:
        """Add a daily scheduled job.

        Args:
            job_id: Unique identifier for the job.
            func: Async function to execute.
            hour: Hour of day (0-23). If None, uses first time from config.daily_times.
            minute: Minute of hour (0-59). If None, uses first time from config.daily_times.
        """
        # Parse default time from config if not specified
        if hour is None or minute is None:
            try:
                default_time = self.config.daily_times[0] if self.config.daily_times else "06:00"
                default_hour, default_minute = map(int, default_time.split(":"))
                hour = hour if hour is not None else default_hour
                minute = minute if minute is not None else default_minute
            except (ValueError, AttributeError, IndexError) as e:
                self.logger.warning(f"Invalid daily_times config, using 06:00: {e}")
                hour = hour if hour is not None else 6
                minute = minute if minute is not None else 0

        # Create cron trigger for daily execution
        trigger = CronTrigger(hour=hour, minute=minute)

        # Add job to scheduler
        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            name=job_id,
            replace_existing=True,
        )

        self.logger.info(f"Added daily job '{job_id}' at {hour:02d}:{minute:02d}")

    def add_hourly_job(
        self,
        job_id: str,
        func: Callable,
        minute: int | None = None,
    ) -> None:
        """Add an hourly scheduled job.

        Args:
            job_id: Unique identifier for the job.
            func: Async function to execute.
            minute: Minute of hour (0-59). If None, uses config.minute_of_hour.
        """
        if minute is None:
            minute = self.config.minute_of_hour

        trigger = CronTrigger(minute=minute)

        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            name=job_id,
            replace_existing=True,
        )

        self.logger.info(f"Added hourly job '{job_id}' at minute {minute:02d}")

    def start(self) -> None:
        """Start the scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            self.logger.info("Task scheduler started")
        else:
            self.logger.warning("Task scheduler is already running")

    def shutdown(self) -> None:
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            self.logger.info("Task scheduler stopped")
        else:
            self.logger.debug("Task scheduler is not running")

    def run_job_now(self, job_id: str) -> None:
        """Execute a registered job immediately.

        Creates a one-time job that runs immediately with the same function
        as the registered job.

        Args:
            job_id: The ID of the job to run.
        """
        try:
            job = self.scheduler.get_job(job_id)
            if job is None:
                self.logger.error(f"Job '{job_id}' not found")
                return

            # Add a one-time job that runs immediately
            self.scheduler.add_job(
                job.func,
                trigger=DateTrigger(run_date=datetime.now()),
                id=f"{job_id}_immediate",
                name=f"{job_id}_immediate",
                replace_existing=True,
            )
            self.logger.info(f"Triggered immediate execution of job '{job_id}'")
        except Exception as e:
            self.logger.error(f"Failed to run job '{job_id}': {e}")

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is running.

        Returns:
            True if scheduler is running, False otherwise.
        """
        return self.scheduler.running
