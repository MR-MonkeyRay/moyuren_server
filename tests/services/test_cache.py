"""Tests for app/services/cache.py - cache cleanup service."""

import logging
import os
import time
from pathlib import Path

import pytest

from app.services.cache import CacheCleaner


class TestCacheCleaner:
    """Tests for CacheCleaner class."""

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """Create a temporary cache directory."""
        cache_dir = tmp_path / "static"
        cache_dir.mkdir()
        return cache_dir

    @pytest.fixture
    def cleaner(self, cache_dir: Path, logger: logging.Logger) -> CacheCleaner:
        """Create a CacheCleaner instance."""
        return CacheCleaner(static_dir=str(cache_dir), ttl_hours=24, logger=logger)

    def test_cleanup_no_files(self, cleaner: CacheCleaner) -> None:
        """Test cleanup with no files returns 0."""
        result = cleaner.cleanup()
        assert result == 0

    def test_cleanup_preserves_newest_file(self, cleaner: CacheCleaner, cache_dir: Path) -> None:
        """Test cleanup always preserves the newest file."""
        # Create files with different ages
        old_file = cache_dir / "moyuren_20260101.jpg"
        new_file = cache_dir / "moyuren_20260204.jpg"

        old_file.write_bytes(b"old")
        new_file.write_bytes(b"new")

        # Make old file very old
        old_time = time.time() - (48 * 3600)  # 48 hours ago
        os.utime(old_file, (old_time, old_time))

        result = cleaner.cleanup()

        assert result == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_cleanup_keeps_files_within_ttl(self, cleaner: CacheCleaner, cache_dir: Path) -> None:
        """Test cleanup keeps files within TTL."""
        # Create two recent files
        file1 = cache_dir / "moyuren_20260203.jpg"
        file2 = cache_dir / "moyuren_20260204.jpg"

        file1.write_bytes(b"file1")
        file2.write_bytes(b"file2")

        # Both files are recent (within 24 hours)
        recent_time = time.time() - (12 * 3600)  # 12 hours ago
        os.utime(file1, (recent_time, recent_time))

        result = cleaner.cleanup()

        assert result == 0
        assert file1.exists()
        assert file2.exists()

    def test_cleanup_deletes_expired_files(self, cleaner: CacheCleaner, cache_dir: Path) -> None:
        """Test cleanup deletes files older than TTL."""
        # Create multiple files
        files = []
        for i in range(3):
            f = cache_dir / f"moyuren_2026010{i}.jpg"
            f.write_bytes(b"content")
            files.append(f)

        # Make first two files expired
        expired_time = time.time() - (48 * 3600)  # 48 hours ago
        os.utime(files[0], (expired_time, expired_time))
        os.utime(files[1], (expired_time - 3600, expired_time - 3600))

        result = cleaner.cleanup()

        assert result == 2
        assert not files[0].exists()
        assert not files[1].exists()
        assert files[2].exists()  # Newest preserved

    def test_cleanup_ignores_non_moyuren_files(self, cleaner: CacheCleaner, cache_dir: Path) -> None:
        """Test cleanup ignores files not matching pattern."""
        # Create non-matching files
        other_file = cache_dir / "other_image.jpg"
        other_file.write_bytes(b"other")

        moyuren_file = cache_dir / "moyuren_20260204.jpg"
        moyuren_file.write_bytes(b"moyuren")

        result = cleaner.cleanup()

        assert result == 0
        assert other_file.exists()
        assert moyuren_file.exists()

    def test_cleanup_handles_single_file(self, cleaner: CacheCleaner, cache_dir: Path) -> None:
        """Test cleanup with single file preserves it."""
        single_file = cache_dir / "moyuren_20260204.jpg"
        single_file.write_bytes(b"single")

        # Make it old
        old_time = time.time() - (48 * 3600)
        os.utime(single_file, (old_time, old_time))

        result = cleaner.cleanup()

        # Single file should be preserved as newest
        assert result == 0
        assert single_file.exists()

    def test_cleanup_creates_directory_if_missing(self, tmp_path: Path, logger: logging.Logger) -> None:
        """Test cleaner creates directory if it doesn't exist."""
        non_existent = tmp_path / "new_static"

        CacheCleaner(static_dir=str(non_existent), ttl_hours=24, logger=logger)

        assert non_existent.exists()

    def test_cleanup_handles_permission_error(
        self, cleaner: CacheCleaner, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test cleanup handles permission errors gracefully."""
        file1 = cache_dir / "moyuren_20260101.jpg"
        file2 = cache_dir / "moyuren_20260204.jpg"

        file1.write_bytes(b"file1")
        file2.write_bytes(b"file2")

        # Make file1 old
        old_time = time.time() - (48 * 3600)
        os.utime(file1, (old_time, old_time))

        # Mock unlink to raise permission error
        original_unlink = Path.unlink

        def mock_unlink(self: Path, *args, **kwargs) -> None:
            if "20260101" in str(self):
                raise PermissionError("Permission denied")
            original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", mock_unlink)

        result = cleaner.cleanup()

        # Should handle error gracefully
        assert result == 0
        assert file1.exists()  # Failed to delete
        assert file2.exists()  # Preserved as newest
