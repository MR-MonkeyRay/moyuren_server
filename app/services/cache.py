"""Cache cleanup service module."""

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from app.core.errors import StorageError
from app.models.schemas import DailyDataFile
from app.services.calendar import get_business_timezone, today_business


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
                    f"Skipping image pruning for {pruning_scope}: "
                    f"unreadable or invalid data file {data_path.name}: {e}"
                )

        # Clean data files
        data_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
        for file_path in self.data_dir.glob("*.json"):
            match = data_pattern.match(file_path.name)
            if not match:
                self.logger.warning(f"Skipping file with invalid name format: {file_path.name}")
                continue

            try:
                file_date = datetime.fromisoformat(match.group(1)).date()
                if file_date < cutoff_date:
                    file_size = file_path.stat().st_size
                    file_path.unlink()
                    deleted_files += 1
                    freed_bytes += file_size
                    self.logger.info(f"Deleted expired data file: {file_path.name}")
                else:
                    if file_date < oldest_kept_date:
                        oldest_kept_date = file_date
            except Exception as e:
                self.logger.warning(f"Failed to process data file {file_path.name}: {e}")

        # Clean image files
        image_pattern = re.compile(r"^([A-Za-z0-9_-]+)_(\d{8})_(\d{6})\.jpg$")
        image_groups: dict[tuple[str, date], list[tuple[str, Path]]] = {}
        for file_path in self.images_dir.glob("*.jpg"):
            match = image_pattern.match(file_path.name)
            if not match:
                self.logger.warning(f"Skipping file with invalid name format: {file_path.name}")
                continue

            try:
                date_str = match.group(2)
                file_date = datetime.strptime(date_str, "%Y%m%d").date()
                if file_date < cutoff_date:
                    file_size = file_path.stat().st_size
                    file_path.unlink()
                    deleted_files += 1
                    freed_bytes += file_size
                    self.logger.info(f"Deleted expired image file: {file_path.name}")
                else:
                    image_groups.setdefault((match.group(1), file_date), []).append(
                        (match.group(3), file_path)
                    )
                    if file_date < oldest_kept_date:
                        oldest_kept_date = file_date
            except Exception as e:
                self.logger.warning(f"Failed to process image file {file_path.name}: {e}")

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
                    file_size = image_path.stat().st_size
                    image_path.unlink()
                    deleted_files += 1
                    freed_bytes += file_size
                    pruned_images += 1
                    self.logger.info(f"Pruned superseded image file: {image_path.name}")
                except OSError as e:
                    self.logger.warning(f"Failed to prune image file {image_path.name}: {e}")

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
                    self.logger.info(f"Deleted expired render resource cache: {cache_key}")
            except (OSError, ValueError, json.JSONDecodeError) as e:
                self.logger.warning(f"Removing unreadable render resource meta: {meta_path.name} ({e})")
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
                self.logger.info(f"Deleting orphan render resource body: {body_path.name}")
                freed = body_path.stat().st_size
                body_path.unlink(missing_ok=True)
                deleted_files += 1
                freed_bytes += freed

        # Clean stale temporary files from interrupted atomic writes
        for tmp_path in self.render_resources_dir.glob("*.tmp"):
            self.logger.info(f"Deleting stale temporary file: {tmp_path.name}")
            freed = tmp_path.stat().st_size
            tmp_path.unlink(missing_ok=True)
            deleted_files += 1
            freed_bytes += freed

        self.logger.info(
            f"Cache cleanup completed: {deleted_files} file(s) deleted, "
            f"{pruned_images} image(s) pruned, {freed_bytes / 1024:.1f} KB freed"
        )

        return {
            "deleted_files": deleted_files,
            "freed_bytes": freed_bytes,
            "oldest_kept": oldest_kept_date.isoformat(),
            "pruned_images": pruned_images,
        }
