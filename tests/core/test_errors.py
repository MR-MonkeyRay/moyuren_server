"""Tests for app/core/errors.py - custom exception classes."""

import pytest

from app.core.errors import (
    AppError,
    ConfigError,
    ErrorCode,
    FetchError,
    RenderError,
    StorageError,
)


class TestErrorCode:
    """Tests for ErrorCode enumeration."""

    def test_config_error_codes_in_1000_range(self) -> None:
        """Test config error codes are in 1000-1099 range."""
        assert ErrorCode.CONFIG_LOAD_FAILED.value == "CONFIG_1001"
        assert ErrorCode.CONFIG_VALIDATION_FAILED.value == "CONFIG_1002"
        assert ErrorCode.CONFIG_MISSING_REQUIRED.value == "CONFIG_1003"

    def test_fetch_error_codes_in_2000_range(self) -> None:
        """Test fetch error codes are in 2000-2099 range."""
        assert ErrorCode.FETCH_REQUEST_FAILED.value == "FETCH_2001"
        assert ErrorCode.FETCH_TIMEOUT.value == "FETCH_2002"
        assert ErrorCode.FETCH_INVALID_RESPONSE.value == "FETCH_2003"

    def test_render_error_codes_in_3000_range(self) -> None:
        """Test render error codes are in 3000-3099 range."""
        assert ErrorCode.RENDER_PLAYWRIGHT_ERROR.value == "RENDER_3001"
        assert ErrorCode.RENDER_TEMPLATE_ERROR.value == "RENDER_3002"
        assert ErrorCode.RENDER_SAVE_FAILED.value == "RENDER_3003"

    def test_storage_error_codes_in_4000_range(self) -> None:
        """Test storage error codes are in 4000-4099 range."""
        assert ErrorCode.STORAGE_WRITE_FAILED.value == "STORAGE_4001"
        assert ErrorCode.STORAGE_READ_FAILED.value == "STORAGE_4002"
        assert ErrorCode.STORAGE_NOT_FOUND.value == "STORAGE_4003"

    def test_generation_error_codes_in_5000_range(self) -> None:
        """Test generation error codes are in 5000-5099 range."""
        assert ErrorCode.GENERATION_FAILED.value == "GENERATION_5001"
        assert ErrorCode.GENERATION_BUSY.value == "GENERATION_5002"

    def test_error_code_str_representation_matches_value(self) -> None:
        """StrEnum should stringify to raw code value."""
        assert str(ErrorCode.CONFIG_LOAD_FAILED) == "CONFIG_1001"
        assert isinstance(ErrorCode.CONFIG_LOAD_FAILED, str)


class TestAppError:
    """Tests for AppError base class."""

    def test_app_error_attributes(self) -> None:
        """Test AppError has correct attributes."""
        error = AppError(
            message="Test error",
            code=ErrorCode.CONFIG_LOAD_FAILED,
            detail="Additional detail"
        )

        assert error.message == "Test error"
        assert error.code == ErrorCode.CONFIG_LOAD_FAILED
        assert error.detail == "Additional detail"
        assert str(error) == "Test error"

    def test_app_error_without_detail(self) -> None:
        """Test AppError without detail."""
        error = AppError(
            message="Test error",
            code=ErrorCode.CONFIG_LOAD_FAILED
        )

        assert error.detail is None

    def test_app_error_is_exception(self) -> None:
        """Test AppError is an Exception."""
        error = AppError(
            message="Test error",
            code=ErrorCode.CONFIG_LOAD_FAILED
        )

        assert isinstance(error, Exception)

    def test_app_error_can_be_raised(self) -> None:
        """Test AppError can be raised and caught."""
        with pytest.raises(AppError) as exc_info:
            raise AppError(
                message="Test error",
                code=ErrorCode.CONFIG_LOAD_FAILED
            )

        assert exc_info.value.message == "Test error"


class TestConfigError:
    """Tests for ConfigError class."""

    def test_config_error_default_values(self) -> None:
        """Test ConfigError has correct default values."""
        error = ConfigError()

        assert error.message == "Configuration error"
        assert error.code == ErrorCode.CONFIG_LOAD_FAILED
        assert error.detail is None

    def test_config_error_custom_values(self) -> None:
        """Test ConfigError with custom values."""
        error = ConfigError(
            message="Custom config error",
            code=ErrorCode.CONFIG_VALIDATION_FAILED,
            detail="Validation failed"
        )

        assert error.message == "Custom config error"
        assert error.code == ErrorCode.CONFIG_VALIDATION_FAILED
        assert error.detail == "Validation failed"

    def test_config_error_is_app_error(self) -> None:
        """Test ConfigError is an AppError."""
        error = ConfigError()
        assert isinstance(error, AppError)


class TestFetchError:
    """Tests for FetchError class."""

    def test_fetch_error_default_values(self) -> None:
        """Test FetchError has correct default values."""
        error = FetchError()

        assert error.message == "Fetch error"
        assert error.code == ErrorCode.FETCH_REQUEST_FAILED
        assert error.detail is None

    def test_fetch_error_custom_values(self) -> None:
        """Test FetchError with custom values."""
        error = FetchError(
            message="API timeout",
            code=ErrorCode.FETCH_TIMEOUT,
            detail="Request timed out after 10s"
        )

        assert error.message == "API timeout"
        assert error.code == ErrorCode.FETCH_TIMEOUT
        assert error.detail == "Request timed out after 10s"

    def test_fetch_error_is_app_error(self) -> None:
        """Test FetchError is an AppError."""
        error = FetchError()
        assert isinstance(error, AppError)


class TestRenderError:
    """Tests for RenderError class."""

    def test_render_error_default_values(self) -> None:
        """Test RenderError has correct default values."""
        error = RenderError()

        assert error.message == "Render error"
        assert error.code == ErrorCode.RENDER_PLAYWRIGHT_ERROR
        assert error.detail is None

    def test_render_error_custom_values(self) -> None:
        """Test RenderError with custom values."""
        error = RenderError(
            message="Template error",
            code=ErrorCode.RENDER_TEMPLATE_ERROR,
            detail="Missing variable"
        )

        assert error.message == "Template error"
        assert error.code == ErrorCode.RENDER_TEMPLATE_ERROR
        assert error.detail == "Missing variable"

    def test_render_error_is_app_error(self) -> None:
        """Test RenderError is an AppError."""
        error = RenderError()
        assert isinstance(error, AppError)


class TestStorageError:
    """Tests for StorageError class."""

    def test_storage_error_default_values(self) -> None:
        """Test StorageError has correct default values."""
        error = StorageError()

        assert error.message == "Storage error"
        assert error.code == ErrorCode.STORAGE_WRITE_FAILED
        assert error.detail is None

    def test_storage_error_custom_values(self) -> None:
        """Test StorageError with custom values."""
        error = StorageError(
            message="File not found",
            code=ErrorCode.STORAGE_NOT_FOUND,
            detail="state/latest.json"
        )

        assert error.message == "File not found"
        assert error.code == ErrorCode.STORAGE_NOT_FOUND
        assert error.detail == "state/latest.json"

    def test_storage_error_is_app_error(self) -> None:
        """Test StorageError is an AppError."""
        error = StorageError()
        assert isinstance(error, AppError)
