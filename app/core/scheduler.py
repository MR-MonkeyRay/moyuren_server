"""Task scheduler module using APScheduler."""

import logging
from collections.abc import Callable
from datetime import datetime, tzinfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.core.config import SchedulerConfig
from app.services.calendar import get_business_timezone

MISFIRE_GRACE_TIME_SECONDS = 300


class TaskScheduler:
    """Async task scheduler using APScheduler."""

    def __init__(
        self,
        config: SchedulerConfig,
        logger: logging.Logger,
        timezone: tzinfo | None = None,
    ) -> None:
        """Initialize the task scheduler.

        Args:
            config: Scheduler configuration including default times.
            logger: Logger instance for logging scheduler events.
            timezone: Scheduling timezone. If None, uses the business timezone.
        """
        self.config = config
        self.logger = logger
        self.timezone = timezone if timezone is not None else get_business_timezone()

        # Initialize AsyncIOScheduler with business timezone and job defaults
        self.scheduler = AsyncIOScheduler(
            timezone=self.timezone,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": MISFIRE_GRACE_TIME_SECONDS,
            },
        )

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
                self.logger.warning("Invalid daily_times config, using 06:00: %s", e)
                hour = hour if hour is not None else 6
                minute = minute if minute is not None else 0

        # Create cron trigger for daily execution
        trigger = CronTrigger(hour=hour, minute=minute, timezone=self.timezone)

        # Add job to scheduler
        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            name=job_id,
            replace_existing=True,
        )

        self.logger.info(
            "Added daily job '%s' at %02d:%02d (%s)", job_id, hour, minute, self.timezone
        )

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

        trigger = CronTrigger(minute=minute, timezone=self.timezone)

        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            name=job_id,
            replace_existing=True,
        )

        self.logger.info(
            "Added hourly job '%s' at minute %02d (%s)", job_id, minute, self.timezone
        )

    def start(self) -> None:
        """Start the scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            self.logger.info("Task scheduler started (%s)", self.timezone)
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
                self.logger.error("Job '%s' not found", job_id)
                return

            # Add a one-time job that runs immediately
            self.scheduler.add_job(
                job.func,
                trigger=DateTrigger(run_date=datetime.now(self.timezone)),
                id=f"{job_id}_immediate",
                name=f"{job_id}_immediate",
                replace_existing=True,
            )
            self.logger.info("Triggered immediate execution of job '%s'", job_id)
        except Exception as e:
            self.logger.error("Failed to run job '%s': %s", job_id, e)

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is running.

        Returns:
            True if scheduler is running, False otherwise.
        """
        return self.scheduler.running
