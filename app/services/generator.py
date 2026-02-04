"""Image generation service module."""

import asyncio
import json
import os
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from filelock import FileLock, Timeout

from app.core.errors import StorageError
from app.services.calendar import get_display_timezone
from app.services.state import migrate_state


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


def _read_latest_filename(state_path: Path, template_name: str | None = None) -> str | None:
    """读取 state 文件中的最新文件名

    Args:
        state_path: Path to state file.
        template_name: Optional template name to look up.

    Returns:
        文件名，如果读取失败则返回 None
    """
    try:
        with state_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return None

            # Check if this is v1 format (no version or version=1)
            version = data.get("version")
            is_v1 = version is None or version == 1

            # For v1 state: only return filename if requesting default template
            # This prevents returning wrong template's file when multi-template is used
            if is_v1:
                # v1 state only has one template (the default "moyuren")
                # If requesting a different template, force regeneration
                if template_name and template_name != "moyuren":
                    return None
                return data.get("filename")

            # v2 format: look up in templates map
            if template_name:
                templates = data.get("templates")
                if isinstance(templates, dict):
                    template_entry = templates.get(template_name)
                    if isinstance(template_entry, dict):
                        return template_entry.get("filename")
                return None
            return data.get("filename")
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return None


async def generate_and_save_image(app: FastAPI, template_name: str | None = None) -> str:
    """Generate image and update state file.

    This function performs the complete image generation pipeline:
    1. Fetch data from API endpoints
    2. Compute template context
    3. Render HTML to image
    4. Update state/latest.json atomically

    Args:
        app: FastAPI application instance with services in app.state.
        template_name: Optional template name to use for rendering.

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

    # Resolve template name
    templates_config = config.get_templates_config()
    template_item = templates_config.get_template(template_name)
    resolved_template_name = template_item.name

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
                        filename = _read_latest_filename(state_path, resolved_template_name)
                        if filename:
                            logger.info(f"State file recently updated for template '{resolved_template_name}', skipping generation")
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

                # 1.3 Fetch KFC Crazy Thursday content (Only on Thursday)
                raw_data["kfc_copy"] = None
                if app.state.kfc_service and date.today().weekday() == 3:
                    try:
                        kfc_copy = await app.state.kfc_service.fetch_kfc_copy()
                        raw_data["kfc_copy"] = kfc_copy
                        if kfc_copy:
                            logger.info("Fetched KFC Crazy Thursday content")
                    except Exception as e:
                        logger.warning(f"Failed to fetch KFC content: {e}")

                # 1.4 Fetch stock index data
                raw_data["stock_indices"] = None
                if app.state.stock_index_service:
                    try:
                        stock_indices = await app.state.stock_index_service.fetch_indices()
                        raw_data["stock_indices"] = stock_indices
                        logger.info(f"Fetched {len(stock_indices.get('items', []))} stock indices")
                    except Exception as e:
                        logger.warning(f"Failed to fetch stock indices: {e}")

                # 2. Compute template context
                template_data = app.state.data_computer.compute(raw_data)
                logger.info("Template data computed")

                # 3. Render image
                filename = await app.state.image_renderer.render(
                    template_data,
                    template_name=resolved_template_name,
                )
                logger.info(f"Image rendered: {filename}")

                # 4. Update state/latest.json atomically
                await _update_state_file(
                    state_path=config.paths.state_path,
                    filename=filename,
                    template_data=template_data,
                    raw_data=raw_data,
                    config=config,
                    template_name=resolved_template_name,
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


async def _update_state_file(
    state_path: str,
    filename: str,
    template_data: dict,
    raw_data: dict,
    config: Any,
    template_name: str,
) -> None:
    """Atomically update the state file with latest image information.

    Args:
        state_path: Path to the state file (e.g., "state/latest.json").
        filename: Generated image filename.
        template_data: Template data dictionary from DataComputer.
        raw_data: Raw data dictionary containing original API responses.
        config: Application config for reading render dimensions.
        template_name: Template name used for rendering.

    Raises:
        StorageError: If file write fails.
    """
    def _write_state():
        state_file = Path(state_path)

        # 确保父目录存在
        state_file.parent.mkdir(parents=True, exist_ok=True)

        # Prepare state data
        now = datetime.now(get_display_timezone())

        # Extract data from template_data
        date_info = template_data.get("date", {})
        fun_content_raw = template_data.get("history", {})
        holidays_raw = template_data.get("holidays", [])
        kfc_content_raw = template_data.get("kfc_content")

        # Build fun_content
        fun_content = None
        if fun_content_raw:
            # Determine type from title
            title = fun_content_raw.get("title", "")
            content_type = "unknown"
            if "冷笑话" in title:
                content_type = "dad_joke"
            elif "一言" in title:
                content_type = "hitokoto"
            elif "段子" in title:
                content_type = "duanzi"
            elif "摸鱼" in title:
                content_type = "moyu_quote"

            fun_content = {
                "type": content_type,
                "title": title,
                "text": fun_content_raw.get("content", "")
            }

        # Build countdowns
        countdowns = []
        for holiday in holidays_raw:
            if isinstance(holiday, dict):
                countdowns.append({
                    "name": holiday.get("name", ""),
                    "date": holiday.get("start_date", ""),
                    "days_left": holiday.get("days_left", 0)
                })

        # Build KFC content
        kfc_content = None
        if kfc_content_raw and isinstance(kfc_content_raw, dict):
            kfc_content = kfc_content_raw.get("content")

        # Prepare timestamps
        updated_iso = now.isoformat(timespec="seconds")
        updated_at_ms = int(now.timestamp() * 1000)

        # Public data (shared across templates)
        public_data = {
            "date": now.strftime("%Y-%m-%d"),
            "timestamp": now.isoformat(),
            "updated": updated_iso,
            "updated_at": updated_at_ms,
            "weekday": date_info.get("week_cn", ""),
            "lunar_date": date_info.get("lunar_date", ""),
            "fun_content": fun_content,
            "countdowns": countdowns,
            "is_crazy_thursday": now.weekday() == 3,
            "kfc_content": kfc_content,
        }

        # Template-specific data
        template_specific_data = {
            "date_info": template_data.get("date"),
            "weekend": template_data.get("weekend"),
            "solar_term": template_data.get("solar_term"),
            "guide": template_data.get("guide"),
            "news_list": [item.get("text", "") for item in template_data.get("news_list", []) if isinstance(item, dict)],
            "news_meta": template_data.get("news_meta"),
            "holidays": [
                {k: v for k, v in h.items() if k != "color"}
                for h in template_data.get("holidays", [])
                if isinstance(h, dict)
            ],
            "kfc_content_full": template_data.get("kfc_content"),
            "stock_indices": raw_data.get("stock_indices"),
        }

        # Read existing state to merge template entries
        existing_templates: dict[str, Any] = {}
        existing_template_data: dict[str, Any] = {}
        if state_file.exists():
            try:
                with state_file.open("r", encoding="utf-8") as f:
                    existing_state = json.load(f)
                if isinstance(existing_state, dict):
                    # Always use "moyuren" as default for v1 migration to preserve existing cache
                    existing_state = migrate_state(existing_state, default_template="moyuren")
                    if isinstance(existing_state.get("templates"), dict):
                        existing_templates.update(existing_state["templates"])
                    if isinstance(existing_state.get("template_data"), dict):
                        existing_template_data.update(existing_state["template_data"])
            except (OSError, json.JSONDecodeError, ValueError):
                pass

        # Update current template entry
        existing_templates[template_name] = {
            "filename": filename,
            "updated": updated_iso,
            "updated_at": updated_at_ms,
        }
        existing_template_data[template_name] = template_specific_data

        # Build v2 state data with backward compatibility
        state_data = {
            "version": 2,
            "public": public_data,
            "templates": existing_templates,
            "template_data": existing_template_data,
            # Backward compatible fields (flat structure)
            **public_data,
            "filename": filename,
            **template_specific_data,
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
