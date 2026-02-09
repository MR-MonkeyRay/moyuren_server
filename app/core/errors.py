"""Custom exception classes and error handling."""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Error code enumeration."""

    # Config errors (1000-1099)
    CONFIG_LOAD_FAILED = "CONFIG_1001"
    CONFIG_VALIDATION_FAILED = "CONFIG_1002"
    CONFIG_MISSING_REQUIRED = "CONFIG_1003"

    # Fetch errors (2000-2099)
    FETCH_REQUEST_FAILED = "FETCH_2001"
    FETCH_TIMEOUT = "FETCH_2002"
    FETCH_INVALID_RESPONSE = "FETCH_2003"
    FETCH_API_UNAVAILABLE = "FETCH_2004"
    FETCH_STOCK_INDEX_FAILED = "FETCH_2005"
    FETCH_STOCK_CALENDAR_FAILED = "FETCH_2006"

    # Render errors (3000-3099)
    RENDER_PLAYWRIGHT_ERROR = "RENDER_3001"
    RENDER_TEMPLATE_ERROR = "RENDER_3002"
    RENDER_SAVE_FAILED = "RENDER_3003"

    # Storage errors (4000-4099)
    STORAGE_WRITE_FAILED = "STORAGE_4001"
    STORAGE_READ_FAILED = "STORAGE_4002"
    STORAGE_NOT_FOUND = "STORAGE_4003"
    STORAGE_DELETE_FAILED = "STORAGE_4004"

    # Generation errors (5000-5099)
    GENERATION_FAILED = "GENERATION_5001"
    GENERATION_BUSY = "GENERATION_5002"


class AppError(Exception):
    """Base application error."""

    def __init__(self, message: str, code: ErrorCode, detail: str | None = None) -> None:
        self.message = message
        self.code = code
        self.detail = detail
        super().__init__(message)


class ConfigError(AppError):
    """Configuration related error."""

    def __init__(
        self,
        message: str = "Configuration error",
        code: ErrorCode = ErrorCode.CONFIG_LOAD_FAILED,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, code, detail)


class FetchError(AppError):
    """Data fetching related error."""

    def __init__(
        self, message: str = "Fetch error", code: ErrorCode = ErrorCode.FETCH_REQUEST_FAILED, detail: str | None = None
    ) -> None:
        super().__init__(message, code, detail)


class RenderError(AppError):
    """Rendering related error."""

    def __init__(
        self,
        message: str = "Render error",
        code: ErrorCode = ErrorCode.RENDER_PLAYWRIGHT_ERROR,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, code, detail)


class StorageError(AppError):
    """Storage related error."""

    def __init__(
        self,
        message: str = "Storage error",
        code: ErrorCode = ErrorCode.STORAGE_WRITE_FAILED,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, code, detail)


def error_response(code: ErrorCode, message: str, detail: str | None = None) -> dict[str, Any]:
    """
    Create standardized error response.

    Args:
        code: Error code enum.
        message: Human-readable error message.
        detail: Additional error details.

    Returns:
        Dictionary with error information.
    """
    response: dict[str, Any] = {"error": {"code": code.value, "message": message}}
    if detail:
        response["error"]["detail"] = detail
    return response


# HTTP 状态码映射表
ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    # 4xx 客户端错误
    ErrorCode.CONFIG_VALIDATION_FAILED: 400,
    ErrorCode.CONFIG_MISSING_REQUIRED: 400,
    ErrorCode.STORAGE_NOT_FOUND: 404,
    # 5xx 服务端错误
    ErrorCode.CONFIG_LOAD_FAILED: 500,
    ErrorCode.FETCH_REQUEST_FAILED: 502,
    ErrorCode.FETCH_TIMEOUT: 504,
    ErrorCode.FETCH_INVALID_RESPONSE: 502,
    ErrorCode.FETCH_API_UNAVAILABLE: 503,
    ErrorCode.FETCH_STOCK_INDEX_FAILED: 502,
    ErrorCode.FETCH_STOCK_CALENDAR_FAILED: 502,
    ErrorCode.RENDER_PLAYWRIGHT_ERROR: 500,
    ErrorCode.RENDER_TEMPLATE_ERROR: 500,
    ErrorCode.RENDER_SAVE_FAILED: 500,
    ErrorCode.STORAGE_WRITE_FAILED: 500,
    ErrorCode.STORAGE_READ_FAILED: 500,
    ErrorCode.STORAGE_DELETE_FAILED: 500,
    ErrorCode.GENERATION_FAILED: 500,
    ErrorCode.GENERATION_BUSY: 503,
}


def get_http_status(code: ErrorCode) -> int:
    """获取错误码对应的 HTTP 状态码"""
    return ERROR_HTTP_STATUS.get(code, 500)
