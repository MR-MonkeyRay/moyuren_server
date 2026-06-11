"""Configuration management module."""

import re
import logging
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Pattern for valid template names: alphanumeric, underscore, hyphen only
_TEMPLATE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_FUN_CONTENT_ENDPOINT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_DAILY_TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_UTC_OFFSET_PATTERN = re.compile(r"^UTC([+-])(\d{1,2})(?::(\d{2}))?$", re.IGNORECASE)


class ServerConfig(BaseModel):
    """Server configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str
    port: int
    base_domain: str


class PathsConfig(BaseModel):
    """Path configuration."""

    model_config = ConfigDict(extra="forbid")

    cache_dir: str = "cache"


class SchedulerConfig(BaseModel):
    """Scheduler configuration."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["daily", "hourly"] = "daily"
    daily_times: list[str] = Field(default_factory=lambda: ["06:00"])
    minute_of_hour: int = 0

    @field_validator("daily_times")
    @classmethod
    def validate_daily_times(cls, value: list[str]) -> list[str]:
        """校验每日调度时间列表.

        Args:
            value: 配置中的 HH:MM 时间字符串列表.

        Returns:
            去除首尾空白后的时间字符串列表.

        Raises:
            ValueError: 任一时间不符合 HH:MM 格式时抛出.
        """
        if not value:
            return value
        normalized: list[str] = []
        for time_str in value:
            item = time_str.strip()
            if not _DAILY_TIME_PATTERN.match(item):
                raise ValueError(f"Invalid time format: {item}, expected HH:MM")
            normalized.append(item)
        return normalized

    @field_validator("minute_of_hour")
    @classmethod
    def validate_minute_of_hour(cls, value: int) -> int:
        """校验小时调度的分钟值.

        Args:
            value: 每小时触发任务的分钟数.

        Returns:
            通过校验的分钟数.

        Raises:
            ValueError: 分钟数不在 0 到 59 范围内时抛出.
        """
        if value < 0 or value > 59:
            raise ValueError("minute_of_hour must be between 0 and 59")
        return value

    @model_validator(mode="after")
    def validate_mode_specific(self) -> "SchedulerConfig":
        """校验调度模式相关的必填配置.

        Returns:
            当前调度配置实例.

        Raises:
            ValueError: daily 模式下未配置 daily_times 时抛出.
        """
        if self.mode == "daily" and not self.daily_times:
            raise ValueError("daily_times cannot be empty when mode is daily")
        return self


class CacheConfig(BaseModel):
    """Output cache configuration."""

    model_config = ConfigDict(extra="forbid")

    retain_days: int = 30

    @field_validator("retain_days")
    @classmethod
    def validate_retain_days(cls, value: int) -> int:
        """校验缓存保留天数.

        Args:
            value: 缓存保留天数.

        Returns:
            通过校验的保留天数.

        Raises:
            ValueError: 保留天数小于等于 0 时抛出.
        """
        if value <= 0:
            raise ValueError("retain_days must be positive")
        return value


class OpsConfig(BaseModel):
    """Operations configuration."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = ""


# ---------------------------------------------------------------------------
# Data Sources — Discriminated Union
# ---------------------------------------------------------------------------


class NetworkConfig(BaseModel):
    """Global network configuration."""

    model_config = ConfigDict(extra="forbid")

    ghproxy_urls: list[str] = Field(default_factory=list)
    proxy_url: str | None = None

    @field_validator("ghproxy_urls")
    @classmethod
    def validate_ghproxy_urls(cls, value: list[str]) -> list[str]:
        """过滤并返回合法的 GitHub 代理地址.

        Args:
            value: 配置中的代理 URL 列表.

        Returns:
            仅包含 http 或 https 且不带 query/fragment 的 URL 列表.

        Side Effects:
            对被跳过的非法 URL 写入 warning 日志.
        """
        from app.core.network import redact_url

        valid_urls: list[str] = []
        for url in value:
            if not url.startswith(("http://", "https://")):
                logging.getLogger(__name__).warning(
                    f"Invalid ghproxy_url '{redact_url(url)}': must start with http:// or https://, skipping"
                )
                continue
            if "?" in url or "#" in url:
                logging.getLogger(__name__).warning(
                    f"Invalid ghproxy_url '{redact_url(url)}': query/fragment not allowed, skipping"
                )
                continue
            valid_urls.append(url)
        return valid_urls

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy_url(cls, value: str | None) -> str | None:
        """Validate optional outbound proxy URL."""
        from app.core.network import normalize_proxy_url

        return normalize_proxy_url(value)


class DataSourceBase(BaseModel):
    """Base model for all data sources."""

    model_config = ConfigDict(extra="forbid")

    type: str
    timeout_sec: int = 10
    enabled: bool = True

    @field_validator("timeout_sec")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        """校验数据源请求超时时间.

        Args:
            value: 超时时间秒数.

        Returns:
            通过校验的超时时间.

        Raises:
            ValueError: 超时时间小于等于 0 时抛出.
        """
        if value <= 0:
            raise ValueError("timeout_sec must be positive")
        return value


class NewsSource(DataSourceBase):
    """News data source configuration."""

    type: Literal["news"] = "news"
    url: str
    params: dict[str, Any] | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """校验新闻数据源 URL 非空.

        Args:
            value: 新闻数据源 URL.

        Returns:
            原始 URL 字符串.

        Raises:
            ValueError: URL 为空时抛出.
        """
        if not value:
            raise ValueError("url cannot be empty")
        return value


class FunContentEndpoint(BaseModel):
    """Fun content API endpoint configuration."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    data_path: str
    display_title: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """校验趣味内容端点名称.

        Args:
            value: 端点名称.

        Returns:
            通过校验的端点名称.

        Raises:
            ValueError: 名称不符合小写字母开头的标识符格式时抛出.
        """
        if not _FUN_CONTENT_ENDPOINT_NAME_PATTERN.match(value):
            raise ValueError("name must match pattern ^[a-z][a-z0-9_]*$")
        return value

    @field_validator("url", "data_path", "display_title")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        """校验趣味内容端点字符串字段非空.

        Args:
            value: URL, 数据路径或展示标题.

        Returns:
            原始字符串值.

        Raises:
            ValueError: 字符串为空时抛出.
        """
        if not value:
            raise ValueError("cannot be empty")
        return value


class FunContentSource(DataSourceBase):
    """Fun content data source configuration."""

    type: Literal["fun_content"] = "fun_content"
    endpoints: list[FunContentEndpoint]

    @field_validator("endpoints")
    @classmethod
    def validate_endpoints(
        cls, value: list[FunContentEndpoint]
    ) -> list[FunContentEndpoint]:
        """校验趣味内容端点列表非空.

        Args:
            value: 趣味内容端点配置列表.

        Returns:
            原始端点配置列表.

        Raises:
            ValueError: 列表为空时抛出.
        """
        if not value:
            raise ValueError("endpoints cannot be empty")
        return value


class CrazyThursdaySource(DataSourceBase):
    """KFC crazy Thursday data source configuration."""

    type: Literal["crazy_thursday"] = "crazy_thursday"
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """校验疯狂星期四数据源 URL 非空.

        Args:
            value: 疯狂星期四数据源 URL.

        Returns:
            原始 URL 字符串.

        Raises:
            ValueError: URL 为空时抛出.
        """
        if not value:
            raise ValueError("url cannot be empty")
        return value


class HolidaySource(DataSourceBase):
    """Holiday data source configuration."""

    type: Literal["holiday"] = "holiday"

    @model_validator(mode="before")
    @classmethod
    def migrate_mirror_urls(cls, data: Any) -> Any:
        """兼容并移除已废弃的节假日镜像配置.

        Args:
            data: Pydantic 传入的原始配置数据.

        Returns:
            移除 mirror_urls 后的原始配置数据, 或非 dict 数据原样返回.

        Side Effects:
            检测到 mirror_urls 时写入弃用 warning 日志并从数据中移除该字段.
        """
        if isinstance(data, dict) and "mirror_urls" in data:
            logging.getLogger(__name__).warning(
                "HolidaySource.mirror_urls is deprecated. "
                "Use top-level network.ghproxy_urls instead."
            )
            data.pop("mirror_urls")
        return data


class SQLiteBackendConfig(BaseModel):
    """SQLite dictionary backend configuration."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["sqlite"] = "sqlite"
    db_path: str = "cache/ecdict/stardict.db"
    download_url: str = "https://github.com/skywind3000/ECDICT/releases/download/1.0.28/ecdict-sqlite-28.zip"
    checksum_sha256: str = ""


DictBackendConfig = Annotated[
    SQLiteBackendConfig,  # Future: | CsvBackendConfig | PostgresBackendConfig
    Field(discriminator="type"),
]


class DailyEnglishSource(DataSourceBase):
    """Daily English word data source configuration."""

    type: Literal["daily_english"] = "daily_english"
    enabled: bool = True
    word_api_url: str = "https://random-word-api.herokuapp.com/word"
    difficulty_range: list[int] = Field(default_factory=lambda: [3, 5])
    max_retries: int = 10
    api_failure_threshold: int = 3
    backend: DictBackendConfig = Field(default_factory=SQLiteBackendConfig)

    @field_validator("difficulty_range")
    @classmethod
    def validate_difficulty_range(cls, value: list[int]) -> list[int]:
        """校验每日英文单词难度范围.

        Args:
            value: 包含最小和最大难度的整数列表.

        Returns:
            原始难度范围列表.

        Raises:
            ValueError: 列表长度不是 2 或范围不满足 1 <= min <= max <= 5 时抛出.
        """
        if len(value) != 2:
            raise ValueError("difficulty_range must have exactly 2 elements")
        if not (1 <= value[0] <= value[1] <= 5):
            raise ValueError("difficulty_range must satisfy 1 <= min <= max <= 5")
        return value

    @field_validator("word_api_url")
    @classmethod
    def validate_word_api_url(cls, value: str) -> str:
        """校验随机单词 API 地址.

        Args:
            value: 单词 API URL.

        Returns:
            原始 URL 字符串.

        Raises:
            ValueError: URL 不以 http:// 或 https:// 开头时抛出.
        """
        if not value.startswith(("http://", "https://")):
            raise ValueError("word_api_url must start with http:// or https://")
        return value

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, value: int) -> int:
        """校验单词获取最大重试次数.

        Args:
            value: 最大重试次数.

        Returns:
            通过校验的重试次数.

        Raises:
            ValueError: 重试次数小于等于 0 时抛出.
        """
        if value <= 0:
            raise ValueError("max_retries must be positive")
        return value

    @field_validator("api_failure_threshold")
    @classmethod
    def validate_api_failure_threshold(cls, value: int) -> int:
        """校验 API 失败阈值.

        Args:
            value: 连续失败阈值.

        Returns:
            通过校验的失败阈值.

        Raises:
            ValueError: 阈值小于 1 时抛出.
        """
        if value < 1:
            raise ValueError("api_failure_threshold must be >= 1")
        return value


class StockIndexSource(DataSourceBase):
    """Stock index data source configuration."""

    type: Literal["stock_index"] = "stock_index"
    quote_url: str
    secids: list[str]
    market_timezones: dict[str, str]
    cache_ttl_sec: int = 60

    @field_validator("quote_url")
    @classmethod
    def validate_quote_url(cls, value: str) -> str:
        """校验股票指数行情 URL 非空.

        Args:
            value: 行情接口 URL.

        Returns:
            原始 URL 字符串.

        Raises:
            ValueError: URL 为空时抛出.
        """
        if not value:
            raise ValueError("quote_url cannot be empty")
        return value

    @field_validator("secids")
    @classmethod
    def validate_secids(cls, value: list[str]) -> list[str]:
        """校验股票指数 secid 列表非空.

        Args:
            value: 股票指数 secid 列表.

        Returns:
            原始 secid 列表.

        Raises:
            ValueError: 列表为空时抛出.
        """
        if not value:
            raise ValueError("secids cannot be empty")
        return value

    @field_validator("market_timezones")
    @classmethod
    def validate_market_timezones(cls, value: dict[str, str]) -> dict[str, str]:
        """校验市场时区映射非空.

        Args:
            value: 市场代码到时区名称的映射.

        Returns:
            原始市场时区映射.

        Raises:
            ValueError: 映射为空时抛出.
        """
        if not value:
            raise ValueError("market_timezones cannot be empty")
        return value

    @field_validator("cache_ttl_sec")
    @classmethod
    def validate_cache_ttl(cls, value: int) -> int:
        """校验股票指数缓存 TTL.

        Args:
            value: 缓存有效期秒数.

        Returns:
            通过校验的 TTL 秒数.

        Raises:
            ValueError: TTL 小于等于 0 时抛出.
        """
        if value <= 0:
            raise ValueError("cache_ttl_sec must be positive")
        return value


class GoldPriceSource(DataSourceBase):
    """Gold price data source configuration."""

    type: Literal["gold_price"] = "gold_price"
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """校验金价数据源 URL.

        Args:
            value: 金价数据源 URL.

        Returns:
            原始 URL 字符串.

        Raises:
            ValueError: URL 为空或不以 http:// 或 https:// 开头时抛出.
        """
        if not value:
            raise ValueError("url cannot be empty")
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


DataSource = Annotated[
    NewsSource
    | FunContentSource
    | CrazyThursdaySource
    | HolidaySource
    | StockIndexSource
    | GoldPriceSource
    | DailyEnglishSource,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class ViewportConfig(BaseModel):
    """Viewport configuration for template rendering."""

    model_config = ConfigDict(extra="forbid")

    width: int = 794
    height: int = 1123

    @field_validator("width", "height")
    @classmethod
    def validate_positive(cls, value: int) -> int:
        """校验视口尺寸为正数.

        Args:
            value: 视口宽度或高度.

        Returns:
            通过校验的尺寸值.

        Raises:
            ValueError: 尺寸小于等于 0 时抛出.
        """
        if value <= 0:
            raise ValueError("must be positive")
        return value


class TemplateRenderConfig(BaseModel):
    """Global template render configuration."""

    model_config = ConfigDict(extra="forbid")

    device_scale_factor: int = 3
    jpeg_quality: int = 100
    use_china_cdn: bool = True
    remote_resource_cache_enabled: bool = True
    remote_resource_cache_ttl_sec: int = 7 * 24 * 60 * 60
    remote_resource_timeout_sec: float = 5.0
    page_load_timeout_sec: float = 10.0
    font_ready_timeout_sec: float = 2.0
    remote_resource_max_size_kb: int = 5120

    @field_validator("device_scale_factor")
    @classmethod
    def validate_scale_positive(cls, value: int) -> int:
        """校验渲染设备缩放因子为正数.

        Args:
            value: 设备缩放因子.

        Returns:
            通过校验的缩放因子.

        Raises:
            ValueError: 缩放因子小于等于 0 时抛出.
        """
        if value <= 0:
            raise ValueError("must be positive")
        return value

    @field_validator("jpeg_quality")
    @classmethod
    def validate_jpeg_quality(cls, value: int) -> int:
        """校验全局 JPEG 质量.

        Args:
            value: JPEG 质量值.

        Returns:
            通过校验的 JPEG 质量值.

        Raises:
            ValueError: 质量值不在 1 到 100 范围内时抛出.
        """
        if value <= 0 or value > 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
        return value

    @field_validator("remote_resource_cache_ttl_sec")
    @classmethod
    def validate_remote_resource_cache_ttl(cls, value: int) -> int:
        """校验远程资源缓存 TTL.

        Args:
            value: 远程资源缓存有效期秒数.

        Returns:
            通过校验的 TTL 秒数.

        Raises:
            ValueError: TTL 小于等于 0 或超过一年时抛出.
        """
        if value <= 0:
            raise ValueError("remote_resource_cache_ttl_sec must be positive")
        if value > 365 * 24 * 60 * 60:
            raise ValueError(
                "remote_resource_cache_ttl_sec must not exceed 1 year (31536000 seconds)"
            )
        return value

    @field_validator("remote_resource_timeout_sec")
    @classmethod
    def validate_remote_resource_timeout(cls, value: float) -> float:
        """校验远程资源请求超时时间.

        Args:
            value: 超时时间秒数.

        Returns:
            通过校验的超时时间.

        Raises:
            ValueError: 超时时间小于等于 0 或超过 60 秒时抛出.
        """
        if value <= 0:
            raise ValueError("remote_resource_timeout_sec must be positive")
        if value > 60.0:
            raise ValueError("remote_resource_timeout_sec must not exceed 60.0 seconds")
        return value

    @field_validator("page_load_timeout_sec", "font_ready_timeout_sec")
    @classmethod
    def validate_render_timeout(cls, value: float, info: ValidationInfo) -> float:
        """校验浏览器渲染等待超时时间."""
        field_name = info.field_name
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")
        if value > 60.0:
            raise ValueError(f"{field_name} must not exceed 60.0 seconds")
        return value

    @field_validator("remote_resource_max_size_kb")
    @classmethod
    def validate_remote_resource_max_size(cls, value: int) -> int:
        """校验远程资源最大体积.

        Args:
            value: 最大体积, 单位 KB.

        Returns:
            通过校验的最大体积.

        Raises:
            ValueError: 体积不在 1 到 51200 KB 范围内时抛出.
        """
        if value <= 0 or value > 51200:
            raise ValueError(
                "remote_resource_max_size_kb must be between 1 and 51200 (50MB)"
            )
        return value


class TemplateItemConfig(BaseModel):
    """Single template configuration."""

    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    viewport: ViewportConfig
    device_scale_factor: int | None = None
    jpeg_quality: int | None = None
    show_kfc: bool = True
    show_stock: bool = True
    show_daily_english: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """校验模板名称.

        Args:
            value: 模板名称.

        Returns:
            通过校验的模板名称.

        Raises:
            ValueError: 名称为空或包含非法字符时抛出.
        """
        if not value:
            raise ValueError("name cannot be empty")
        if not _TEMPLATE_NAME_PATTERN.match(value):
            raise ValueError(
                "name must contain only alphanumeric characters, underscores, and hyphens"
            )
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """校验模板路径非空.

        Args:
            value: 模板文件路径.

        Returns:
            原始模板路径.

        Raises:
            ValueError: 路径为空时抛出.
        """
        if not value:
            raise ValueError("path cannot be empty")
        return value

    @field_validator("jpeg_quality")
    @classmethod
    def validate_jpeg_quality(cls, value: int | None) -> int | None:
        """校验单个模板的 JPEG 质量覆盖值.

        Args:
            value: 模板级 JPEG 质量值, 未设置时为 None.

        Returns:
            原始质量值或 None.

        Raises:
            ValueError: 质量值不在 1 到 100 范围内时抛出.
        """
        if value is None:
            return value
        if value <= 0 or value > 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
        return value


class TemplatesConfig(BaseModel):
    """Multi-template configuration."""

    model_config = ConfigDict(extra="forbid")

    default: str | None = None
    dir: str = "templates"
    config: TemplateRenderConfig = Field(default_factory=TemplateRenderConfig)
    items: list[TemplateItemConfig] = Field(default_factory=list, exclude=True)

    @field_validator("items")
    @classmethod
    def validate_items(
        cls, value: list[TemplateItemConfig]
    ) -> list[TemplateItemConfig]:
        """校验模板配置项名称唯一.

        Args:
            value: 模板配置项列表.

        Returns:
            原始模板配置项列表.

        Raises:
            ValueError: 存在重复模板名称时抛出.
        """
        names = [item.name for item in value]
        if len(set(names)) != len(names):
            raise ValueError("template names must be unique")
        return value

    def get_template(self, name: str | None = None) -> TemplateItemConfig:
        """按名称获取模板配置.

        Args:
            name: 指定模板名称. 未提供时使用 default, 再退回第一个模板.

        Returns:
            匹配到的模板配置项.

        Raises:
            ValueError: 模板列表为空或目标模板不存在时抛出.
        """
        if not self.items:
            raise ValueError("templates.items cannot be empty")
        resolved_name = name or self.default or self.items[0].name
        for item in self.items:
            if item.name == resolved_name:
                return item
        raise ValueError(f"Template not found: {resolved_name}")


# ---------------------------------------------------------------------------
# Logging / Timezone
# ---------------------------------------------------------------------------


class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    file: str = ""

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        """校验并规范化日志级别.

        Args:
            value: 配置中的日志级别.

        Returns:
            大写形式的日志级别.

        Raises:
            ValueError: 日志级别不在允许集合内时抛出.
        """
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if value.upper() not in valid_levels:
            raise ValueError(f"must be one of {valid_levels}")
        return value.upper()


class TimezoneConfig(BaseModel):
    """Timezone configuration."""

    model_config = ConfigDict(extra="forbid")

    business: str = "Asia/Shanghai"
    display: str = "local"

    @field_validator("business", "display")
    @classmethod
    def validate_timezone(cls, value: str, info: ValidationInfo) -> str:
        """校验业务时区和展示时区配置.

        Args:
            value: 时区名称, UTC 偏移量或 display 字段允许的 local.
            info: Pydantic 字段校验上下文.

        Returns:
            通过校验的时区配置值.

        Raises:
            ValueError: 时区名称或 UTC 偏移量非法时抛出.
        """
        if value.lower() == "local":
            if info.field_name == "display":
                return "local"
            raise ValueError(
                f"Field '{info.field_name}' does not accept 'local' value, must be a specific timezone name"
            )

        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(value)
            return value
        except Exception:  # nosec B110 - intentional fallback to UTC offset parsing
            pass

        match = _UTC_OFFSET_PATTERN.match(value)
        if match:
            hours = int(match.group(2))
            minutes = int(match.group(3) or 0)
            if hours > 14 or minutes > 59 or (hours == 14 and minutes > 0):
                raise ValueError(
                    f"Invalid timezone offset: {value} (hours must be 0-14, minutes 0-59)"
                )
            return value

        raise ValueError(f"Invalid timezone: {value}")


# ---------------------------------------------------------------------------
# Top-level AppConfig
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=DataSourceBase)


class AppConfig(BaseSettings):
    """Main application configuration."""

    model_config = SettingsConfigDict(
        extra="forbid",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    server: ServerConfig
    paths: PathsConfig
    scheduler: SchedulerConfig
    cache: CacheConfig
    ops: OpsConfig = Field(default_factory=OpsConfig)
    templates: TemplatesConfig
    data_sources: list[DataSource]
    logging: LoggingConfig
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    timezone: TimezoneConfig = Field(default_factory=TimezoneConfig)

    @model_validator(mode="after")
    def validate_unique_source_types(self) -> "AppConfig":
        """校验数据源类型唯一.

        Returns:
            当前应用配置实例.

        Raises:
            ValueError: 存在重复的数据源类型时抛出.
        """
        source_types = [source.type for source in self.data_sources]
        if len(set(source_types)) != len(source_types):
            raise ValueError("data_sources types must be unique")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """定义应用配置来源优先级.

        Args:
            settings_cls: Pydantic settings 类.
            init_settings: 初始化参数配置源.
            env_settings: 环境变量配置源.
            dotenv_settings: .env 文件配置源.
            file_secret_settings: 文件 secret 配置源.

        Returns:
            按优先级排列的配置源元组.
        """
        return env_settings, dotenv_settings, init_settings, file_secret_settings

    def get_source(self, source_type: type[T]) -> T | None:
        """按配置类型获取已启用的数据源.

        Args:
            source_type: 目标数据源配置类.

        Returns:
            匹配且 enabled 为 True 的数据源配置, 否则返回 None.
        """
        for source in self.data_sources:
            if isinstance(source, source_type):
                return source if source.enabled else None
        return None

    def get_templates_config(self) -> TemplatesConfig:
        """获取模板配置并按需发现模板文件.

        Returns:
            已填充 items 且 default 合法的模板配置.

        Raises:
            ValueError: default 指向不存在的模板名称时抛出.

        Side Effects:
            当 templates.items 为空时执行模板发现并写回 self.templates.items.
        """
        if not self.templates.items:
            from app.services.template_discovery import TemplateDiscovery

            discovery = TemplateDiscovery()
            items = discovery.discover(self.templates.dir, self.templates.config)
            self.templates.items = items
        # 验证 default（无条件执行）
        if self.templates.default:
            names = [item.name for item in self.templates.items]
            if self.templates.default not in names:
                raise ValueError(
                    f"default template '{self.templates.default}' not found: {names}"
                )
        return self.templates

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "AppConfig":
        """从 YAML 文件加载并校验应用配置.

        Args:
            path: 配置文件路径.

        Returns:
            应用配置实例.

        Raises:
            FileNotFoundError: 配置文件不存在时抛出.
            ValueError: YAML 格式非法, 内容为空或配置校验失败时抛出.
        """
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as err:
            raise ValueError(f"Invalid YAML format: {err}") from err

        if not isinstance(data, dict) or not data:
            raise ValueError("Configuration file is empty")

        try:
            return cls(**data)
        except ValidationError as err:
            raise ValueError(f"Configuration validation failed: {err}") from err


def load_config(path: str = "config.yaml") -> AppConfig:
    """Load and validate application configuration."""
    return AppConfig.from_yaml(path)
