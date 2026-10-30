"""自定义异常类与错误处理。"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """错误码枚举。"""

    # 配置错误（1000-1099）
    CONFIG_LOAD_FAILED = "CONFIG_1001"
    CONFIG_VALIDATION_FAILED = "CONFIG_1002"
    CONFIG_MISSING_REQUIRED = "CONFIG_1003"

    # 数据获取错误（2000-2099）
    FETCH_REQUEST_FAILED = "FETCH_2001"
    FETCH_TIMEOUT = "FETCH_2002"
    FETCH_INVALID_RESPONSE = "FETCH_2003"
    FETCH_API_UNAVAILABLE = "FETCH_2004"
    FETCH_STOCK_INDEX_FAILED = "FETCH_2005"
    FETCH_STOCK_CALENDAR_FAILED = "FETCH_2006"

    # 渲染错误（3000-3099）
    RENDER_PLAYWRIGHT_ERROR = "RENDER_3001"
    RENDER_TEMPLATE_ERROR = "RENDER_3002"
    RENDER_SAVE_FAILED = "RENDER_3003"

    # 存储错误（4000-4099）
    STORAGE_WRITE_FAILED = "STORAGE_4001"
    STORAGE_READ_FAILED = "STORAGE_4002"
    STORAGE_NOT_FOUND = "STORAGE_4003"
    STORAGE_DELETE_FAILED = "STORAGE_4004"

    # 生成错误（5000-5099）
    GENERATION_FAILED = "GENERATION_5001"
    GENERATION_BUSY = "GENERATION_5002"

    # 鉴权错误（6000-6099）
    AUTH_UNAUTHORIZED = "AUTH_6001"

    # API 错误（7000-7099）
    API_INVALID_DATE = "API_7001"
    API_INVALID_ENCODE = "API_7002"
    API_INVALID_PARAMETER = "API_7003"
    API_TEMPLATE_NOT_FOUND = "API_7004"
    API_DATA_NOT_FOUND = "API_7005"

    # Ops 运维错误（8000-8099）
    OPS_CACHE_CLEAN_FAILED = "OPS_8001"


class AppError(Exception):
    """应用基础异常。"""

    def __init__(self, message: str, code: ErrorCode) -> None:
        """初始化应用基础异常.

        Args:
            message: 面向调用方或日志的人类可读错误信息.
            code: 应用内部标准错误码.
        """
        self.message = message
        self.code = code
        super().__init__(message)


class ConfigError(AppError):
    """配置相关错误。"""

    def __init__(
        self,
        message: str = "Configuration error",
        code: ErrorCode = ErrorCode.CONFIG_LOAD_FAILED,
    ) -> None:
        """初始化配置相关异常.

        Args:
            message: 配置错误说明.
            code: 配置错误码.
        """
        super().__init__(message, code)


class FetchError(AppError):
    """数据获取相关错误。"""

    def __init__(
        self, message: str = "Fetch error", code: ErrorCode = ErrorCode.FETCH_REQUEST_FAILED
    ) -> None:
        """初始化数据获取相关异常.

        Args:
            message: 数据获取错误说明.
            code: 数据获取错误码.
        """
        super().__init__(message, code)


class WebhookDeliveryError(FetchError):
    """Webhook 推送在 HTTP 或服务商响应层面失败。"""

    def __init__(self, message: str, status_code: int) -> None:
        """保留 HTTP 状态码，但不保留可能含密钥的请求对象。"""
        super().__init__(message)
        self.status_code = status_code


class RenderError(AppError):
    """渲染相关错误。"""

    def __init__(
        self,
        message: str = "Render error",
        code: ErrorCode = ErrorCode.RENDER_PLAYWRIGHT_ERROR,
    ) -> None:
        """初始化渲染相关异常.

        Args:
            message: 渲染错误说明.
            code: 渲染错误码.
        """
        super().__init__(message, code)


class RenderResourceRedirectError(RenderError):
    """渲染资源重定向被拦截或超出配置上限。"""


class StorageError(AppError):
    """存储相关错误。"""

    def __init__(
        self,
        message: str = "Storage error",
        code: ErrorCode = ErrorCode.STORAGE_WRITE_FAILED,
    ) -> None:
        """初始化存储相关异常.

        Args:
            message: 存储错误说明.
            code: 存储错误码.
        """
        super().__init__(message, code)


class GenerationBusyError(Exception):
    """生成任务正在进行中"""


def error_response(code: ErrorCode, message: str) -> dict[str, Any]:
    """
    构造标准化错误响应。

    Args:
        code: 错误码枚举。
        message: 人类可读的错误信息。

    Returns:
        包含错误信息的字典。
    """
    return {"error": {"code": code.value, "message": message}}


# HTTP 状态码映射表
ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    # 4xx 客户端错误
    ErrorCode.CONFIG_VALIDATION_FAILED: 400,
    ErrorCode.CONFIG_MISSING_REQUIRED: 400,
    ErrorCode.STORAGE_NOT_FOUND: 404,
    ErrorCode.AUTH_UNAUTHORIZED: 401,
    ErrorCode.API_INVALID_DATE: 400,
    ErrorCode.API_INVALID_ENCODE: 400,
    ErrorCode.API_INVALID_PARAMETER: 400,
    ErrorCode.API_TEMPLATE_NOT_FOUND: 404,
    ErrorCode.API_DATA_NOT_FOUND: 404,
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
    ErrorCode.OPS_CACHE_CLEAN_FAILED: 500,
}


def get_http_status(code: ErrorCode) -> int:
    """获取错误码对应的 HTTP 状态码"""
    return ERROR_HTTP_STATUS.get(code, 500)
