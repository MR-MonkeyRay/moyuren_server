"""Cache cleanup service module."""

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
    """Cache cleaner for removing expired data, image, and render resource files."""

    def __init__(
        self,
        cache_dir: str,
        retain_days: int,
        logger: logging.Logger,
        max_images_per_template_per_day: int = 3,
    ) -> None:
        """Initialize the cache cleaner.

        Args:
            cache_dir: Root cache directory containing data/, images/, and render_resources/ subdirectories.
            retain_days: Number of days to retain cached files.
            logger: Logger instance for logging cleanup status.
            max_images_per_template_per_day: Maximum images to keep per template and day.
        """
        self.cache_dir = Path(cache_dir)
        self.data_dir = self.cache_dir / "data"
        self.images_dir = self.cache_dir / "images"
        self.render_resources_dir = self.cache_dir / "render_resources"
        self.retain_days = retain_days
        self.logger = logger
        self.max_images_per_template_per_day = max_images_per_template_per_day

        # Ensure directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.render_resources_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self, retain_days: int | None = None) -> dict[str, int | str]:
        """Clean up expired files and superseded same-day images.

        Scans cache/data/, cache/images/, and cache/render_resources/ directories and removes files
        older than retain_days. Within retention, keep up to the configured image
        limit per template and day, always preserving images referenced by data files.

        Data files format: YYYY-MM-DD.json
        Image files format: {template}_{YYYYMMDD}_{HHMMSS}.jpg
        Render resources: sha256-keyed .body/.meta pairs with JSON metadata

        Returns:
            Dictionary with cleanup statistics:
            - deleted_files: Number of files deleted
            - freed_bytes: Total bytes freed
            - oldest_kept: Date string of oldest kept file (YYYY-MM-DD)
            - pruned_images: Number of superseded images deleted within retention
        """
        effective_retain = retain_days if retain_days is not None else self.retain_days
        today = today_business()
        cutoff_date = today - timedelta(days=effective_retain)

        deleted_files = 0
        freed_bytes = 0
        pruned_images = 0
        oldest_kept_date = today

        # Read every data file before deleting expired files to protect all image references
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

        # Clean data files
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

        # Clean base images. Variants are managed as members of the base image family.
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

        # Referenced images count toward the limit, even when they are older
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

        # Remove variants left behind after their base JPEG was deleted or lost.
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

        # Clean render resource cache files
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

        # Clean orphan body files (no matching meta)
        for body_path in self.render_resources_dir.glob("*.body"):
            cache_key = body_path.stem
            meta_path = self.render_resources_dir / f"{cache_key}.meta"
            if not meta_path.exists():
                self.logger.info("Deleting orphan render resource body: %s", body_path.name)
                freed = body_path.stat().st_size
                body_path.unlink(missing_ok=True)
                deleted_files += 1
                freed_bytes += freed

        # Clean stale temporary files from interrupted atomic writes
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
        """Delete a base JPEG and every recognized sibling variant."""
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
