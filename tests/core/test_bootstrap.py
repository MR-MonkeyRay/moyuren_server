"""Tests for application bootstrap helpers."""

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

import app.core.bootstrap as bootstrap_module
from app.core.bootstrap import build_services, register_scheduled_jobs
from app.core.config import AppConfig
from app.core.scheduler import TaskScheduler


def test_register_scheduled_jobs_registers_each_daily_time() -> None:
    """Daily scheduling registers one stable job ID per configured time."""
    scheduler = SimpleNamespace(add_daily_job=MagicMock(), add_hourly_job=MagicMock())
    config = SimpleNamespace(
        scheduler=SimpleNamespace(
            mode="daily", daily_times=["06:15", "18:45"], minute_of_hour=0
        )
    )

    async def job() -> None:
        return None

    register_scheduled_jobs(
        cast(TaskScheduler, scheduler),
        cast(AppConfig, config),
        job,
        logging.getLogger(__name__),
    )

    assert scheduler.add_daily_job.call_count == 2
    scheduler.add_daily_job.assert_any_call(
        job_id="generate_moyuren_image_0", func=job, hour=6, minute=15
    )
    scheduler.add_daily_job.assert_any_call(
        job_id="generate_moyuren_image_1", func=job, hour=18, minute=45
    )
    scheduler.add_hourly_job.assert_not_called()


def test_register_scheduled_jobs_skips_invalid_daily_time() -> None:
    """Malformed daily times do not prevent valid jobs from registering."""
    scheduler = SimpleNamespace(add_daily_job=MagicMock(), add_hourly_job=MagicMock())
    config = SimpleNamespace(
        scheduler=SimpleNamespace(
            mode="daily", daily_times=["invalid", "18:45"], minute_of_hour=0
        )
    )

    async def job() -> None:
        return None

    register_scheduled_jobs(
        cast(TaskScheduler, scheduler),
        cast(AppConfig, config),
        job,
        logging.getLogger(__name__),
    )

    scheduler.add_daily_job.assert_called_once_with(
        job_id="generate_moyuren_image_1", func=job, hour=18, minute=45
    )


def test_register_scheduled_jobs_registers_hourly_job() -> None:
    """Hourly scheduling preserves the configured minute and stable job ID."""
    scheduler = SimpleNamespace(add_daily_job=MagicMock(), add_hourly_job=MagicMock())
    config = SimpleNamespace(
        scheduler=SimpleNamespace(
            mode="hourly", daily_times=["06:00"], minute_of_hour=23
        )
    )

    async def job() -> None:
        return None

    register_scheduled_jobs(
        cast(TaskScheduler, scheduler),
        cast(AppConfig, config),
        job,
        logging.getLogger(__name__),
    )

    scheduler.add_hourly_job.assert_called_once_with(
        job_id="generate_moyuren_image", func=job, minute=23
    )
    scheduler.add_daily_job.assert_not_called()


def test_build_services_leaves_absent_optional_sources_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing optional sources produce a container with disabled services."""
    config = SimpleNamespace(
        paths=SimpleNamespace(cache_dir=str(tmp_path)),
        network=SimpleNamespace(proxy_url=None, ghproxy_urls=[]),
        instances=SimpleNamespace(),
        templates=SimpleNamespace(config=SimpleNamespace()),
        cache=SimpleNamespace(retain_days=30, max_images_per_template_per_day=3),
        get_source=lambda source_type: None,
        get_templates_config=lambda: SimpleNamespace(items=[]),
    )
    holiday_service = MagicMock()
    monkeypatch.setattr(
        bootstrap_module, "CachedHolidayService", lambda **kwargs: holiday_service
    )
    monkeypatch.setattr(bootstrap_module, "ImageRenderer", lambda **kwargs: MagicMock())
    monkeypatch.setattr(bootstrap_module, "CacheCleaner", lambda **kwargs: MagicMock())
    monkeypatch.setattr(bootstrap_module, "ImageGenerator", lambda: MagicMock())

    services = build_services(
        cast(AppConfig, config), logging.getLogger(__name__), MagicMock()
    )

    assert services.holiday_service is holiday_service
    assert services.data_fetcher is None
    assert services.fun_content_service is None
    assert services.kfc_service is None
    assert services.stock_index_service is None
    assert services.gold_price_service is None
    assert services.daily_english_service is None


def test_build_services_leaves_disabled_daily_english_unconstructed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A disabled daily English source does not build its backend or service."""
    disabled_source = SimpleNamespace(enabled=False, backend=SimpleNamespace())

    def get_source(source_type: type[object]) -> object | None:
        if source_type is bootstrap_module.DailyEnglishSource:
            return disabled_source
        return None

    config = SimpleNamespace(
        paths=SimpleNamespace(cache_dir=str(tmp_path)),
        network=SimpleNamespace(proxy_url=None, ghproxy_urls=[]),
        instances=SimpleNamespace(),
        templates=SimpleNamespace(config=SimpleNamespace()),
        cache=SimpleNamespace(retain_days=30, max_images_per_template_per_day=3),
        get_source=get_source,
        get_templates_config=lambda: SimpleNamespace(items=[]),
    )
    build_backend = MagicMock()
    build_daily_service = MagicMock()
    monkeypatch.setattr(
        bootstrap_module, "CachedHolidayService", lambda **kwargs: MagicMock()
    )
    monkeypatch.setattr(bootstrap_module, "ImageRenderer", lambda **kwargs: MagicMock())
    monkeypatch.setattr(bootstrap_module, "CacheCleaner", lambda **kwargs: MagicMock())
    monkeypatch.setattr(bootstrap_module, "ImageGenerator", lambda: MagicMock())
    monkeypatch.setattr(bootstrap_module, "build_dict_backend", build_backend)
    monkeypatch.setattr(
        bootstrap_module, "CachedDailyEnglishService", build_daily_service
    )

    services = build_services(
        cast(AppConfig, config), logging.getLogger(__name__), MagicMock()
    )

    assert services.daily_english_service is None
    build_backend.assert_not_called()
    build_daily_service.assert_not_called()
