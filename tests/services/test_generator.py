import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Coroutine, cast
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from app.core.errors import StorageError
from app.core.filelock import FileLockTimeout
from app.core.services import AppServices
from app.services.compute import DataComputer
from app.services.generator import (
    GenerationBusyError,
    ImageGenerator,
    _read_latest_filename,
    generate_and_save_image,
)
from app.services.renderer import ImageRenderer


def _build_services(generator: ImageGenerator | None = None, **overrides: object) -> AppServices:
    """使用轻量测试替身构建 AppServices 容器。"""
    values: dict[str, object] = {
        "data_fetcher": MagicMock(),
        "holiday_service": MagicMock(),
        "fun_content_service": MagicMock(),
        "kfc_service": MagicMock(),
        "stock_index_service": MagicMock(),
        "gold_price_service": MagicMock(),
        "daily_english_service": MagicMock(),
        "image_renderer": MagicMock(),
        "data_computer": MagicMock(),
        "cache_cleaner": MagicMock(),
        "generator": generator or ImageGenerator(),
    }
    values.update(overrides)
    return AppServices(**cast(dict[str, Any], values))


def _build_app(
    config: object,
    services: AppServices | None = None,
    logger: object | None = None,
) -> MagicMock:
    """构建仅暴露标准状态属性的最小应用。"""
    app = MagicMock()
    app.state.config = config
    app.state.logger = logger or MagicMock()
    app.state.services = services or _build_services()
    return app


class TestGenerationBusyError:
    """测试 GenerationBusyError 异常。"""

    def test_is_exception(self) -> None:
        """验证 GenerationBusyError 属于 Exception。"""
        error = GenerationBusyError("busy")
        assert isinstance(error, Exception)

    def test_can_be_raised(self) -> None:
        """验证可以抛出 GenerationBusyError。"""
        with pytest.raises(GenerationBusyError):
            raise GenerationBusyError("Generation in progress")


class TestReadLatestFilename:
    """测试 _read_latest_filename 函数。"""

    def test_reads_v1_state(self, tmp_path: Path) -> None:
        """验证从含 images 映射的数据文件中读取文件名。"""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "images": {"moyuren": "moyuren_20260204.jpg"},
            "date": "2026-02-04",
        }
        data = {"date": "2026-02-04", "updated": "2026/02/04 10:00:00", "updated_at": 1, **data}
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file)

        assert result == "moyuren_20260204.jpg"

    def test_reads_v2_state(self, tmp_path: Path) -> None:
        """验证从含多个模板的数据文件中读取文件名。"""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "images": {
                "moyuren": "moyuren_20260204.jpg",
            },
            "date": "2026-02-04",
        }
        data = {"date": "2026-02-04", "updated": "2026/02/04 10:00:00", "updated_at": 1, **data}
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file)

        assert result == "moyuren_20260204.jpg"

    def test_reads_v2_state_with_template_name(self, tmp_path: Path) -> None:
        """验证按指定模板名从数据文件中读取文件名。"""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "images": {
                "moyuren": "moyuren_20260204.jpg",
                "custom": "custom_20260204.jpg",
            },
        }
        data = {"date": "2026-02-04", "updated": "2026/02/04 10:00:00", "updated_at": 1, **data}
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file, template_name="custom")

        assert result == "custom_20260204.jpg"

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """验证文件缺失时返回 None。"""
        data_file = tmp_path / "nonexistent.json"

        result = _read_latest_filename(data_file)

        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        """验证 JSON 无效时返回 None。"""
        data_file = tmp_path / "latest.json"
        data_file.write_text("not valid json")

        result = _read_latest_filename(data_file)

        assert result is None

    def test_returns_none_for_non_dict(self, tmp_path: Path) -> None:
        """验证 JSON 不是字典时返回 None。"""
        data_file = tmp_path / "latest.json"
        data_file.write_text(json.dumps(["array", "not", "dict"]))

        result = _read_latest_filename(data_file)

        assert result is None

    def test_returns_none_for_missing_template(self, tmp_path: Path) -> None:
        """验证 images 映射中缺少模板时返回 None。"""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "images": {
                "moyuren": "moyuren_20260204.jpg",
            },
        }
        data = {"date": "2026-02-04", "updated": "2026/02/04 10:00:00", "updated_at": 1, **data}
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file, template_name="nonexistent")

        assert result is None

    def test_handles_missing_images_key(self, tmp_path: Path) -> None:
        """验证能够妥善处理缺少 images 键的情况。"""
        data_file = tmp_path / "2026-02-04.json"
        data = {
            "date": "2026-02-04",
        }
        data = {"date": "2026-02-04", "updated": "2026/02/04 10:00:00", "updated_at": 1, **data}
        data_file.write_text(json.dumps(data))

        result = _read_latest_filename(data_file)

        assert result is None


class TestGenerationBusyErrorExceptionChain:
    """测试 generate_and_save_image 的异常链。"""

    @staticmethod
    def _build_app(tmp_path: Path) -> MagicMock:
        """为 generate_and_save_image 构建最小 FastAPI mock 应用。"""
        data_file = tmp_path / "cache" / "latest.json"
        data_file.parent.mkdir(parents=True, exist_ok=True)

        template_item = MagicMock()
        template_item.name = "moyuren"
        templates_config = MagicMock()
        templates_config.get_template.return_value = template_item
        templates_config.items = [template_item]

        config = MagicMock()
        config.paths.cache_dir = str(tmp_path / "cache")
        config.paths.data_file = str(data_file)
        config.get_templates_config.return_value = templates_config
        return _build_app(config)

    @pytest.mark.asyncio
    async def test_busy_error_preserves_inflight_timeout_cause(self, tmp_path: Path) -> None:
        """验证进程内等待超时时保留 TimeoutError 作为异常原因。"""
        app = self._build_app(tmp_path)
        generator = app.state.services.generator
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_generation(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            await release.wait()
            return {"moyuren": "moyuren.jpg"}

        with (
            patch.object(generator, "_generate_and_save_image", side_effect=slow_generation),
            patch("app.services.generator.INFLIGHT_WAIT_TIMEOUT_SEC", 0.01),
            pytest.raises(GenerationBusyError) as exc_info,
        ):
            first = asyncio.create_task(generate_and_save_image(app, "moyuren"))
            await started.wait()
            try:
                await generate_and_save_image(app, "moyuren")
            finally:
                release.set()
                await first

        assert isinstance(exc_info.value.__cause__, asyncio.TimeoutError)

    @pytest.mark.asyncio
    async def test_busy_error_preserves_filelock_timeout_cause(self, tmp_path: Path) -> None:
        """验证 generate_and_save_image 将 FileLockTimeout 保留为 __cause__。"""
        app = self._build_app(tmp_path)

        @asynccontextmanager
        async def _raise_timeout(
            *args: object, **kwargs: object
        ) -> AsyncIterator[None]:
            raise FileLockTimeout("lock")
            yield

        with (
            patch(
                "app.services.generator.async_file_lock",
                new=_raise_timeout,
            ),
            pytest.raises(GenerationBusyError) as exc_info,
        ):
            await generate_and_save_image(app)

        assert isinstance(exc_info.value.__cause__, FileLockTimeout)


class TestImageGeneratorSingleFlight:
    """测试进程内合并并发生成请求。"""

    @staticmethod
    def _build_app(generator: ImageGenerator, names: tuple[str, ...] = ("moyuren", "cute")) -> MagicMock:
        templates_config = MagicMock()
        templates_config.items = [SimpleNamespace(name=name) for name in names]
        config = MagicMock()
        config.get_templates_config.return_value = templates_config
        return _build_app(config, _build_services(generator=generator))

    @pytest.mark.asyncio
    async def test_matching_calls_share_one_generation(self) -> None:
        """匹配的调用方共享一次获取和渲染流水线及其结果。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        started = asyncio.Event()
        release = asyncio.Event()
        fetch = AsyncMock(return_value={"headline": "ok"})
        render = AsyncMock(return_value="moyuren.jpg")

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            await release.wait()
            await fetch()
            return {"moyuren": await render()}

        with patch.object(generator, "_generate_and_save_image", side_effect=run) as generate:
            first = asyncio.create_task(generator.generate(app, "moyuren"))
            await started.wait()
            second = asyncio.create_task(generator.generate(app, "moyuren"))
            await asyncio.sleep(0)
            release.set()
            first_result, second_result = await asyncio.gather(first, second)

        assert first_result is second_result
        assert first_result == {"moyuren": "moyuren.jpg"}
        generate.assert_awaited_once_with(app, "moyuren", "manual")
        fetch.assert_awaited_once()
        render.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_template_call_covers_named_waiter(self) -> None:
        """指定模板的调用共享涵盖该模板的全模板生成任务。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        started = asyncio.Event()
        release = asyncio.Event()

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            await release.wait()
            return {"moyuren": "moyuren.jpg", "cute": "cute.jpg"}

        with patch.object(generator, "_generate_and_save_image", side_effect=run) as generate:
            first = asyncio.create_task(generator.generate(app))
            await started.wait()
            second = asyncio.create_task(generator.generate(app, "cute"))
            release.set()
            first_result, second_result = await asyncio.gather(first, second)

        assert first_result is second_result
        generate.assert_awaited_once_with(app, None, "manual")

    @pytest.mark.asyncio
    async def test_exception_propagates_to_all_waiters(self) -> None:
        """创建者及所有匹配的等待方均收到任务异常。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        started = asyncio.Event()
        release = asyncio.Event()
        failure = StorageError("generation failed")

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            await release.wait()
            raise failure

        with patch.object(generator, "_generate_and_save_image", side_effect=run) as generate:
            first = asyncio.create_task(generator.generate(app, "moyuren"))
            await started.wait()
            second = asyncio.create_task(generator.generate(app, "moyuren"))
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)

        assert results == [failure, failure]
        generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_task_timeout_error_propagates_unchanged(self) -> None:
        """生成时抛出的 TimeoutError 原样传递给所有调用方。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        started = asyncio.Event()
        release = asyncio.Event()
        failure = TimeoutError("upstream timed out")

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            await release.wait()
            raise failure

        with patch.object(generator, "_generate_and_save_image", side_effect=run):
            creator = asyncio.create_task(generator.generate(app, "moyuren"))
            await started.wait()
            waiter = asyncio.create_task(generator.generate(app, "moyuren"))
            release.set()
            results = await asyncio.gather(creator, waiter, return_exceptions=True)

        assert results == [failure, failure]

    @pytest.mark.asyncio
    async def test_all_template_call_joins_named_generation(self) -> None:
        """全模板调用加入已运行的指定模板生成任务。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        started = asyncio.Event()
        release = asyncio.Event()

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            await release.wait()
            return {"moyuren": "moyuren.jpg"}

        with patch.object(generator, "_generate_and_save_image", side_effect=run) as generate:
            creator = asyncio.create_task(generator.generate(app, "moyuren"))
            await started.wait()
            waiter = asyncio.create_task(generator.generate(app))
            release.set()
            creator_result, waiter_result = await asyncio.gather(creator, waiter)

        assert creator_result is waiter_result
        generate.assert_awaited_once_with(app, "moyuren", "manual")

    @pytest.mark.asyncio
    async def test_uncovered_template_runs_after_current_generation(self) -> None:
        """未被当前任务涵盖的模板排队，待当前任务结束后开始生成。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        calls: list[str | None] = []

        async def run(
            _app: object, template_name: str | None, _trigger: str
        ) -> dict[str, str]:
            calls.append(template_name)
            if template_name == "moyuren":
                first_started.set()
                await first_release.wait()
            return {cast(str, template_name): f"{template_name}.jpg"}

        with patch.object(generator, "_generate_and_save_image", side_effect=run):
            first = asyncio.create_task(generator.generate(app, "moyuren"))
            await first_started.wait()
            second = asyncio.create_task(generator.generate(app, "cute"))
            await asyncio.sleep(0)
            assert calls == ["moyuren"]
            first_release.set()
            first_result, second_result = await asyncio.gather(first, second)

        assert calls == ["moyuren", "cute"]
        assert first_result == {"moyuren": "moyuren.jpg"}
        assert second_result == {"cute": "cute.jpg"}

    @pytest.mark.asyncio
    async def test_queued_same_template_callers_coalesce_after_current(self) -> None:
        """同一未涵盖模板的多个排队调用共享下一次生成任务。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        calls: list[str | None] = []

        async def run(
            _app: object, template_name: str | None, _trigger: str
        ) -> dict[str, str]:
            calls.append(template_name)
            if template_name == "moyuren":
                first_started.set()
                await first_release.wait()
            return {cast(str, template_name): f"{template_name}.jpg"}

        with patch.object(generator, "_generate_and_save_image", side_effect=run):
            first = asyncio.create_task(generator.generate(app, "moyuren"))
            await first_started.wait()
            queued = [
                asyncio.create_task(generator.generate(app, "cute"))
                for _ in range(4)
            ]
            await asyncio.sleep(0)
            first_release.set()
            first_result, *queued_results = await asyncio.gather(first, *queued)

        assert calls == ["moyuren", "cute"]
        assert first_result == {"moyuren": "moyuren.jpg"}
        assert all(result is queued_results[0] for result in queued_results)
        assert queued_results[0] == {"cute": "cute.jpg"}

    @pytest.mark.asyncio
    async def test_distinct_queued_templates_remain_serial_and_coalesce(self) -> None:
        """不同的排队模板串行生成，重复调用则合并。"""
        generator = ImageGenerator()
        app = self._build_app(generator, names=("moyuren", "cute", "work"))
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        active = 0
        max_active = 0
        calls: list[str | None] = []

        async def run(
            _app: object, template_name: str | None, _trigger: str
        ) -> dict[str, str]:
            nonlocal active, max_active
            calls.append(template_name)
            active += 1
            max_active = max(max_active, active)
            try:
                if template_name == "moyuren":
                    first_started.set()
                    await first_release.wait()
                else:
                    await asyncio.sleep(0)
                return {cast(str, template_name): f"{template_name}.jpg"}
            finally:
                active -= 1

        with patch.object(generator, "_generate_and_save_image", side_effect=run):
            first = asyncio.create_task(generator.generate(app, "moyuren"))
            await first_started.wait()
            cute_callers = [
                asyncio.create_task(generator.generate(app, "cute"))
                for _ in range(3)
            ]
            work_callers = [
                asyncio.create_task(generator.generate(app, "work"))
                for _ in range(3)
            ]
            first_release.set()
            results = await asyncio.gather(first, *cute_callers, *work_callers)

        assert max_active == 1
        assert calls[0] == "moyuren"
        assert calls.count("cute") == 1
        assert calls.count("work") == 1
        assert all(result is results[1] for result in results[1:4])
        assert all(result is results[4] for result in results[4:])
        assert generator._task is None
        assert generator._templates == frozenset()

    @pytest.mark.asyncio
    async def test_creator_cancellation_does_not_cancel_generation(self) -> None:
        """取消创建者不会中断受保护的共享任务。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        started = asyncio.Event()
        release = asyncio.Event()

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            await release.wait()
            return {"moyuren": "moyuren.jpg"}

        with patch.object(generator, "_generate_and_save_image", side_effect=run) as generate:
            creator = asyncio.create_task(generator.generate(app, "moyuren"))
            await started.wait()
            creator.cancel()
            with pytest.raises(asyncio.CancelledError):
                await creator
            waiter = asyncio.create_task(generator.generate(app, "moyuren"))
            release.set()
            assert await waiter == {"moyuren": "moyuren.jpg"}

        generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_waiter_cancellation_does_not_cancel_creator(self) -> None:
        """取消等待方不会中断创建者的共享任务。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        started = asyncio.Event()
        release = asyncio.Event()

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            await release.wait()
            return {"moyuren": "moyuren.jpg"}

        with patch.object(generator, "_generate_and_save_image", side_effect=run) as generate:
            creator = asyncio.create_task(generator.generate(app, "moyuren"))
            await started.wait()
            waiter = asyncio.create_task(generator.generate(app, "moyuren"))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            release.set()
            assert await creator == {"moyuren": "moyuren.jpg"}

        generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_instances_have_independent_flights(self) -> None:
        """不同的生成器实例可以并发生成。"""
        first_generator = ImageGenerator()
        second_generator = ImageGenerator()
        first_app = self._build_app(first_generator)
        second_app = self._build_app(second_generator)
        both_started = asyncio.Event()
        started = 0

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            return {"moyuren": "moyuren.jpg"}

        with (
            patch.object(first_generator, "_generate_and_save_image", side_effect=run),
            patch.object(second_generator, "_generate_and_save_image", side_effect=run),
        ):
            results = await asyncio.wait_for(
                asyncio.gather(
                    first_generator.generate(first_app, "moyuren"),
                    second_generator.generate(second_app, "moyuren"),
                ),
                timeout=1,
            )

        assert results == [
            {"moyuren": "moyuren.jpg"},
            {"moyuren": "moyuren.jpg"},
        ]

    @pytest.mark.asyncio
    async def test_shutdown_cancels_and_drains_active_task(self) -> None:
        """关闭时取消并等待当前生成任务结束。"""
        generator = ImageGenerator()
        app = self._build_app(generator)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def run(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return {}

        with patch.object(generator, "_generate_and_save_image", side_effect=run):
            caller = asyncio.create_task(generator.generate(app, "moyuren"))
            await started.wait()
            await generator.shutdown()
            with pytest.raises(asyncio.CancelledError):
                await caller

        assert cancelled.is_set()

    @pytest.mark.asyncio
    async def test_real_pipeline_is_shared_by_overlapping_calls(
        self, tmp_path: Path
    ) -> None:
        """重叠的封装函数调用只执行一次底层流水线。"""
        generator = ImageGenerator()
        cache_dir = tmp_path / "cache"
        template = SimpleNamespace(name="moyuren")
        templates_config = MagicMock()
        templates_config.items = [template]
        templates_config.get_template.return_value = template
        config = MagicMock()
        config.paths.cache_dir = str(cache_dir)
        config.get_templates_config.return_value = templates_config
        data_computer = MagicMock()
        data_computer.compute.return_value = {"date": {}}
        image_renderer = MagicMock()
        image_renderer.render = AsyncMock(return_value="moyuren.jpg")
        services = _build_services(
            generator=generator,
            data_computer=data_computer,
            image_renderer=image_renderer,
        )
        app = _build_app(config, services)
        fetch_started = asyncio.Event()
        fetch_release = asyncio.Event()

        async def fetch(*args: object, **kwargs: object) -> dict[str, str]:
            fetch_started.set()
            await fetch_release.wait()
            return {"headline": "ok"}

        @asynccontextmanager
        async def file_lock(
            *args: object, **kwargs: object
        ) -> AsyncIterator[None]:
            yield

        with (
            patch("app.services.generator.async_file_lock", new=file_lock),
            patch(
                "app.services.generator._fetch_all_data_parallel",
                new=AsyncMock(side_effect=fetch),
            ) as fetch_mock,
            patch("app.services.generator._update_data_file", new=AsyncMock()),
            patch("app.services.generator._schedule_cache_cleanup"),
        ):
            creator = asyncio.create_task(generate_and_save_image(app, "moyuren"))
            await fetch_started.wait()
            waiter = asyncio.create_task(generate_and_save_image(app, "moyuren"))
            fetch_release.set()
            creator_result, waiter_result = await asyncio.gather(creator, waiter)

        assert creator_result is waiter_result
        fetch_mock.assert_awaited_once()
        data_computer.compute.assert_called_once()
        image_renderer.render.assert_awaited_once()


class TestReadDataFile:
    """测试 _read_data_file 函数。"""

    def test_returns_dict_for_valid_json(self, tmp_path: Path) -> None:
        from app.services.generator import _read_data_file

        data_file = tmp_path / "state.json"
        data = {
            "date": "2026-02-04", "updated": "2026/02/04 10:00:00",
            "updated_at": 1, "images": {"moyuren": "image.jpg"}, "key": "value",
        }
        data_file.write_text(json.dumps(data), encoding="utf-8")
        result = _read_data_file(data_file)
        assert result == data

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
    """测试 _is_recently_updated 函数。"""

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
    """测试 _fetch_all_data_parallel 函数。"""

    @pytest.mark.asyncio
    async def test_uses_empty_holidays_when_service_is_disabled(self) -> None:
        """节假日服务缺失时使用空节假日列表继续生成。"""
        from app.services.generator import _fetch_all_data_parallel

        services = _build_services(
            data_fetcher=None,
            holiday_service=None,
            fun_content_service=None,
            kfc_service=None,
            stock_index_service=None,
            gold_price_service=None,
            daily_english_service=None,
        )
        app = MagicMock()
        app.state.services = services

        result = await _fetch_all_data_parallel(app, MagicMock())

        assert result == {
            "holidays": [],
            "make_up_workdays": [],
            "fun_content": None,
            "kfc_copy": None,
            "stock_indices": None,
            "gold_price": None,
            "daily_english": None,
            "stale_sources": [],
        }

    @pytest.mark.asyncio
    async def test_fetches_all_data_from_service_container(self) -> None:
        from app.services.generator import _fetch_all_data_parallel

        services = _build_services(
            data_fetcher=SimpleNamespace(get=AsyncMock(return_value={"headline": "ok"})),
            holiday_service=SimpleNamespace(get=AsyncMock(return_value={
                "holidays": [{"name": "春节"}],
                "make_up_workdays": ["2027-02-06"],
            })),
            fun_content_service=SimpleNamespace(get=AsyncMock(return_value={"title": "摸鱼文案"})),
            kfc_service=SimpleNamespace(get=AsyncMock(return_value={"content": "疯狂星期四"})),
            stock_index_service=SimpleNamespace(fetch_indices=AsyncMock(return_value={"items": [1, 2]})),
            gold_price_service=SimpleNamespace(get=AsyncMock(return_value={"today_price": "680.00", "sell_price": "670.00", "unit": "元/克"})),
            daily_english_service=SimpleNamespace(get=AsyncMock(return_value={"word": "hello", "translation": "你好"})),
        )
        app = MagicMock()
        app.state.services = services
        logger = MagicMock()

        result = await _fetch_all_data_parallel(app, logger)

        assert result["headline"] == "ok"
        assert result["holidays"] == [{"name": "春节"}]
        assert result["make_up_workdays"] == ["2027-02-06"]
        assert result["fun_content"] == {"title": "摸鱼文案"}
        assert result["kfc_copy"] == {"content": "疯狂星期四"}
        assert result["stock_indices"] == {"items": [1, 2]}
        assert result["gold_price"] == {"today_price": "680.00", "sell_price": "670.00", "unit": "元/克"}
        assert result["daily_english"] == {"word": "hello", "translation": "你好"}

    @pytest.mark.asyncio
    async def test_handles_missing_optional_services_and_errors(self) -> None:
        from app.services.generator import _fetch_all_data_parallel

        app = MagicMock()
        app.state.services = _build_services(
                data_fetcher=SimpleNamespace(get=AsyncMock(return_value=["bad-data"])),
                holiday_service=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("down"))),
                fun_content_service=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("down"))),
                kfc_service=None,
                stock_index_service=None,
                gold_price_service=None,
                daily_english_service=None,
        )
        logger = MagicMock()

        result = await _fetch_all_data_parallel(app, logger)

        assert result == {"holidays": [], "make_up_workdays": [], "fun_content": None, "kfc_copy": None, "stock_indices": None, "gold_price": None, "daily_english": None, "stale_sources": []}
        warning_messages = [call.args[0] for call in logger.warning.call_args_list]
        assert any("raw_data is not dict" in msg for msg in warning_messages)

    @pytest.mark.asyncio
    async def test_handles_none_returns_and_exceptions(self) -> None:
        from app.services.generator import _fetch_all_data_parallel

        services = _build_services(
            data_fetcher=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("api down"))),
            holiday_service=SimpleNamespace(get=AsyncMock(return_value=None)),
            fun_content_service=SimpleNamespace(get=AsyncMock(return_value=None)),
            kfc_service=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("kfc down"))),
            stock_index_service=SimpleNamespace(fetch_indices=AsyncMock(side_effect=RuntimeError("stock down"))),
            gold_price_service=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("gold down"))),
            daily_english_service=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("english down"))),
        )
        app = MagicMock()
        app.state.services = services
        logger = MagicMock()

        result = await _fetch_all_data_parallel(app, logger)

        assert result == {"holidays": [], "make_up_workdays": [], "fun_content": None, "kfc_copy": None, "stock_indices": None, "gold_price": None, "daily_english": None, "stale_sources": []}


class TestScheduleCacheCleanup:
    """测试 _schedule_cache_cleanup 函数。"""

    @pytest.mark.asyncio
    async def test_retains_cleanup_task_until_done(self) -> None:
        """保留未完成清理任务的引用，完成后释放。"""
        import app.services.generator as generator_module

        started = asyncio.Event()
        release = asyncio.Event()

        async def cleanup_inline(*args: object) -> dict[str, int]:
            started.set()
            await release.wait()
            return {"deleted_files": 0, "freed_bytes": 0}

        with patch("app.services.generator.asyncio.to_thread", side_effect=cleanup_inline):
            generator_module._schedule_cache_cleanup(MagicMock(), MagicMock())
            await started.wait()
            try:
                tasks = generator_module._cleanup_tasks.copy()
                assert len(tasks) == 1
                task = next(iter(tasks))
                assert not task.done()
            finally:
                release.set()
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        assert task.done()
        assert task not in generator_module._cleanup_tasks

    @pytest.mark.asyncio
    async def test_schedules_cleanup_and_logs_result(self, caplog: pytest.LogCaptureFixture) -> None:
        from unittest.mock import AsyncMock

        from app.services.generator import _schedule_cache_cleanup

        cache_cleaner = MagicMock()
        logger = logging.getLogger("test.cleanup")
        caplog.set_level(logging.INFO, logger=logger.name)
        created_tasks: list[asyncio.Task[Any]] = []
        original_create_task = asyncio.create_task

        def _capture_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
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

        assert "Cleaned up 2 expired cache file(s), freed 4.0 KB" in caplog.messages

    @pytest.mark.asyncio
    async def test_logs_warning_when_cleanup_fails(self, caplog: pytest.LogCaptureFixture) -> None:
        from unittest.mock import AsyncMock

        from app.services.generator import _schedule_cache_cleanup

        cache_cleaner = MagicMock()
        logger = logging.getLogger("test.cleanup")
        created_tasks: list[asyncio.Task[Any]] = []
        original_create_task = asyncio.create_task

        def _capture_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with (
            patch("app.services.generator.asyncio.create_task", side_effect=_capture_task),
            patch("app.services.generator.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            _schedule_cache_cleanup(cache_cleaner, logger)
            await asyncio.gather(*created_tasks)

        assert "Cache cleanup failed: boom" in caplog.messages


class TestUpdateDataFile:
    """测试 _update_data_file 函数。"""

    @pytest.mark.asyncio
    async def test_writes_data_file_and_merges_existing_images(self, tmp_path: Path) -> None:
        from datetime import date, datetime, timezone

        from app.services.generator import _update_data_file

        async def run_inline(func: Any) -> None:
            func()

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text(json.dumps({
            "date": business_date.isoformat(), "updated": "2026/02/05 09:00:00",
            "updated_at": 1, "images": {"legacy": "legacy.jpg"},
        }), encoding="utf-8")

        template_data = {
            "date": {"week_cn": "星期四", "lunar_date": "正月初八"},
            "weekend": {"days_left": 2, "is_weekend": False},
            "solar_term": {"name": "立春"},
            "guide": {"yi": ["摸鱼"], "ji": ["加班"]},
            "holidays": [{"name": "春节", "days_left": 10, "color": "#FF0000"}, "invalid"],
            "news_list": [{"text": "新闻1"}, {}, 123],
            "news_meta": {"total": 2},
            "history": {"title": "摸鱼语录", "content": "今天也要开心"},
            "kfc_content": {"content": "疯狂星期四文案", "source": "kfc"},
            "daily_english": {
                "word": "hello",
                "translation": "capped translation",
                "definition": "capped definition",
            },
        }
        raw_data = {
            "stock_indices": {"items": [{"name": "上证指数"}]},
            "daily_english": {
                "word": "hello",
                "translation": "uncapped translation from source",
                "definition": "uncapped definition from source",
            },
        }
        fixed_now = datetime(2026, 2, 5, 9, 30, 0, tzinfo=timezone.utc)

        with (
            patch("app.services.generator.now_business", return_value=fixed_now),
            patch("app.services.generator.get_display_timezone", return_value=timezone.utc),
            patch("app.services.generator.asyncio.to_thread", side_effect=run_inline),
        ):
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
        assert saved_data["daily_english"] == template_data["daily_english"]

    @pytest.mark.asyncio
    async def test_raises_storage_error_on_replace_failure(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone

        from app.core.errors import StorageError
        from app.services.generator import _update_data_file

        data_dir = tmp_path / "data"
        fixed_now = datetime(2026, 2, 6, 10, 0, 0, tzinfo=timezone.utc)

        with (
            patch("app.services.generator.now_business", return_value=fixed_now),
            patch("app.services.generator.get_display_timezone", return_value=timezone.utc),
            patch("app.models.schemas.os.replace", side_effect=OSError("disk full")),
        ):
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
    """测试 generate_and_save_image 的正常生成流程。"""

    @pytest.mark.parametrize(
        ("snapshot_iso", "write_iso", "expected_thursday", "expected_updated"),
        [
            ("2026-02-04T23:59:59+08:00", "2026-02-05T00:00:30+08:00", False, "2026/02/04 16:00:30"),
            ("2026-02-05T23:59:59+08:00", "2026-02-06T00:00:30+08:00", True, "2026/02/05 16:00:30"),
            ("2026-02-05T02:00:00+08:00", "2026-02-05T02:00:30+08:00", True, "2026/02/04 18:00:30"),
        ],
    )
    @pytest.mark.asyncio
    async def test_uses_snapshot_through_render_and_write(
        self,
        tmp_path: Path,
        snapshot_iso: str,
        write_iso: str,
        expected_thursday: bool,
        expected_updated: str,
    ) -> None:
        """保留快照的日期和星期，时间戳则使用实际写入时间。"""
        app = TestGenerationBusyErrorExceptionChain._build_app(tmp_path)
        snapshot = datetime.fromisoformat(snapshot_iso)
        computer = DataComputer()
        app.state.services.data_computer.compute.side_effect = computer.compute
        renderer = ImageRenderer.__new__(ImageRenderer)

        @asynccontextmanager
        async def fake_file_lock(*args: object, **kwargs: object) -> AsyncIterator[None]:
            yield

        with (
            freeze_time(snapshot_iso) as clock,
            patch("app.services.calendar._business_timezone", ZoneInfo("Asia/Shanghai")),
            patch("app.services.generator.get_display_timezone", return_value=timezone.utc),
            patch("app.services.generator.now_business", wraps=lambda: snapshot) as mock_now,
            patch("app.services.generator.async_file_lock", new=fake_file_lock),
            patch("app.services.generator._fetch_all_data_parallel", new=AsyncMock(return_value={})),
            patch("app.services.generator._schedule_cache_cleanup"),
        ):
            async def render(data: dict, template_name: str, *, now: datetime) -> str:
                assert data["date"]["day"] == str(snapshot.day)
                assert data["date"]["week_cn"] == ("星期四" if expected_thursday else "星期三")
                clock.move_to(write_iso)
                return renderer._generate_filename(template_name, now=now)

            app.state.services.image_renderer.render = AsyncMock(side_effect=render)
            results = await generate_and_save_image(app)
            saved_at_write = json.loads(
                (tmp_path / "cache" / "data" / f"{snapshot.date()}.json").read_text()
            )
            from app.services.generator import _is_recently_updated

            assert _is_recently_updated(saved_at_write, threshold_sec=10)

        data_dir = tmp_path / "cache" / "data"
        saved = json.loads((data_dir / f"{snapshot.date()}.json").read_text())
        assert list(data_dir.glob("*.json")) == [data_dir / f"{snapshot.date()}.json"]
        assert saved["date"] == snapshot.date().isoformat()
        assert saved["is_crazy_thursday"] is expected_thursday
        assert saved["updated"] == expected_updated
        assert saved["updated_at"] == int(datetime.fromisoformat(write_iso).timestamp() * 1000)
        assert results == {"moyuren": f"moyuren_{snapshot:%Y%m%d_%H%M%S}.jpg"}
        assert saved["images"] == results
        app.state.services.data_computer.compute.assert_called_once_with({}, now=snapshot)
        mock_now.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_runs_full_pipeline(self, tmp_path: Path) -> None:
        from datetime import date
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
        templates_config.items = [template_item]

        config = MagicMock()
        config.paths = SimpleNamespace(cache_dir=str(cache_dir))
        config.get_templates_config.return_value = templates_config

        data_computer = MagicMock()
        image_renderer = MagicMock()
        image_renderer.render = AsyncMock(return_value="moyuren_20260205.jpg")
        cache_cleaner = MagicMock()

        app = _build_app(
            config,
            _build_services(
                data_computer=data_computer,
                image_renderer=image_renderer,
                cache_cleaner=cache_cleaner,
            ),
            logger,
        )

        raw_data = {"api_data": "ok"}
        template_data = {"date": {"week_cn": "星期四"}}
        data_computer.compute.return_value = template_data

        @asynccontextmanager
        async def _fake_file_lock(
            *args: object, **kwargs: object
        ) -> AsyncIterator[None]:
            yield

        mock_fetch = AsyncMock(return_value=raw_data)
        mock_update = AsyncMock()

        with (
            patch("app.services.generator.async_file_lock", new=_fake_file_lock),
            patch("app.services.generator.now_business", return_value=datetime.combine(business_date, time(12), tzinfo=timezone.utc)),
            patch("app.services.generator._read_data_file", return_value={"updated_at": 1}),
            patch("app.services.generator._is_recently_updated", return_value=True),
            patch("app.services.generator._read_latest_filename", return_value=None),
            patch("app.services.generator._fetch_all_data_parallel", new=mock_fetch),
            patch("app.services.generator._update_data_file", new=mock_update),
            patch("app.services.generator._schedule_cache_cleanup") as mock_cleanup,
        ):
            results = await generate_and_save_image(app)

        assert results == {"moyuren": "moyuren_20260205.jpg"}
        mock_fetch.assert_awaited_once()
        data_computer.compute.assert_called_once_with(
            raw_data, now=datetime.combine(business_date, time(12), tzinfo=timezone.utc)
        )
        image_renderer.render.assert_awaited_once()
        mock_update.assert_awaited_once()
        mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_generation_when_recently_updated(self, tmp_path: Path) -> None:
        from datetime import date
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_dir = cache_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text("{}", encoding="utf-8")
        images_dir = cache_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "moyuren_cached.jpg").write_bytes(b"cached")

        logger = MagicMock()
        template_item = SimpleNamespace(name="moyuren")
        templates_config = MagicMock()
        templates_config.items = [template_item]

        config = MagicMock()
        config.paths = SimpleNamespace(cache_dir=str(cache_dir))
        config.get_templates_config.return_value = templates_config

        app = _build_app(config, logger=logger)

        @asynccontextmanager
        async def _fake_file_lock(
            *args: object, **kwargs: object
        ) -> AsyncIterator[None]:
            yield

        with (
            patch("app.services.generator.async_file_lock", new=_fake_file_lock),
            patch("app.services.generator.now_business", return_value=datetime.combine(business_date, time(12), tzinfo=timezone.utc)),
            patch("app.services.generator._read_data_file", return_value={"updated_at": 999}),
            patch("app.services.generator._is_recently_updated", return_value=True),
            patch("app.services.generator._read_latest_filename", return_value="moyuren_cached.jpg"),
        ):
            results = await generate_and_save_image(app)

        assert results == {"moyuren": "moyuren_cached.jpg"}

    @pytest.mark.asyncio
    async def test_rerenders_recent_template_when_cached_image_is_missing(
        self, tmp_path: Path
    ) -> None:
        """近期生成模板引用的图片缺失时重新渲染。"""
        from datetime import date
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text("{}", encoding="utf-8")

        logger = MagicMock()
        template_item = SimpleNamespace(name="moyuren")
        templates_config = MagicMock()
        templates_config.items = [template_item]

        config = MagicMock()
        config.paths = SimpleNamespace(cache_dir=str(cache_dir))
        config.get_templates_config.return_value = templates_config

        data_computer = MagicMock()
        data_computer.compute.return_value = {"date": {"week_cn": "星期四"}}
        image_renderer = MagicMock()
        image_renderer.render = AsyncMock(return_value="moyuren_new.jpg")
        app = _build_app(
            config,
            _build_services(
                data_computer=data_computer,
                image_renderer=image_renderer,
                cache_cleaner=MagicMock(),
            ),
            logger,
        )

        @asynccontextmanager
        async def _fake_file_lock(*args: object, **kwargs: object) -> AsyncIterator[None]:
            yield

        with (
            patch("app.services.generator.async_file_lock", new=_fake_file_lock),
            patch(
                "app.services.generator.now_business",
                return_value=datetime.combine(
                    business_date, time(12), tzinfo=timezone.utc
                ),
            ),
            patch(
                "app.services.generator._read_data_file",
                return_value={"updated_at": 999},
            ),
            patch("app.services.generator._is_recently_updated", return_value=True),
            patch(
                "app.services.generator._read_latest_filename",
                return_value="moyuren_missing.jpg",
            ),
            patch(
                "app.services.generator._fetch_all_data_parallel",
                new=AsyncMock(return_value={}),
            ),
            patch("app.services.generator._update_data_file", new=AsyncMock()),
            patch("app.services.generator._schedule_cache_cleanup"),
        ):
            results = await generate_and_save_image(app)

        assert results == {"moyuren": "moyuren_new.jpg"}
        image_renderer.render.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_template_failure(self, tmp_path: Path) -> None:
        """验证部分模板失败时只返回成功结果。"""
        from datetime import date

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_dir = cache_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text("{}", encoding="utf-8")

        logger = MagicMock()
        template_a = SimpleNamespace(name="moyuren")
        template_b = SimpleNamespace(name="moyuren_cute")
        templates_config = MagicMock()
        templates_config.items = [template_a, template_b]

        config = MagicMock()
        config.paths = SimpleNamespace(cache_dir=str(cache_dir))
        config.get_templates_config.return_value = templates_config

        data_computer = MagicMock()
        image_renderer = MagicMock()
        # 第一个模板成功，第二个失败
        image_renderer.render = AsyncMock(
            side_effect=["moyuren_20260205.jpg", Exception("render error")]
        )
        cache_cleaner = MagicMock()

        app = _build_app(
            config,
            _build_services(
                data_computer=data_computer,
                image_renderer=image_renderer,
                cache_cleaner=cache_cleaner,
            ),
            logger,
        )

        raw_data = {"api_data": "ok"}
        data_computer.compute.return_value = {"date": {"week_cn": "星期四"}}

        @asynccontextmanager
        async def _fake_file_lock(
            *args: object, **kwargs: object
        ) -> AsyncIterator[None]:
            yield

        mock_fetch = AsyncMock(return_value=raw_data)
        mock_update = AsyncMock()

        with (
            patch("app.services.generator.async_file_lock", new=_fake_file_lock),
            patch("app.services.generator.now_business", return_value=datetime.combine(business_date, time(12), tzinfo=timezone.utc)),
            patch("app.services.generator._read_data_file", return_value={"updated_at": 1}),
            patch("app.services.generator._is_recently_updated", return_value=True),
            patch("app.services.generator._read_latest_filename", return_value=None),
            patch("app.services.generator._fetch_all_data_parallel", new=mock_fetch),
            patch("app.services.generator._update_data_file", new=mock_update),
            patch("app.services.generator._schedule_cache_cleanup"),
        ):
            results = await generate_and_save_image(app)

        # 结果中应仅包含成功的模板
        assert results == {"moyuren": "moyuren_20260205.jpg"}
        assert "moyuren_cute" not in results

    @pytest.mark.asyncio
    async def test_all_templates_fail_raises_storage_error(self, tmp_path: Path) -> None:
        """验证所有模板均失败时抛出 StorageError。"""
        from datetime import date

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_dir = cache_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text("{}", encoding="utf-8")

        logger = MagicMock()
        template_a = SimpleNamespace(name="moyuren")
        template_b = SimpleNamespace(name="moyuren_cute")
        templates_config = MagicMock()
        templates_config.items = [template_a, template_b]

        config = MagicMock()
        config.paths = SimpleNamespace(cache_dir=str(cache_dir))
        config.get_templates_config.return_value = templates_config

        data_computer = MagicMock()
        image_renderer = MagicMock()
        # 两个模板均失败
        image_renderer.render = AsyncMock(side_effect=Exception("render error"))
        cache_cleaner = MagicMock()

        app = _build_app(
            config,
            _build_services(
                data_computer=data_computer,
                image_renderer=image_renderer,
                cache_cleaner=cache_cleaner,
            ),
            logger,
        )

        data_computer.compute.return_value = {"date": {"week_cn": "星期四"}}

        @asynccontextmanager
        async def _fake_file_lock(
            *args: object, **kwargs: object
        ) -> AsyncIterator[None]:
            yield

        mock_fetch = AsyncMock(return_value={"api_data": "ok"})

        with (
            patch("app.services.generator.async_file_lock", new=_fake_file_lock),
            patch("app.services.generator.now_business", return_value=datetime.combine(business_date, time(12), tzinfo=timezone.utc)),
            patch("app.services.generator._read_data_file", return_value={"updated_at": 1}),
            patch("app.services.generator._is_recently_updated", return_value=True),
            patch("app.services.generator._read_latest_filename", return_value=None),
            patch("app.services.generator._fetch_all_data_parallel", new=mock_fetch),
            patch("app.services.generator._update_data_file", new=AsyncMock()),
            patch("app.services.generator._schedule_cache_cleanup"),
        ):
            with pytest.raises(StorageError, match="All templates failed to render"):
                await generate_and_save_image(app)

    @pytest.mark.asyncio
    async def test_partial_cache_renders_missing_only(self, tmp_path: Path) -> None:
        """验证复用已缓存模板，仅渲染缺失模板。"""
        from datetime import date

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        business_date = date(2026, 2, 5)
        data_dir = cache_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        data_file = data_dir / f"{business_date.isoformat()}.json"
        data_file.write_text("{}", encoding="utf-8")
        images_dir = cache_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "moyuren_cached.jpg").write_bytes(b"cached")

        logger = MagicMock()
        template_a = SimpleNamespace(name="moyuren")
        template_b = SimpleNamespace(name="moyuren_cute")
        templates_config = MagicMock()
        templates_config.items = [template_a, template_b]

        config = MagicMock()
        config.paths = SimpleNamespace(cache_dir=str(cache_dir))
        config.get_templates_config.return_value = templates_config

        data_computer = MagicMock()
        image_renderer = MagicMock()
        # 仅渲染缺失的模板（moyuren_cute）
        image_renderer.render = AsyncMock(return_value="cute_20260205.jpg")
        cache_cleaner = MagicMock()

        app = _build_app(
            config,
            _build_services(
                data_computer=data_computer,
                image_renderer=image_renderer,
                cache_cleaner=cache_cleaner,
            ),
            logger,
        )

        data_computer.compute.return_value = {"date": {"week_cn": "星期四"}}

        @asynccontextmanager
        async def _fake_file_lock(
            *args: object, **kwargs: object
        ) -> AsyncIterator[None]:
            yield

        mock_fetch = AsyncMock(return_value={"api_data": "ok"})
        mock_update = AsyncMock()

        # _read_latest_filename 对 moyuren 返回缓存文件，对 moyuren_cute 返回 None
        def fake_read_latest(_data_file_path: Path, tname: str) -> str | None:
            if tname == "moyuren":
                return "moyuren_cached.jpg"
            return None

        with (
            patch("app.services.generator.async_file_lock", new=_fake_file_lock),
            patch("app.services.generator.now_business", return_value=datetime.combine(business_date, time(12), tzinfo=timezone.utc)),
            patch("app.services.generator._read_data_file", return_value={"updated_at": 999}),
            patch("app.services.generator._is_recently_updated", return_value=True),
            patch("app.services.generator._read_latest_filename", side_effect=fake_read_latest),
            patch("app.services.generator._fetch_all_data_parallel", new=mock_fetch),
            patch("app.services.generator._update_data_file", new=mock_update),
            patch("app.services.generator._schedule_cache_cleanup"),
        ):
            results = await generate_and_save_image(app)

        # moyuren 来自缓存，moyuren_cute 为新渲染结果
        assert results == {"moyuren": "moyuren_cached.jpg", "moyuren_cute": "cute_20260205.jpg"}
        # render 应只调用一次（渲染缺失模板）
        image_renderer.render.assert_awaited_once()

