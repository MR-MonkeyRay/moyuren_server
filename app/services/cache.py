"""Cache cleanup service module."""

import logging
import os
from pathlib import Path


class CacheCleaner:
    """Cache cleaner for removing expired image files."""

    def __init__(
        self,
        static_dir: str,
        ttl_hours: int,
        logger: logging.Logger,
    ) -> None:
        """Initialize the cache cleaner.

        Args:
            static_dir: Directory containing cached image files.
            ttl_hours: Time-to-live in hours for cached files.
            logger: Logger instance for logging cleanup status.
        """
        self.static_dir = Path(static_dir)
        self.ttl_hours = ttl_hours
        self.logger = logger

        # Ensure static directory exists
        self.static_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> int:
        """Clean up expired image files from static directory.

        Scans for moyuren_*.jpg files and removes those that exceed the TTL,
        while always preserving the newest file.

        Returns:
            The number of files deleted.
        """
        import time

        # Find all moyuren_*.jpg files
        pattern = "moyuren_*.jpg"
        files = list(self.static_dir.glob(pattern))

        if not files:
            self.logger.debug("No cache files found for cleanup")
            return 0

        # Sort by modification time (oldest first)
        files.sort(key=lambda f: os.path.getmtime(f))

        # Keep the newest file regardless of age
        candidates = files[:-1]  # Exclude newest from deletion

        # Calculate cutoff time
        cutoff_time = time.time() - (self.ttl_hours * 3600)

        deleted_count = 0
        for file_path in candidates:
            try:
                mtime = os.path.getmtime(file_path)
                if mtime < cutoff_time:
                    file_path.unlink()
                    deleted_count += 1
                    self.logger.info(f"Deleted expired cache file: {file_path.name}")
                else:
                    self.logger.debug(f"File within TTL, keeping: {file_path.name}")
            except Exception as e:
                self.logger.warning(f"Failed to delete {file_path.name}: {e}")

        self.logger.info(f"Cache cleanup completed: {deleted_count} file(s) deleted")
        return deleted_count
