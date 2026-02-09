import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from filelock import Timeout

from app.services.generator import (
    GenerationBusyError,
    _get_async_lock,
    _read_latest_filename,
    generate_and_save_image,
)


class TestGenerationBusyError:
    """Tests for GenerationBusyError exception."""

    def test_is_exception(self) -> None:
        """Test GenerationBusyError is an Exception."""
        error = GenerationBusyError("busy")
        assert isinstance(error, Exception)

    def test_can_be_raised(self) -> None:
        """Test GenerationBusyError can be raised."""
        with pytest.raises(GenerationBusyError):
            raise GenerationBusyError("Generation in progress")


class TestGetAsyncLock:
    """Tests for _get_async_lock function."""

    def test_returns_lock(self) -> None:
        """Test returns asyncio.Lock."""
        import asyncio
        lock = _get_async_lock()
        assert isinstance(lock, asyncio.Lock)

    def test_returns_same_lock(self) -> None:
        """Test returns same lock instance (singleton)."""
        lock1 = _get_async_lock()
        lock2 = _get_async_lock()
        assert lock1 is lock2


class TestReadLatestFilename:
    """Tests for _read_latest_filename function."""

    def test_reads_v1_state(self, tmp_path: Path) -> None:
        """Test reads filename from v1 state."""
        state_path = tmp_path / "latest.json"
        state_data = {
            "filename": "moyuren_20260204.jpg",
            "date": "2026-02-04",
        }
        state_path.write_text(json.dumps(state_data))

        result = _read_latest_filename(state_path)

        assert result == "moyuren_20260204.jpg"

    def test_reads_v2_state(self, tmp_path: Path) -> None:
        """Test reads filename from v2 state."""
        state_path = tmp_path / "latest.json"
        state_data = {
            "version": 2,
            "filename": "moyuren_20260204.jpg",
            "templates": {
                "moyuren": {
                    "filename": "moyuren_20260204.jpg",
                }
            },
        }
        state_path.write_text(json.dumps(state_data))

        result = _read_latest_filename(state_path)

        assert result == "moyuren_20260204.jpg"

    def test_reads_v2_state_with_template_name(self, tmp_path: Path) -> None:
        """Test reads filename from v2 state with template name."""
        state_path = tmp_path / "latest.json"
        state_data = {
            "version": 2,
            "templates": {
                "moyuren": {"filename": "moyuren_20260204.jpg"},
                "custom": {"filename": "custom_20260204.jpg"},
            },
        }
        state_path.write_text(json.dumps(state_data))

        result = _read_latest_filename(state_path, template_name="custom")

        assert result == "custom_20260204.jpg"

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """Test returns None for missing file."""
        state_path = tmp_path / "nonexistent.json"

        result = _read_latest_filename(state_path)

        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        """Test returns None for invalid JSON."""
        state_path = tmp_path / "latest.json"
        state_path.write_text("not valid json")

        result = _read_latest_filename(state_path)

        assert result is None

    def test_returns_none_for_non_dict(self, tmp_path: Path) -> None:
        """Test returns None for non-dict JSON."""
        state_path = tmp_path / "latest.json"
        state_path.write_text(json.dumps(["array", "not", "dict"]))

        result = _read_latest_filename(state_path)

        assert result is None

    def test_returns_none_for_missing_template(self, tmp_path: Path) -> None:
        """Test returns None for missing template in v2 state."""
        state_path = tmp_path / "latest.json"
        state_data = {
            "version": 2,
            "templates": {
                "moyuren": {"filename": "moyuren_20260204.jpg"},
            },
        }
        state_path.write_text(json.dumps(state_data))

        result = _read_latest_filename(state_path, template_name="nonexistent")

        assert result is None

    def test_handles_migration_error(self, tmp_path: Path) -> None:
        """Test handles migration error gracefully."""
        state_path = tmp_path / "latest.json"
        state_data = {
            "version": 99,  # Unsupported version
            "filename": "test.jpg",
        }
        state_path.write_text(json.dumps(state_data))

        # Should not raise, returns filename from original data
        result = _read_latest_filename(state_path)

        assert result == "test.jpg"


class TestGenerationBusyErrorExceptionChain:
    """Tests for exception chaining in generate_and_save_image."""

    @staticmethod
    def _build_app(tmp_path: Path) -> MagicMock:
        """Build a minimal mock FastAPI app for generate_and_save_image."""
        app = MagicMock()
        app.state.logger = MagicMock()

        state_path = tmp_path / "state" / "latest.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        template_item = MagicMock()
        template_item.name = "moyuren"
        templates_config = MagicMock()
        templates_config.get_template.return_value = template_item

        config = MagicMock()
        config.paths.state_path = str(state_path)
        config.get_templates_config.return_value = templates_config
        app.state.config = config

        return app

    @pytest.mark.asyncio
    async def test_busy_error_preserves_async_timeout_cause(
        self, tmp_path: Path
    ) -> None:
        """Test generate_and_save_image preserves asyncio.TimeoutError as __cause__."""
        app = self._build_app(tmp_path)

        # Make async_lock.acquire() hang so wait_for raises TimeoutError
        never_done: asyncio.Future[bool] = asyncio.Future()

        mock_lock = MagicMock()
        mock_lock.acquire = MagicMock(return_value=never_done)

        with (
            patch("app.services.generator._get_async_lock", return_value=mock_lock),
            pytest.raises(GenerationBusyError) as exc_info,
        ):
            await generate_and_save_image(app)

        assert isinstance(exc_info.value.__cause__, asyncio.TimeoutError)

    @pytest.mark.asyncio
    async def test_busy_error_preserves_filelock_timeout_cause(
        self, tmp_path: Path
    ) -> None:
        """Test generate_and_save_image preserves filelock.Timeout as __cause__."""
        app = self._build_app(tmp_path)

        with patch("app.services.generator._get_async_lock", return_value=asyncio.Lock()), patch(
            "app.services.generator.asyncio.to_thread",
            new=AsyncMock(side_effect=Timeout("lock")),
        ), pytest.raises(GenerationBusyError) as exc_info:
            await generate_and_save_image(app)

        assert isinstance(exc_info.value.__cause__, Timeout)
