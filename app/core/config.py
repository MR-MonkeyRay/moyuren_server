"""Configuration management module."""

import re
import logging
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

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
        if value < 0 or value > 59:
            raise ValueError("minute_of_hour must be between 0 and 59")
        return value

    @model_validator(mode="after")
    def validate_mode_specific(self) -> "SchedulerConfig":
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

    @field_validator("ghproxy_urls")
    @classmethod
    def validate_ghproxy_urls(cls, value: list[str]) -> list[str]:
        valid_urls: list[str] = []
        for url in value:
            if not url.startswith(("http://", "https://")):
                logging.getLogger(__name__).warning(
                    f"Invalid ghproxy_url '{url}': must start with http:// or https://, skipping"
                )
                continue
            if "?" in url or "#" in url:
                logging.getLogger(__name__).warning(
                    f"Invalid ghproxy_url '{url}': query/fragment not allowed, skipping"
                )
                continue
            valid_urls.append(url)
        return valid_urls


class DataSourceBase(BaseModel):
    """Base model for all data sources."""

    model_config = ConfigDict(extra="forbid")

    type: str
    timeout_sec: int = 10
    enabled: bool = True

    @field_validator("timeout_sec")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
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
        if not _FUN_CONTENT_ENDPOINT_NAME_PATTERN.match(value):
            raise ValueError("name must match pattern ^[a-z][a-z0-9_]*$")
        return value

    @field_validator("url", "data_path", "display_title")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("cannot be empty")
        return value


class FunContentSource(DataSourceBase):
    """Fun content data source configuration."""

    type: Literal["fun_content"] = "fun_content"
    endpoints: list[FunContentEndpoint]

    @field_validator("endpoints")
    @classmethod
    def validate_endpoints(cls, value: list[FunContentEndpoint]) -> list[FunContentEndpoint]:
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
        if not value:
            raise ValueError("url cannot be empty")
        return value


class HolidaySource(DataSourceBase):
    """Holiday data source configuration."""

    type: Literal["holiday"] = "holiday"

    @model_validator(mode="before")
    @classmethod
    def migrate_mirror_urls(cls, data: Any) -> Any:
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
    download_url: str = (
        "https://github.com/skywind3000/ECDICT/releases/download/1.0.28/ecdict-sqlite-28.zip"
    )
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
        if len(value) != 2:
            raise ValueError("difficulty_range must have exactly 2 elements")
        if not (1 <= value[0] <= value[1] <= 5):
            raise ValueError("difficulty_range must satisfy 1 <= min <= max <= 5")
        return value

    @field_validator("word_api_url")
    @classmethod
    def validate_word_api_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("word_api_url must start with http:// or https://")
        return value

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_retries must be positive")
        return value

    @field_validator("api_failure_threshold")
    @classmethod
    def validate_api_failure_threshold(cls, value: int) -> int:
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
        if not value:
            raise ValueError("quote_url cannot be empty")
        return value

    @field_validator("secids")
    @classmethod
    def validate_secids(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("secids cannot be empty")
        return value

    @field_validator("market_timezones")
    @classmethod
    def validate_market_timezones(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("market_timezones cannot be empty")
        return value

    @field_validator("cache_ttl_sec")
    @classmethod
    def validate_cache_ttl(cls, value: int) -> int:
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
        if value <= 0:
            raise ValueError("must be positive")
        return value


class TemplateRenderConfig(BaseModel):
    """Global template render configuration."""

    model_config = ConfigDict(extra="forbid")

    device_scale_factor: int = 3
    jpeg_quality: int = 100
    use_china_cdn: bool = True

    @field_validator("device_scale_factor")
    @classmethod
    def validate_scale_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be positive")
        return value

    @field_validator("jpeg_quality")
    @classmethod
    def validate_jpeg_quality(cls, value: int) -> int:
        if value <= 0 or value > 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
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
        if not value:
            raise ValueError("name cannot be empty")
        if not _TEMPLATE_NAME_PATTERN.match(value):
            raise ValueError("name must contain only alphanumeric characters, underscores, and hyphens")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value:
            raise ValueError("path cannot be empty")
        return value

    @field_validator("jpeg_quality")
    @classmethod
    def validate_jpeg_quality(cls, value: int | None) -> int | None:
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
    def validate_items(cls, value: list[TemplateItemConfig]) -> list[TemplateItemConfig]:
        names = [item.name for item in value]
        if len(set(names)) != len(names):
            raise ValueError("template names must be unique")
        return value

    def get_template(self, name: str | None = None) -> TemplateItemConfig:
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
    def validate_timezone(cls, value: str, info) -> str:
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
                raise ValueError(f"Invalid timezone offset: {value} (hours must be 0-14, minutes 0-59)")
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
        return env_settings, dotenv_settings, init_settings, file_secret_settings

    def get_source(self, source_type: type[T]) -> T | None:
        for source in self.data_sources:
            if isinstance(source, source_type):
                return source if source.enabled else None
        return None

    def get_templates_config(self) -> TemplatesConfig:
        if not self.templates.items:
            from app.services.template_discovery import TemplateDiscovery
            discovery = TemplateDiscovery()
            items = discovery.discover(self.templates.dir, self.templates.config)
            self.templates.items = items
        # 验证 default（无条件执行）
        if self.templates.default:
            names = [item.name for item in self.templates.items]
            if self.templates.default not in names:
                raise ValueError(f"default template '{self.templates.default}' not found: {names}")
        return self.templates

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "AppConfig":
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
