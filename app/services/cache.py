"""缓存清理服务模块。"""

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from app.core.errors import StorageError
from app.models.schemas import DailyDataFile
from app.services.calendar import get_business_timezone, today_business

_BASE_IMAGE_PATTERN = re.compile(
    r"^(?P<template>[A-Za-z0-9_-]+)_(?P<date>\d{8})_(?P<time>\d{6})\.jpg$"
)
_VARIANT_IMAGE_PATTERN = re.compile(
    r"^(?P<stem>[A-Za-z0-9_-]+_\d{8}_\d{6})(?:_w(?P<width>\d+))?\.(?P<format>jpg|webp)$"
)


class CacheCleaner:
    """清理过期数据、图片和渲染资源文件。"""

    def __init__(
        self,
        cache_dir: str,
        retain_days: int,
        logger: logging.Logger,
        max_images_per_template_per_day: int = 3,
    ) -> None:
        """初始化缓存清理器。

        Args:
            cache_dir: 缓存根目录，包含 data/、images/ 和 render_resources/ 子目录。
            retain_days: 缓存文件保留天数。
            logger: 记录清理状态的日志实例。
            max_images_per_template_per_day: 每个模板每天最多保留的图片数。
        """
        self.cache_dir = Path(cache_dir)
        self.data_dir = self.cache_dir / "data"
        self.images_dir = self.cache_dir / "images"
        self.render_resources_dir = self.cache_dir / "render_resources"
        self.retain_days = retain_days
        self.logger = logger
        self.max_images_per_template_per_day = max_images_per_template_per_day

        # 确保目录存在
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.render_resources_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self, retain_days: int | None = None) -> dict[str, int | str]:
        """清理过期文件和同日被更新版本替代的图片。

        扫描 cache/data/、cache/images/ 和 cache/render_resources/ 目录，删除
        超过 retain_days 的文件。每个模板每天按配置上限保留图片；
        数据文件引用的图片始终保留，即使因此超出上限。

        数据文件格式：YYYY-MM-DD.json
        图片文件格式：{template}_{YYYYMMDD}_{HHMMSS}.jpg
        渲染资源：以 sha256 为键的 .body/.meta 文件对，元数据为 JSON

        Returns:
            包含清理统计的字典：
            - deleted_files: 已删除文件数
            - freed_bytes: 释放的总字节数
            - oldest_kept: 保留文件中最早的日期字符串（YYYY-MM-DD）
            - pruned_images: 保留期内被更新版本替代而删除的图片数
        """
        effective_retain = retain_days if retain_days is not None else self.retain_days
        today = today_business()
        cutoff_date = today - timedelta(days=effective_retain)

        deleted_files = 0
        freed_bytes = 0
        pruned_images = 0
        oldest_kept_date = today

        # 删除过期文件前先读取所有数据文件，保护全部图片引用
        referenced_images: set[str] = set()
        unsafe_dates: set[date] = set()
        skip_pruning = False
        for data_path in self.data_dir.glob("*.json"):
            try:
                data = DailyDataFile.load(data_path)
                referenced_images.update(data.images.values())
            except StorageError as e:
                try:
                    unsafe_date = datetime.strptime(data_path.stem, "%Y-%m-%d").date()
                    unsafe_dates.add(unsafe_date)
                    pruning_scope = unsafe_date.isoformat()
                except ValueError:
                    skip_pruning = True
                    pruning_scope = "all dates"
                self.logger.warning(
                    "Skipping image pruning for %s: unreadable or invalid data file %s: %s",
                    pruning_scope,
                    data_path.name,
                    e,
                )

        # 清理数据文件
        data_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
        for file_path in self.data_dir.glob("*.json"):
            match = data_pattern.match(file_path.name)
            if not match:
                self.logger.warning("Skipping file with invalid name format: %s", file_path.name)
                continue

            try:
                file_date = datetime.fromisoformat(match.group(1)).date()
                if file_date < cutoff_date:
                    file_size = file_path.stat().st_size
                    file_path.unlink()
                    deleted_files += 1
                    freed_bytes += file_size
                    self.logger.info("Deleted expired data file: %s", file_path.name)
                else:
                    if file_date < oldest_kept_date:
                        oldest_kept_date = file_date
            except Exception as e:
                self.logger.warning("Failed to process data file %s: %s", file_path.name, e)

        # 清理原图；变体作为同一原图的配套文件管理
        image_groups: dict[tuple[str, date], list[tuple[str, Path]]] = {}
        for file_path in self.images_dir.glob("*.jpg"):
            match = _BASE_IMAGE_PATTERN.match(file_path.name)
            if not match:
                if not _VARIANT_IMAGE_PATTERN.match(file_path.name):
                    self.logger.warning(
                        "Skipping file with invalid name format: %s", file_path.name
                    )
                continue

            try:
                date_str = match.group("date")
                file_date = datetime.strptime(date_str, "%Y%m%d").date()
                if file_date < cutoff_date and file_path.name not in referenced_images:
                    family_deleted, family_freed = self._delete_image_family(file_path)
                    deleted_files += family_deleted
                    freed_bytes += family_freed
                    if not file_path.exists():
                        self.logger.info("Deleted expired image file: %s", file_path.name)
                else:
                    image_groups.setdefault((match.group("template"), file_date), []).append(
                        (match.group("time"), file_path)
                    )
                    if file_date < oldest_kept_date:
                        oldest_kept_date = file_date
            except Exception as e:
                self.logger.warning("Failed to process image file %s: %s", file_path.name, e)

        # 被引用的图片即使较旧，也计入保留上限
        for (_, file_date), images_in_group in image_groups.items():
            if skip_pruning or file_date in unsafe_dates:
                continue
            kept_names = {
                path.name for _, path in images_in_group if path.name in referenced_images
            }
            for _, image_path in sorted(images_in_group, reverse=True):
                if image_path.name in kept_names:
                    continue
                if len(kept_names) < self.max_images_per_template_per_day:
                    kept_names.add(image_path.name)
                    continue
                try:
                    family_deleted, family_freed = self._delete_image_family(image_path)
                    deleted_files += family_deleted
                    freed_bytes += family_freed
                    if not image_path.exists():
                        pruned_images += 1
                        self.logger.info("Pruned superseded image file: %s", image_path.name)
                except OSError as e:
                    self.logger.warning("Failed to prune image file %s: %s", image_path.name, e)

        # 删除原始 JPEG 被删除或丢失后遗留的变体
        for file_path in self.images_dir.iterdir():
            if not file_path.is_file():
                continue
            match = _VARIANT_IMAGE_PATTERN.match(file_path.name)
            if match is None or _BASE_IMAGE_PATTERN.match(file_path.name):
                continue
            base_path = self.images_dir / f"{match.group('stem')}.jpg"
            if base_path.exists() or base_path.name in referenced_images:
                continue
            try:
                file_size = file_path.stat().st_size
                file_path.unlink()
                deleted_files += 1
                freed_bytes += file_size
                self.logger.info("Deleted orphan image variant: %s", file_path.name)
            except OSError as e:
                self.logger.warning("Failed to delete orphan image variant %s: %s", file_path.name, e)

        # 清理渲染资源缓存文件
        cutoff_timestamp = datetime.combine(
            cutoff_date, datetime.min.time(), tzinfo=get_business_timezone()
        ).timestamp()
        for meta_path in self.render_resources_dir.glob("*.meta"):
            cache_key = meta_path.stem
            body_path = self.render_resources_dir / f"{cache_key}.body"
            try:
                meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
                fetched_at = float(meta_data.get("fetched_at", 0))
                if fetched_at < cutoff_timestamp:
                    freed = meta_path.stat().st_size
                    meta_path.unlink(missing_ok=True)
                    freed += body_path.stat().st_size if body_path.exists() else 0
                    body_path.unlink(missing_ok=True)
                    deleted_files += 1
                    freed_bytes += freed
                    self.logger.info("Deleted expired render resource cache: %s", cache_key)
            except (OSError, ValueError, json.JSONDecodeError) as e:
                self.logger.warning(
                    "Removing unreadable render resource meta: %s (%s)", meta_path.name, e
                )
                freed = 0
                try:
                    freed = meta_path.stat().st_size
                except OSError:
                    pass
                try:
                    meta_path.unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    if body_path.exists():
                        freed += body_path.stat().st_size
                    body_path.unlink(missing_ok=True)
                except OSError:
                    pass
                deleted_files += 1
                freed_bytes += freed

        # 清理没有对应元数据的孤立内容文件
        for body_path in self.render_resources_dir.glob("*.body"):
            cache_key = body_path.stem
            meta_path = self.render_resources_dir / f"{cache_key}.meta"
            if not meta_path.exists():
                self.logger.info("Deleting orphan render resource body: %s", body_path.name)
                freed = body_path.stat().st_size
                body_path.unlink(missing_ok=True)
                deleted_files += 1
                freed_bytes += freed

        # 清理原子写入中断后遗留的临时文件
        for tmp_path in self.render_resources_dir.glob("*.tmp"):
            self.logger.info("Deleting stale temporary file: %s", tmp_path.name)
            freed = tmp_path.stat().st_size
            tmp_path.unlink(missing_ok=True)
            deleted_files += 1
            freed_bytes += freed

        self.logger.info(
            "Cache cleanup completed: %s file(s) deleted, %s image(s) pruned, %.1f KB freed",
            deleted_files,
            pruned_images,
            freed_bytes / 1024,
        )

        return {
            "deleted_files": deleted_files,
            "freed_bytes": freed_bytes,
            "oldest_kept": oldest_kept_date.isoformat(),
            "pruned_images": pruned_images,
        }

    def _delete_image_family(self, base_path: Path) -> tuple[int, int]:
        """删除原始 JPEG 及所有可识别的配套变体。"""
        stem = base_path.stem
        candidates = [base_path, self.images_dir / f"{stem}.webp"]
        candidates.extend(self.images_dir.glob(f"{stem}_w*.jpg"))
        candidates.extend(self.images_dir.glob(f"{stem}_w*.webp"))

        deleted_files = 0
        freed_bytes = 0
        for file_path in candidates:
            match = _VARIANT_IMAGE_PATTERN.match(file_path.name)
            if match is None or match.group("stem") != stem or not file_path.exists():
                continue
            try:
                file_size = file_path.stat().st_size
                file_path.unlink()
            except OSError as exc:
                self.logger.warning("Failed to delete image family member %s: %s", file_path.name, exc)
            else:
                freed_bytes += file_size
                deleted_files += 1
        return deleted_files, freed_bytes
