"""Tests for app/core/errors.py - custom exception classes."""

import pytest

from app.core.errors import (
    AppError,
    ConfigError,
    ErrorCode,
    FetchError,
    RenderError,
    StorageError,
    error_response,
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

    def test_auth_error_codes_in_6000_range(self) -> None:
        """Test auth error codes are in 6000-6099 range."""
        assert ErrorCode.AUTH_UNAUTHORIZED.value == "AUTH_6001"

    def test_api_error_codes_in_7000_range(self) -> None:
        """Test API error codes are in 7000-7099 range."""
        assert ErrorCode.API_INVALID_DATE.value == "API_7001"
        assert ErrorCode.API_INVALID_ENCODE.value == "API_7002"
        assert ErrorCode.API_INVALID_PARAMETER.value == "API_7003"
        assert ErrorCode.API_TEMPLATE_NOT_FOUND.value == "API_7004"
        assert ErrorCode.API_DATA_NOT_FOUND.value == "API_7005"

    def test_ops_error_codes_in_8000_range(self) -> None:
        """Test ops error codes are in 8000-8099 range."""
        assert ErrorCode.OPS_CACHE_CLEAN_FAILED.value == "OPS_8001"

    def test_error_code_str_representation_matches_value(self) -> None:
        """StrEnum should stringify to raw code value."""
        assert str(ErrorCode.CONFIG_LOAD_FAILED) == "CONFIG_1001"
        assert isinstance(ErrorCode.CONFIG_LOAD_FAILED, str)


class TestAppError:
    """Tests for AppError base class."""

    def test_app_error_attributes(self) -> None:
        """Test AppError has correct attributes."""
        error = AppError(message="Test error", code=ErrorCode.CONFIG_LOAD_FAILED)

        assert error.message == "Test error"
        assert error.code == ErrorCode.CONFIG_LOAD_FAILED
        assert str(error) == "Test error"

    def test_app_error_is_exception(self) -> None:
        """Test AppError is an Exception."""
        error = AppError(message="Test error", code=ErrorCode.CONFIG_LOAD_FAILED)

        assert isinstance(error, Exception)

    def test_app_error_can_be_raised(self) -> None:
        """Test AppError can be raised and caught."""
        with pytest.raises(AppError) as exc_info:
            raise AppError(message="Test error", code=ErrorCode.CONFIG_LOAD_FAILED)

        assert exc_info.value.message == "Test error"


class TestConfigError:
    """Tests for ConfigError class."""

    def test_config_error_default_values(self) -> None:
        """Test ConfigError has correct default values."""
        error = ConfigError()

        assert error.message == "Configuration error"
        assert error.code == ErrorCode.CONFIG_LOAD_FAILED

    def test_config_error_custom_values(self) -> None:
        """Test ConfigError with custom values."""
        error = ConfigError(
            message="Custom config error", code=ErrorCode.CONFIG_VALIDATION_FAILED
        )

        assert error.message == "Custom config error"
        assert error.code == ErrorCode.CONFIG_VALIDATION_FAILED

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

    def test_fetch_error_custom_values(self) -> None:
        """Test FetchError with custom values."""
        error = FetchError(message="API timeout", code=ErrorCode.FETCH_TIMEOUT)

        assert error.message == "API timeout"
        assert error.code == ErrorCode.FETCH_TIMEOUT

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

    def test_render_error_custom_values(self) -> None:
        """Test RenderError with custom values."""
        error = RenderError(message="Template error", code=ErrorCode.RENDER_TEMPLATE_ERROR)

        assert error.message == "Template error"
        assert error.code == ErrorCode.RENDER_TEMPLATE_ERROR

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

    def test_storage_error_custom_values(self) -> None:
        """Test StorageError with custom values."""
        error = StorageError(message="File not found", code=ErrorCode.STORAGE_NOT_FOUND)

        assert error.message == "File not found"
        assert error.code == ErrorCode.STORAGE_NOT_FOUND

    def test_storage_error_is_app_error(self) -> None:
        """Test StorageError is an AppError."""
        error = StorageError()
        assert isinstance(error, AppError)


class TestErrorResponse:
    """Tests for error_response function."""

    def test_error_response_basic(self) -> None:
        """Test basic error response."""
        response = error_response(ErrorCode.CONFIG_LOAD_FAILED, "Failed to load config")

        assert response == {
            "error": {
                "code": "CONFIG_1001",
                "message": "Failed to load config",
            }
        }

    def test_error_response_with_different_code(self) -> None:
        """Test error response with different error code."""
        response = error_response(ErrorCode.FETCH_TIMEOUT, "Request timed out")

        assert response == {
            "error": {
                "code": "FETCH_2002",
                "message": "Request timed out",
            }
        }
