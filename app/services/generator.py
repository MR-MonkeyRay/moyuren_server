"""Image generation service module."""

import asyncio
import json
import os
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI
from filelock import FileLock, Timeout

from app.core.errors import StorageError


class GenerationBusyError(Exception):
    """生成任务正在进行中"""
    pass


# 进程内锁
_async_lock: asyncio.Lock | None = None


def _get_async_lock() -> asyncio.Lock:
    """获取或创建异步锁（单例模式）"""
    global _async_lock
    if _async_lock is None:
        _async_lock = asyncio.Lock()
    return _async_lock


def _read_latest_filename(state_path: Path) -> str | None:
    """读取 state 文件中的最新文件名

    Returns:
        文件名，如果读取失败则返回 None
    """
    try:
        with state_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("filename")
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return None


async def generate_and_save_image(app: FastAPI) -> str:
    """Generate image and update state file.

    This function performs the complete image generation pipeline:
    1. Fetch data from API endpoints
    2. Compute template context
    3. Render HTML to image
    4. Update state/latest.json atomically

    Args:
        app: FastAPI application instance with services in app.state.

    Returns:
        Generated image filename.

    Raises:
        RenderError: If image rendering fails.
        StorageError: If state file update fails.
        GenerationBusyError: If generation is locked by another process.
    """
    logger = app.state.logger
    config = app.state.config
    state_path = Path(config.paths.state_path)

    # 获取进程内锁
    async_lock = _get_async_lock()

    # 获取文件锁路径
    lock_file = state_path.parent / ".generation.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    file_lock = FileLock(str(lock_file), timeout=60)

    async with async_lock:
        # 进程内锁保护 asyncio 并发
        try:
            # 使用线程执行阻塞的文件锁获取
            await asyncio.to_thread(file_lock.acquire)
            try:
                # 二次检查：获取锁后检查是否已有新生成的文件
                if state_path.exists():
                    mtime = state_path.stat().st_mtime
                    if time.time() - mtime < 5:
                        filename = _read_latest_filename(state_path)
                        if filename:
                            logger.info("State file recently updated, skipping generation")
                            return filename
                        # 如果读取失败，继续正常生成流程
                        logger.warning("State file exists but unreadable, proceeding with generation")

                logger.info("Starting image generation...")

                # 1. Fetch data from all endpoints
                raw_data = await app.state.data_fetcher.fetch_all()
                logger.info(f"Fetched data from {len(raw_data)} endpoints")

                # 1.1 Fetch holiday data
                try:
                    holidays = await app.state.holiday_service.fetch_holidays()
                    raw_data["holidays"] = holidays
                    logger.info(f"Fetched {len(holidays)} holidays")
                except Exception as e:
                    logger.warning(f"Failed to fetch holidays, using default: {e}")
                    raw_data["holidays"] = []

                # 1.2 Fetch fun content
                try:
                    fun_content = await app.state.fun_content_service.fetch_content(date.today())
                    raw_data["fun_content"] = fun_content
                    logger.info(f"Fetched fun content: {fun_content.get('title')}")
                except Exception as e:
                    logger.warning(f"Failed to fetch fun content, using default: {e}")
                    raw_data["fun_content"] = None

                # 2. Compute template context
                template_data = app.state.data_computer.compute(raw_data)
                logger.info("Template data computed")

                # 3. Render image
                filename = await app.state.image_renderer.render(template_data)
                logger.info(f"Image rendered: {filename}")

                # 4. Update state/latest.json atomically
                await _update_state_file(
                    state_path=config.paths.state_path,
                    filename=filename,
                )

                # 5. Clean up old cache (run in thread to avoid blocking)
                deleted_count = await asyncio.to_thread(app.state.cache_cleaner.cleanup)
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} expired cache file(s)")

                return filename
            finally:
                # 确保释放文件锁
                try:
                    await asyncio.to_thread(file_lock.release)
                except Exception as e:
                    logger.warning(f"Failed to release file lock: {e}")
        except Timeout:
            logger.warning("Image generation skipped: another process is generating")
            raise GenerationBusyError("Image generation locked by another process")


async def _update_state_file(state_path: str, filename: str) -> None:
    """Atomically update the state file with latest image information.

    Args:
        state_path: Path to the state file (e.g., "state/latest.json").
        filename: Generated image filename.

    Raises:
        StorageError: If file write fails.
    """
    def _write_state():
        state_file = Path(state_path)

        # 确保父目录存在
        state_file.parent.mkdir(parents=True, exist_ok=True)

        # Prepare state data
        now = datetime.now()
        state_data = {
            "date": now.strftime("%Y-%m-%d"),
            "timestamp": now.isoformat(),
            "filename": filename,
        }

        # Atomic write: temp file + rename
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=state_file.parent,
                prefix=".latest_",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                json.dump(state_data, tmp_file, ensure_ascii=False, indent=2)
                tmp_path = tmp_file.name

            os.replace(tmp_path, state_file)

        except Exception as e:
            # Clean up temp file if it exists
            if tmp_path and Path(tmp_path).exists():
                Path(tmp_path).unlink(missing_ok=True)
            raise StorageError(
                message=f"Failed to update state file: {state_path}",
                detail=str(e),
            ) from e

    # Run blocking IO in thread
    await asyncio.to_thread(_write_state)
