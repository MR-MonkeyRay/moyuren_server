"""Cache cleanup service module."""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from app.services.calendar import today_business


class CacheCleaner:
    """Cache cleaner for removing expired data, image, and render resource files."""

    def __init__(
        self,
        cache_dir: str,
        retain_days: int,
        logger: logging.Logger,
    ) -> None:
        """Initialize the cache cleaner.

        Args:
            cache_dir: Root cache directory containing data/, images/, and render_resources/ subdirectories.
            retain_days: Number of days to retain cached files.
            logger: Logger instance for logging cleanup status.
        """
        self.cache_dir = Path(cache_dir)
        self.data_dir = self.cache_dir / "data"
        self.images_dir = self.cache_dir / "images"
        self.render_resources_dir = self.cache_dir / "render_resources"
        self.retain_days = retain_days
        self.logger = logger

        # Ensure directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.render_resources_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self, retain_days: int | None = None) -> dict[str, int | str]:
        """Clean up expired data and image files.

        Scans cache/data/, cache/images/, and cache/render_resources/ directories and removes files
        older than retain_days.

        Data files format: YYYY-MM-DD.json
        Image files format: {template}_{YYYYMMDD}_{HHMMSS}.jpg
        Render resources: sha256-keyed .body/.meta pairs with JSON metadata

        Returns:
            Dictionary with cleanup statistics:
            - deleted_files: Number of files deleted
            - freed_bytes: Total bytes freed
            - oldest_kept: Date string of oldest kept file (YYYY-MM-DD)
        """
        effective_retain = retain_days if retain_days is not None else self.retain_days
        today = today_business()
        cutoff_date = today - timedelta(days=effective_retain)

        deleted_files = 0
        freed_bytes = 0
        oldest_kept_date = today

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
        image_pattern = re.compile(r"^[A-Za-z0-9_-]+_(\d{8})_\d{6}\.jpg$")
        for file_path in self.images_dir.glob("*.jpg"):
            match = image_pattern.match(file_path.name)
            if not match:
                self.logger.warning(f"Skipping file with invalid name format: {file_path.name}")
                continue

            try:
                date_str = match.group(1)
                file_date = datetime.strptime(date_str, "%Y%m%d").date()
                if file_date < cutoff_date:
                    file_size = file_path.stat().st_size
                    file_path.unlink()
                    deleted_files += 1
                    freed_bytes += file_size
                    self.logger.info(f"Deleted expired image file: {file_path.name}")
                else:
                    if file_date < oldest_kept_date:
                        oldest_kept_date = file_date
            except Exception as e:
                self.logger.warning(f"Failed to process image file {file_path.name}: {e}")

        # Clean render resource cache files
        cutoff_timestamp = datetime.combine(cutoff_date, datetime.min.time()).timestamp()
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
            f"{freed_bytes / 1024:.1f} KB freed"
        )

        return {
            "deleted_files": deleted_files,
            "freed_bytes": freed_bytes,
            "oldest_kept": oldest_kept_date.isoformat(),
        }
