"""Tests for app/services/cache.py - cache cleanup service."""

import logging
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.cache import CacheCleaner


class TestCacheCleaner:
    """Tests for CacheCleaner class."""

    @pytest.fixture
    def logger(self) -> logging.Logger:
        """Create a test logger."""
        return logging.getLogger("test")

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """Create cache directory structure."""
        cache_dir = tmp_path / "cache"
        (cache_dir / "data").mkdir(parents=True)
        (cache_dir / "images").mkdir(parents=True)
        return cache_dir

    @pytest.fixture
    def cleaner(self, cache_dir: Path, logger: logging.Logger) -> CacheCleaner:
        """Create CacheCleaner instance."""
        return CacheCleaner(cache_dir=str(cache_dir), retain_days=30, logger=logger)

    @pytest.fixture
    def mock_today(self):
        """Mock today_business to return fixed date."""
        with patch("app.services.cache.today_business") as mock:
            mock.return_value = date(2026, 2, 10)
            yield mock

    def test_cleanup_no_files(self, cleaner: CacheCleaner, mock_today) -> None:
        """Test cleanup with no files returns zero stats."""
        result = cleaner.cleanup()

        assert result["deleted_files"] == 0
        assert result["freed_bytes"] == 0
        assert result["oldest_kept"] == "2026-02-10"

    def test_cleanup_expired_data_files(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """Test cleanup removes expired data files."""
        # Create expired data files (older than 30 days)
        expired_date = date(2026, 1, 1)  # 40 days ago
        expired_file = cache_dir / "data" / f"{expired_date.isoformat()}.json"
        expired_file.write_text('{"test": "data"}')

        # Create recent data file (within 30 days)
        recent_date = date(2026, 2, 1)  # 9 days ago
        recent_file = cache_dir / "data" / f"{recent_date.isoformat()}.json"
        recent_file.write_text('{"test": "data"}')

        result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert result["freed_bytes"] > 0
        assert not expired_file.exists()
        assert recent_file.exists()

    def test_cleanup_expired_image_files(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """Test cleanup removes expired image files."""
        # Create expired image file (older than 30 days)
        expired_file = cache_dir / "images" / "moyuren_20260101_060000.jpg"
        expired_file.write_bytes(b"fake image data")

        # Create recent image file (within 30 days)
        recent_file = cache_dir / "images" / "moyuren_20260201_060000.jpg"
        recent_file.write_bytes(b"fake image data")

        result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert result["freed_bytes"] > 0
        assert not expired_file.exists()
        assert recent_file.exists()

    def test_cleanup_keeps_recent_files(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """Test cleanup keeps files from today."""
        # Create today's data file
        today_data = cache_dir / "data" / "2026-02-10.json"
        today_data.write_text('{"test": "data"}')

        # Create today's image file
        today_image = cache_dir / "images" / "moyuren_20260210_072232.jpg"
        today_image.write_bytes(b"fake image data")

        result = cleaner.cleanup()

        assert result["deleted_files"] == 0
        assert result["freed_bytes"] == 0
        assert today_data.exists()
        assert today_image.exists()

    def test_cleanup_returns_correct_stats(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """Test cleanup returns correct statistics."""
        # Create multiple expired files with known sizes
        expired_data1 = cache_dir / "data" / "2025-12-01.json"
        expired_data1.write_text('{"test": "data1"}')  # ~17 bytes
        size1 = expired_data1.stat().st_size

        expired_data2 = cache_dir / "data" / "2025-12-15.json"
        expired_data2.write_text('{"test": "data2"}')  # ~17 bytes
        size2 = expired_data2.stat().st_size

        expired_image = cache_dir / "images" / "moyuren_20251201_060000.jpg"
        expired_image.write_bytes(b"x" * 1000)  # 1000 bytes
        size3 = expired_image.stat().st_size

        # Create recent file to verify oldest_kept
        recent_file = cache_dir / "data" / "2026-02-01.json"
        recent_file.write_text('{"test": "recent"}')

        result = cleaner.cleanup()

        assert result["deleted_files"] == 3
        assert result["freed_bytes"] == size1 + size2 + size3
        assert result["oldest_kept"] == "2026-02-01"

    def test_cleanup_skips_invalid_filenames(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """Test cleanup skips files with invalid name formats."""
        # Create files with invalid names
        invalid_data = cache_dir / "data" / "invalid.json"
        invalid_data.write_text('{"test": "data"}')

        invalid_image1 = cache_dir / "images" / "invalid.jpg"
        invalid_image1.write_bytes(b"fake image")

        invalid_image2 = cache_dir / "images" / "moyuren_invalid.jpg"
        invalid_image2.write_bytes(b"fake image")

        result = cleaner.cleanup()

        # All invalid files should be skipped
        assert result["deleted_files"] == 0
        assert invalid_data.exists()
        assert invalid_image1.exists()
        assert invalid_image2.exists()

    def test_cleanup_mixed_expired_and_recent(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """Test cleanup with mix of expired and recent files."""
        # Expired files
        (cache_dir / "data" / "2025-11-01.json").write_text('{"old": 1}')
        (cache_dir / "data" / "2025-12-01.json").write_text('{"old": 2}')
        (cache_dir / "images" / "moyuren_20251101_060000.jpg").write_bytes(b"old1")
        (cache_dir / "images" / "moyuren_20251201_060000.jpg").write_bytes(b"old2")

        # Recent files
        (cache_dir / "data" / "2026-02-01.json").write_text('{"new": 1}')
        (cache_dir / "data" / "2026-02-09.json").write_text('{"new": 2}')
        (cache_dir / "images" / "moyuren_20260201_060000.jpg").write_bytes(b"new1")
        (cache_dir / "images" / "moyuren_20260209_060000.jpg").write_bytes(b"new2")

        result = cleaner.cleanup()

        assert result["deleted_files"] == 4
        assert result["freed_bytes"] > 0
        assert result["oldest_kept"] == "2026-02-01"

        # Verify recent files still exist
        assert (cache_dir / "data" / "2026-02-01.json").exists()
        assert (cache_dir / "data" / "2026-02-09.json").exists()
        assert (cache_dir / "images" / "moyuren_20260201_060000.jpg").exists()
        assert (cache_dir / "images" / "moyuren_20260209_060000.jpg").exists()

        # Verify expired files are deleted
        assert not (cache_dir / "data" / "2025-11-01.json").exists()
        assert not (cache_dir / "data" / "2025-12-01.json").exists()
        assert not (cache_dir / "images" / "moyuren_20251101_060000.jpg").exists()
        assert not (cache_dir / "images" / "moyuren_20251201_060000.jpg").exists()

    def test_cleanup_with_different_retain_days(
        self, cache_dir: Path, logger: logging.Logger, mock_today
    ) -> None:
        """Test cleanup with different retain_days values."""
        # Create cleaner with 7 days retention
        cleaner = CacheCleaner(cache_dir=str(cache_dir), retain_days=7, logger=logger)

        # Create file 10 days ago (should be deleted)
        old_file = cache_dir / "data" / "2026-01-31.json"
        old_file.write_text('{"test": "old"}')

        # Create file 5 days ago (should be kept)
        recent_file = cache_dir / "data" / "2026-02-05.json"
        recent_file.write_text('{"test": "recent"}')

        result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_cleanup_handles_multiple_templates(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """Test cleanup handles images from different templates."""
        # Create expired images from different templates
        (cache_dir / "images" / "moyuren_20251201_060000.jpg").write_bytes(b"old1")
        (cache_dir / "images" / "custom_20251201_060000.jpg").write_bytes(b"old2")
        (cache_dir / "images" / "another_template_20251201_060000.jpg").write_bytes(b"old3")

        # Create recent images
        (cache_dir / "images" / "moyuren_20260201_060000.jpg").write_bytes(b"new1")
        (cache_dir / "images" / "custom_20260201_060000.jpg").write_bytes(b"new2")

        result = cleaner.cleanup()

        assert result["deleted_files"] == 3
        assert (cache_dir / "images" / "moyuren_20260201_060000.jpg").exists()
        assert (cache_dir / "images" / "custom_20260201_060000.jpg").exists()

    def test_cleanup_boundary_date(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """Test cleanup at exact boundary (30 days ago)."""
        # File exactly 30 days ago (2026-01-11) - should be kept
        boundary_file = cache_dir / "data" / "2026-01-11.json"
        boundary_file.write_text('{"test": "boundary"}')

        # File 31 days ago (2026-01-10) - should be deleted
        expired_file = cache_dir / "data" / "2026-01-10.json"
        expired_file.write_text('{"test": "expired"}')

        result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert boundary_file.exists()
        assert not expired_file.exists()
