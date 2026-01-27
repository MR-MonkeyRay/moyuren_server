"""Custom exception classes and error handling."""

from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
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

    # Render errors (3000-3099)
    RENDER_PLAYWRIGHT_ERROR = "RENDER_3001"
    RENDER_TEMPLATE_ERROR = "RENDER_3002"
    RENDER_SAVE_FAILED = "RENDER_3003"

    # Storage errors (4000-4099)
    STORAGE_WRITE_FAILED = "STORAGE_4001"
    STORAGE_READ_FAILED = "STORAGE_4002"
    STORAGE_NOT_FOUND = "STORAGE_4003"
    STORAGE_DELETE_FAILED = "STORAGE_4004"


class AppError(Exception):
    """Base application error."""

    def __init__(
        self,
        message: str,
        code: ErrorCode,
        detail: Optional[str] = None
    ) -> None:
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
        detail: Optional[str] = None
    ) -> None:
        super().__init__(message, code, detail)


class FetchError(AppError):
    """Data fetching related error."""

    def __init__(
        self,
        message: str = "Fetch error",
        code: ErrorCode = ErrorCode.FETCH_REQUEST_FAILED,
        detail: Optional[str] = None
    ) -> None:
        super().__init__(message, code, detail)


class RenderError(AppError):
    """Rendering related error."""

    def __init__(
        self,
        message: str = "Render error",
        code: ErrorCode = ErrorCode.RENDER_PLAYWRIGHT_ERROR,
        detail: Optional[str] = None
    ) -> None:
        super().__init__(message, code, detail)


class StorageError(AppError):
    """Storage related error."""

    def __init__(
        self,
        message: str = "Storage error",
        code: ErrorCode = ErrorCode.STORAGE_WRITE_FAILED,
        detail: Optional[str] = None
    ) -> None:
        super().__init__(message, code, detail)


def error_response(
    code: ErrorCode,
    message: str,
    detail: Optional[str] = None
) -> dict[str, Any]:
    """
    Create standardized error response.

    Args:
        code: Error code enum.
        message: Human-readable error message.
        detail: Additional error details.

    Returns:
        Dictionary with error information.
    """
    response: dict[str, Any] = {
        "error": {
            "code": code.value,
            "message": message
        }
    }
    if detail:
        response["error"]["detail"] = detail
    return response
