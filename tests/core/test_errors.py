"""测试 app/core/errors.py 中的自定义异常类。"""

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
    """测试 ErrorCode 枚举。"""

    def test_config_error_codes_in_1000_range(self) -> None:
        """验证配置错误码位于 1000-1099 范围内。"""
        assert ErrorCode.CONFIG_LOAD_FAILED.value == "CONFIG_1001"
        assert ErrorCode.CONFIG_VALIDATION_FAILED.value == "CONFIG_1002"
        assert ErrorCode.CONFIG_MISSING_REQUIRED.value == "CONFIG_1003"

    def test_fetch_error_codes_in_2000_range(self) -> None:
        """验证获取错误码位于 2000-2099 范围内。"""
        assert ErrorCode.FETCH_REQUEST_FAILED.value == "FETCH_2001"
        assert ErrorCode.FETCH_TIMEOUT.value == "FETCH_2002"
        assert ErrorCode.FETCH_INVALID_RESPONSE.value == "FETCH_2003"

    def test_render_error_codes_in_3000_range(self) -> None:
        """验证渲染错误码位于 3000-3099 范围内。"""
        assert ErrorCode.RENDER_PLAYWRIGHT_ERROR.value == "RENDER_3001"
        assert ErrorCode.RENDER_TEMPLATE_ERROR.value == "RENDER_3002"
        assert ErrorCode.RENDER_SAVE_FAILED.value == "RENDER_3003"

    def test_storage_error_codes_in_4000_range(self) -> None:
        """验证存储错误码位于 4000-4099 范围内。"""
        assert ErrorCode.STORAGE_WRITE_FAILED.value == "STORAGE_4001"
        assert ErrorCode.STORAGE_READ_FAILED.value == "STORAGE_4002"
        assert ErrorCode.STORAGE_NOT_FOUND.value == "STORAGE_4003"

    def test_generation_error_codes_in_5000_range(self) -> None:
        """验证生成错误码位于 5000-5099 范围内。"""
        assert ErrorCode.GENERATION_FAILED.value == "GENERATION_5001"
        assert ErrorCode.GENERATION_BUSY.value == "GENERATION_5002"

    def test_auth_error_codes_in_6000_range(self) -> None:
        """验证鉴权错误码位于 6000-6099 范围内。"""
        assert ErrorCode.AUTH_UNAUTHORIZED.value == "AUTH_6001"

    def test_api_error_codes_in_7000_range(self) -> None:
        """验证 API 错误码位于 7000-7099 范围内。"""
        assert ErrorCode.API_INVALID_DATE.value == "API_7001"
        assert ErrorCode.API_INVALID_ENCODE.value == "API_7002"
        assert ErrorCode.API_INVALID_PARAMETER.value == "API_7003"
        assert ErrorCode.API_TEMPLATE_NOT_FOUND.value == "API_7004"
        assert ErrorCode.API_DATA_NOT_FOUND.value == "API_7005"

    def test_ops_error_codes_in_8000_range(self) -> None:
        """验证运维错误码位于 8000-8099 范围内。"""
        assert ErrorCode.OPS_CACHE_CLEAN_FAILED.value == "OPS_8001"

    def test_error_code_str_representation_matches_value(self) -> None:
        """验证 StrEnum 转为字符串时返回原始错误码值。"""
        assert str(ErrorCode.CONFIG_LOAD_FAILED) == "CONFIG_1001"
        assert isinstance(ErrorCode.CONFIG_LOAD_FAILED, str)


class TestAppError:
    """测试 AppError 基类。"""

    def test_app_error_attributes(self) -> None:
        """验证 AppError 的属性正确。"""
        error = AppError(message="Test error", code=ErrorCode.CONFIG_LOAD_FAILED)

        assert error.message == "Test error"
        assert error.code == ErrorCode.CONFIG_LOAD_FAILED
        assert str(error) == "Test error"

    def test_app_error_is_exception(self) -> None:
        """验证 AppError 是 Exception 的实例。"""
        error = AppError(message="Test error", code=ErrorCode.CONFIG_LOAD_FAILED)

        assert isinstance(error, Exception)

    def test_app_error_can_be_raised(self) -> None:
        """验证 AppError 可以抛出和捕获。"""
        with pytest.raises(AppError) as exc_info:
            raise AppError(message="Test error", code=ErrorCode.CONFIG_LOAD_FAILED)

        assert exc_info.value.message == "Test error"


class TestConfigError:
    """测试 ConfigError 类。"""

    def test_config_error_default_values(self) -> None:
        """验证 ConfigError 的默认值正确。"""
        error = ConfigError()

        assert error.message == "Configuration error"
        assert error.code == ErrorCode.CONFIG_LOAD_FAILED

    def test_config_error_custom_values(self) -> None:
        """测试 ConfigError 使用自定义值。"""
        error = ConfigError(
            message="Custom config error", code=ErrorCode.CONFIG_VALIDATION_FAILED
        )

        assert error.message == "Custom config error"
        assert error.code == ErrorCode.CONFIG_VALIDATION_FAILED

    def test_config_error_is_app_error(self) -> None:
        """验证 ConfigError 是 AppError 的实例。"""
        error = ConfigError()
        assert isinstance(error, AppError)


class TestFetchError:
    """测试 FetchError 类。"""

    def test_fetch_error_default_values(self) -> None:
        """验证 FetchError 的默认值正确。"""
        error = FetchError()

        assert error.message == "Fetch error"
        assert error.code == ErrorCode.FETCH_REQUEST_FAILED

    def test_fetch_error_custom_values(self) -> None:
        """测试 FetchError 使用自定义值。"""
        error = FetchError(message="API timeout", code=ErrorCode.FETCH_TIMEOUT)

        assert error.message == "API timeout"
        assert error.code == ErrorCode.FETCH_TIMEOUT

    def test_fetch_error_is_app_error(self) -> None:
        """验证 FetchError 是 AppError 的实例。"""
        error = FetchError()
        assert isinstance(error, AppError)


class TestRenderError:
    """测试 RenderError 类。"""

    def test_render_error_default_values(self) -> None:
        """验证 RenderError 的默认值正确。"""
        error = RenderError()

        assert error.message == "Render error"
        assert error.code == ErrorCode.RENDER_PLAYWRIGHT_ERROR

    def test_render_error_custom_values(self) -> None:
        """测试 RenderError 使用自定义值。"""
        error = RenderError(message="Template error", code=ErrorCode.RENDER_TEMPLATE_ERROR)

        assert error.message == "Template error"
        assert error.code == ErrorCode.RENDER_TEMPLATE_ERROR

    def test_render_error_is_app_error(self) -> None:
        """验证 RenderError 是 AppError 的实例。"""
        error = RenderError()
        assert isinstance(error, AppError)


class TestStorageError:
    """测试 StorageError 类。"""

    def test_storage_error_default_values(self) -> None:
        """验证 StorageError 的默认值正确。"""
        error = StorageError()

        assert error.message == "Storage error"
        assert error.code == ErrorCode.STORAGE_WRITE_FAILED

    def test_storage_error_custom_values(self) -> None:
        """测试 StorageError 使用自定义值。"""
        error = StorageError(message="File not found", code=ErrorCode.STORAGE_NOT_FOUND)

        assert error.message == "File not found"
        assert error.code == ErrorCode.STORAGE_NOT_FOUND

    def test_storage_error_is_app_error(self) -> None:
        """验证 StorageError 是 AppError 的实例。"""
        error = StorageError()
        assert isinstance(error, AppError)


class TestErrorResponse:
    """测试 error_response 函数。"""

    def test_error_response_basic(self) -> None:
        """测试基本错误响应。"""
        response = error_response(ErrorCode.CONFIG_LOAD_FAILED, "Failed to load config")

        assert response == {
            "error": {
                "code": "CONFIG_1001",
                "message": "Failed to load config",
            }
        }

    def test_error_response_with_different_code(self) -> None:
        """测试使用不同错误码的错误响应。"""
        response = error_response(ErrorCode.FETCH_TIMEOUT, "Request timed out")

        assert response == {
            "error": {
                "code": "FETCH_2002",
                "message": "Request timed out",
            }
        }
