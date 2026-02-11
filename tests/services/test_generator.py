import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.filelock import FileLockTimeout
from app.services.generator import (
    GenerationBusyError,
    _get_async_lock,
    _read_latest_filename,
    generate_and_save_image,
)


class TestGenerationBusyError:
    """Tests for GenerationBusyError exception."""

    def test_is_exception(self) -> None:
        """Test GenerationBusyError is an Exception."""
        error = GenerationBusyError("busy")
        assert isinstance(error, Exception)

    def test_can_be_raised(self) -> None:
        """Test GenerationBusyError can be raised."""
        with pytest.raises(GenerationBusyError):
            raise GenerationBusyError("Generation in progress")


class TestGetAsyncLock:
    """Tests for _get_async_lock function."""

    def test_returns_lock(self) -> None:
        """Test returns asyncio.Lock."""
        import asyncio

        lock = _get_async_lock()
        assert isinstance(lock, asyncio.Lock)

    def test_returns_same_lock(self) -> None:
        """Test returns same lock instance (singleton)."""
        lock1 = _get_async_lock()
        lock2 = _get_async_lock()
        assert lock1 is lock2


class TestReadLatestFilename:
    """Tests for _read_latest_filename function."""

    def test_reads_v1_state(self, tmp_path: Path) -> None:
        """Test reads filename from data file with images mapping."""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "images": {"moyuren": "moyuren_20260204.jpg"},
            "date": "2026-02-04",
        }
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file)

        assert result == "moyuren_20260204.jpg"

    def test_reads_v2_state(self, tmp_path: Path) -> None:
        """Test reads filename from data file with multiple templates."""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "images": {
                "moyuren": "moyuren_20260204.jpg",
            },
            "date": "2026-02-04",
        }
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file)

        assert result == "moyuren_20260204.jpg"

    def test_reads_v2_state_with_template_name(self, tmp_path: Path) -> None:
        """Test reads filename from data file with specific template name."""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "images": {
                "moyuren": "moyuren_20260204.jpg",
                "custom": "custom_20260204.jpg",
            },
        }
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file, template_name="custom")

        assert result == "custom_20260204.jpg"

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """Test returns None for missing file."""
        data_file = tmp_path / "nonexistent.json"

        result = _read_latest_filename(data_file)

        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        """Test returns None for invalid JSON."""
        data_file = tmp_path / "latest.json"
        data_file.write_text("not valid json")

        result = _read_latest_filename(data_file)

        assert result is None

    def test_returns_none_for_non_dict(self, tmp_path: Path) -> None:
        """Test returns None for non-dict JSON."""
        data_file = tmp_path / "latest.json"
        data_file.write_text(json.dumps(["array", "not", "dict"]))

        result = _read_latest_filename(data_file)

        assert result is None

    def test_returns_none_for_missing_template(self, tmp_path: Path) -> None:
        """Test returns None for missing template in images mapping."""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "images": {
                "moyuren": "moyuren_20260204.jpg",
            },
        }
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file, template_name="nonexistent")

        assert result is None

    def test_handles_missing_images_key(self, tmp_path: Path) -> None:
        """Test handles missing images key gracefully."""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "date": "2026-02-04",
        }
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file)

        assert result is None


class TestGenerationBusyErrorExceptionChain:
    """Tests for exception chaining in generate_and_save_image."""

    @staticmethod
    def _build_app(tmp_path: Path) -> MagicMock:
        """Build a minimal mock FastAPI app for generate_and_save_image."""
        app = MagicMock()
        app.state.logger = MagicMock()

        data_file = tmp_path / "state" / "latest.json"
        data_file.parent.mkdir(parents=True, exist_ok=True)

        template_item = MagicMock()
        template_item.name = "moyuren"
        templates_config = MagicMock()
        templates_config.get_template.return_value = template_item

        config = MagicMock()
        config.paths.cache_dir = str(tmp_path / "cache")
        config.paths.data_file = str(data_file)
        config.get_templates_config.return_value = templates_config
        app.state.config = config

        return app

    @pytest.mark.asyncio
    async def test_busy_error_preserves_async_timeout_cause(self, tmp_path: Path) -> None:
        """Test generate_and_save_image preserves asyncio.TimeoutError as __cause__."""
        app = self._build_app(tmp_path)

        # Make async_lock.acquire() hang so wait_for raises TimeoutError
        never_done: asyncio.Future[bool] = asyncio.Future()

        mock_lock = MagicMock()
        mock_lock.acquire = MagicMock(return_value=never_done)

        with (
            patch("app.services.generator._get_async_lock", return_value=mock_lock),
            pytest.raises(GenerationBusyError) as exc_info,
        ):
            await generate_and_save_image(app)

        assert isinstance(exc_info.value.__cause__, asyncio.TimeoutError)

    @pytest.mark.asyncio
    async def test_busy_error_preserves_filelock_timeout_cause(self, tmp_path: Path) -> None:
        """Test generate_and_save_image preserves FileLockTimeout as __cause__."""
        app = self._build_app(tmp_path)

        @asynccontextmanager
        async def _raise_timeout(*args, **kwargs):
            raise FileLockTimeout("lock")
            yield  # noqa: unreachable

        with (
            patch("app.services.generator._get_async_lock", return_value=asyncio.Lock()),
            patch(
                "app.services.generator.async_file_lock",
                new=_raise_timeout,
            ),
            pytest.raises(GenerationBusyError) as exc_info,
        ):
            await generate_and_save_image(app)

        assert isinstance(exc_info.value.__cause__, FileLockTimeout)


class TestReadDataFile:
    """Tests for _read_data_file function."""

    def test_returns_dict_for_valid_json(self, tmp_path: Path) -> None:
        from app.services.generator import _read_data_file

        data_file = tmp_path / "state.json"
        data_file.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        result = _read_data_file(data_file)
        assert result == {"key": "value"}

    def test_returns_none_for_non_dict_json(self, tmp_path: Path) -> None:
        from app.services.generator import _read_data_file

        data_file = tmp_path / "state.json"
        data_file.write_text(json.dumps(["not", "dict"]), encoding="utf-8")
        result = _read_data_file(data_file)
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        from app.services.generator import _read_data_file

        data_file = tmp_path / "state.json"
        data_file.write_text("{invalid json", encoding="utf-8")
        result = _read_data_file(data_file)
        assert result is None

    def test_returns_none_on_os_error(self, tmp_path: Path) -> None:
        from app.services.generator import _read_data_file

        data_file = tmp_path / "nonexistent.json"
        result = _read_data_file(data_file)
        assert result is None


class TestIsRecentlyUpdated:
    """Tests for _is_recently_updated function."""

    def test_returns_true_when_within_threshold(self) -> None:
        from app.services.generator import _is_recently_updated

        with patch("app.services.generator.time.time", return_value=1000.0):
            result = _is_recently_updated({"updated_at": 995500}, threshold_sec=10)
        assert result is True

    def test_returns_false_when_stale(self) -> None:
        from app.services.generator import _is_recently_updated

        with patch("app.services.generator.time.time", return_value=1000.0):
            result = _is_recently_updated({"updated_at": 980000}, threshold_sec=10)
        assert result is False

    @pytest.mark.parametrize("updated_at", [None, "1000", True, 0, -1])
    def test_returns_false_for_invalid_updated_at(self, updated_at: object) -> None:
        from app.services.generator import _is_recently_updated

        with patch("app.services.generator.time.time", return_value=1000.0):
            result = _is_recently_updated({"updated_at": updated_at})
        assert result is False


class TestFetchAllDataParallel:
    """Tests for _fetch_all_data_parallel function."""

    @pytest.mark.asyncio
    async def test_fetches_all_data_from_service_container(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.services.generator import _fetch_all_data_parallel

        services = SimpleNamespace(
            data_fetcher=SimpleNamespace(get=AsyncMock(return_value={"headline": "ok"})),
            holiday_service=SimpleNamespace(get=AsyncMock(return_value=[{"name": "春节"}])),
            fun_content_service=SimpleNamespace(get=AsyncMock(return_value={"title": "摸鱼文案"})),
            kfc_service=SimpleNamespace(get=AsyncMock(return_value={"content": "疯狂星期四"})),
            stock_index_service=SimpleNamespace(fetch_indices=AsyncMock(return_value={"items": [1, 2]})),
        )
        app = SimpleNamespace(state=SimpleNamespace(services=services))
        logger = MagicMock()

        result = await _fetch_all_data_parallel(app, logger)

        assert result["headline"] == "ok"
        assert result["holidays"] == [{"name": "春节"}]
        assert result["fun_content"] == {"title": "摸鱼文案"}
        assert result["kfc_copy"] == {"content": "疯狂星期四"}
        assert result["stock_indices"] == {"items": [1, 2]}

    @pytest.mark.asyncio
    async def test_handles_missing_optional_services_and_errors(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.services.generator import _fetch_all_data_parallel

        app = SimpleNamespace(
            state=SimpleNamespace(
                data_fetcher=SimpleNamespace(get=AsyncMock(return_value=["bad-data"])),
                holiday_service=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("down"))),
                fun_content_service=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("down"))),
                kfc_service=None,
                stock_index_service=None,
            )
        )
        logger = MagicMock()

        result = await _fetch_all_data_parallel(app, logger)

        assert result == {"holidays": [], "fun_content": None, "kfc_copy": None, "stock_indices": None}
        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        assert any("raw_data is not dict" in msg for msg in warning_messages)

    @pytest.mark.asyncio
    async def test_handles_none_returns_and_exceptions(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.services.generator import _fetch_all_data_parallel

        services = SimpleNamespace(
            data_fetcher=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("api down"))),
            holiday_service=SimpleNamespace(get=AsyncMock(return_value=None)),
            fun_content_service=SimpleNamespace(get=AsyncMock(return_value=None)),
            kfc_service=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("kfc down"))),
            stock_index_service=SimpleNamespace(fetch_indices=AsyncMock(side_effect=RuntimeError("stock down"))),
        )
        app = SimpleNamespace(state=SimpleNamespace(services=services))
        logger = MagicMock()

        result = await _fetch_all_data_parallel(app, logger)

        assert result == {"holidays": [], "fun_content": None, "kfc_copy": None, "stock_indices": None}


class TestScheduleCacheCleanup:
    """Tests for _schedule_cache_cleanup function."""

    @pytest.mark.asyncio
    async def test_schedules_cleanup_and_logs_result(self) -> None:
        from unittest.mock import AsyncMock

        from app.services.generator import _schedule_cache_cleanup

        cache_cleaner = MagicMock()
        logger = MagicMock()
        created_tasks: list[asyncio.Task] = []
        original_create_task = asyncio.create_task

        def _capture_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with (
            patch("app.services.generator.asyncio.create_task", side_effect=_capture_task),
            patch(
                "app.services.generator.asyncio.to_thread",
                new=AsyncMock(return_value={"deleted_files": 2, "freed_bytes": 4096}),
            ),
        ):
            _schedule_cache_cleanup(cache_cleaner, logger)
            await asyncio.gather(*created_tasks)

        logger.info.assert_called_once_with("Cleaned up 2 expired cache file(s), freed 4.0 KB")

    @pytest.mark.asyncio
    async def test_logs_warning_when_cleanup_fails(self) -> None:
        from unittest.mock import AsyncMock

        from app.services.generator import _schedule_cache_cleanup

        cache_cleaner = MagicMock()
        logger = MagicMock()
        created_tasks: list[asyncio.Task] = []
        original_create_task = asyncio.create_task

        def _capture_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with (
            patch("app.services.generator.asyncio.create_task", side_effect=_capture_task),
            patch("app.services.generator.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            _schedule_cache_cleanup(cache_cleaner, logger)
            await asyncio.gather(*created_tasks)

        assert "Cache cleanup failed: boom" in logger.warning.call_args.args[0]


class TestUpdateDataFile:
    """Tests for _update_data_file function."""

    @pytest.mark.asyncio
    async def test_writes_data_file_and_merges_existing_images(self, tmp_path: Path) -> None:
        from datetime import date, datetime, timezone

        from app.services.generator import _update_data_file

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text(json.dumps({"images": {"legacy": "legacy.jpg"}}), encoding="utf-8")

        template_data = {
            "date": {"week_cn": "星期四", "lunar_date": "正月初八"},
            "weekend": False,
            "solar_term": {"name": "立春"},
            "guide": "今日指南",
            "holidays": [{"name": "春节", "days_left": 10, "color": "#FF0000"}, "invalid"],
            "news_list": [{"text": "新闻1"}, {}, 123],
            "news_meta": {"total": 2},
            "history": {"title": "摸鱼语录", "content": "今天也要开心"},
            "kfc_content": {"content": "疯狂星期四文案", "source": "kfc"},
        }
        raw_data = {"stock_indices": {"items": [{"name": "上证指数"}]}}
        fixed_now = datetime(2026, 2, 5, 9, 30, 0, tzinfo=timezone.utc)

        with (
            patch("app.services.generator.today_business", return_value=business_date),
            patch("app.services.generator.get_display_timezone", return_value=timezone.utc),
            patch("app.services.generator.datetime") as mock_datetime,
        ):
            mock_datetime.now.return_value = fixed_now
            await _update_data_file(
                data_dir=str(data_dir),
                filename="moyuren_20260205.jpg",
                template_data=template_data,
                raw_data=raw_data,
                config=MagicMock(),
                template_name="moyuren",
            )

        saved_data = json.loads(data_file.read_text(encoding="utf-8"))
        assert saved_data["images"] == {"legacy": "legacy.jpg", "moyuren": "moyuren_20260205.jpg"}
        assert saved_data["weekday"] == "星期四"
        assert saved_data["holidays"] == [{"name": "春节", "days_left": 10}]
        assert saved_data["fun_content"] == {"type": "moyu_quote", "title": "摸鱼语录", "text": "今天也要开心"}
        assert saved_data["kfc_content"] == "疯狂星期四文案"

    @pytest.mark.asyncio
    async def test_raises_storage_error_on_replace_failure(self, tmp_path: Path) -> None:
        from datetime import date, datetime, timezone

        from app.core.errors import StorageError
        from app.services.generator import _update_data_file

        data_dir = tmp_path / "data"
        business_date = date(2026, 2, 6)
        fixed_now = datetime(2026, 2, 6, 10, 0, 0, tzinfo=timezone.utc)

        with (
            patch("app.services.generator.today_business", return_value=business_date),
            patch("app.services.generator.get_display_timezone", return_value=timezone.utc),
            patch("app.services.generator.datetime") as mock_datetime,
            patch("app.services.generator.os.replace", side_effect=OSError("disk full")),
        ):
            mock_datetime.now.return_value = fixed_now
            with pytest.raises(StorageError):
                await _update_data_file(
                    data_dir=str(data_dir),
                    filename="moyuren_20260206.jpg",
                    template_data={"date": {}, "holidays": [], "news_list": []},
                    raw_data={},
                    config=MagicMock(),
                    template_name="moyuren",
                )


class TestGenerateAndSaveImageNormalFlow:
    """Tests for generate_and_save_image normal generation flow."""

    @pytest.mark.asyncio
    async def test_runs_full_pipeline(self, tmp_path: Path) -> None:
        from datetime import date
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_dir = cache_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text("{}", encoding="utf-8")

        logger = MagicMock()
        template_item = SimpleNamespace(name="moyuren")
        templates_config = MagicMock()
        templates_config.get_template.return_value = template_item

        config = MagicMock()
        config.paths = SimpleNamespace(cache_dir=str(cache_dir))
        config.get_templates_config.return_value = templates_config

        data_computer = MagicMock()
        image_renderer = MagicMock()
        image_renderer.render = AsyncMock(return_value="moyuren_20260205.jpg")
        cache_cleaner = MagicMock()

        app = SimpleNamespace(
            state=SimpleNamespace(
                logger=logger,
                config=config,
                data_computer=data_computer,
                image_renderer=image_renderer,
                cache_cleaner=cache_cleaner,
            )
        )

        raw_data = {"api_data": "ok"}
        template_data = {"date": {"week_cn": "星期四"}}
        data_computer.compute.return_value = template_data

        async_lock = asyncio.Lock()

        @asynccontextmanager
        async def _fake_file_lock(*args, **kwargs):
            yield

        mock_fetch = AsyncMock(return_value=raw_data)
        mock_update = AsyncMock()

        with (
            patch("app.services.generator._get_async_lock", return_value=async_lock),
            patch("app.services.generator.async_file_lock", new=_fake_file_lock),
            patch("app.services.generator.today_business", return_value=business_date),
            patch("app.services.generator._read_data_file", return_value={"updated_at": 1}),
            patch("app.services.generator._is_recently_updated", return_value=True),
            patch("app.services.generator._read_latest_filename", return_value=None),
            patch("app.services.generator._fetch_all_data_parallel", new=mock_fetch),
            patch("app.services.generator._update_data_file", new=mock_update),
            patch("app.services.generator._schedule_cache_cleanup") as mock_cleanup,
        ):
            filename = await generate_and_save_image(app)

        assert filename == "moyuren_20260205.jpg"
        mock_fetch.assert_awaited_once()
        data_computer.compute.assert_called_once_with(raw_data)
        image_renderer.render.assert_awaited_once()
        mock_update.assert_awaited_once()
        mock_cleanup.assert_called_once()
        assert not async_lock.locked()

    @pytest.mark.asyncio
    async def test_skips_generation_when_recently_updated(self, tmp_path: Path) -> None:
        from datetime import date
        from types import SimpleNamespace

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_dir = cache_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text("{}", encoding="utf-8")

        logger = MagicMock()
        template_item = SimpleNamespace(name="moyuren")
        templates_config = MagicMock()
        templates_config.get_template.return_value = template_item

        config = MagicMock()
        config.paths = SimpleNamespace(cache_dir=str(cache_dir))
        config.get_templates_config.return_value = templates_config

        app = SimpleNamespace(state=SimpleNamespace(logger=logger, config=config))

        async_lock = asyncio.Lock()

        @asynccontextmanager
        async def _fake_file_lock(*args, **kwargs):
            yield

        with (
            patch("app.services.generator._get_async_lock", return_value=async_lock),
            patch("app.services.generator.async_file_lock", new=_fake_file_lock),
            patch("app.services.generator.today_business", return_value=business_date),
            patch("app.services.generator._read_data_file", return_value={"updated_at": 999}),
            patch("app.services.generator._is_recently_updated", return_value=True),
            patch("app.services.generator._read_latest_filename", return_value="moyuren_cached.jpg"),
        ):
            filename = await generate_and_save_image(app)

        assert filename == "moyuren_cached.jpg"
        assert not async_lock.locked()
