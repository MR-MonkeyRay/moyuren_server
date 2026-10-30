"""基于 APScheduler 的任务调度模块。"""

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
    """基于 APScheduler 的异步任务调度器。"""

    def __init__(
        self,
        config: SchedulerConfig,
        logger: logging.Logger,
        timezone: tzinfo | None = None,
    ) -> None:
        """初始化任务调度器。

        Args:
            config: 调度配置，包含默认执行时间。
            logger: 记录调度事件的日志实例。
            timezone: 调度时区；为 None 时使用业务时区。
        """
        self.config = config
        self.logger = logger
        self.timezone = timezone if timezone is not None else get_business_timezone()

        # 使用业务时区和默认任务配置初始化 AsyncIOScheduler
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
        """添加每日定时任务。

        Args:
            job_id: 任务的唯一标识符。
            func: 要执行的异步函数。
            hour: 小时（0-23）；为 None 时使用 config.daily_times 中首个时间的小时。
            minute: 分钟（0-59）；为 None 时使用 config.daily_times 中首个时间的分钟。
        """
        # 未指定时，从配置中解析默认时间
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

        # 创建每日执行的 cron 触发器
        trigger = CronTrigger(hour=hour, minute=minute, timezone=self.timezone)

        # 将任务添加到调度器
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
        """添加每小时定时任务。

        Args:
            job_id: 任务的唯一标识符。
            func: 要执行的异步函数。
            minute: 每小时的执行分钟（0-59）；为 None 时使用 config.minute_of_hour。
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
        """启动调度器。"""
        if not self.scheduler.running:
            self.scheduler.start()
            self.logger.info("Task scheduler started (%s)", self.timezone)
        else:
            self.logger.warning("Task scheduler is already running")

    def shutdown(self) -> None:
        """优雅停止调度器。"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            self.logger.info("Task scheduler stopped")
        else:
            self.logger.debug("Task scheduler is not running")

    def run_job_now(self, job_id: str) -> None:
        """立即执行已注册的任务。

        创建立即执行的一次性任务，使用已注册任务的同一函数。

        Args:
            job_id: 要执行的任务 ID。
        """
        try:
            job = self.scheduler.get_job(job_id)
            if job is None:
                self.logger.error("Job '%s' not found", job_id)
                return

            # 添加立即执行的一次性任务
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
        """检查调度器是否正在运行。

        Returns:
            调度器正在运行时返回 True，否则返回 False。
        """
        return self.scheduler.running
