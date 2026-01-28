"""Configuration management module."""

import os
import yaml
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field, field_validator, ConfigDict


class ServerConfig(BaseModel):
    """Server configuration."""
    host: str
    port: int
    base_domain: str


class PathsConfig(BaseModel):
    """Path configuration."""
    static_dir: str = "static"
    template_path: str = "templates/moyuren.html"
    state_path: str = "state/latest.json"


class SchedulerConfig(BaseModel):
    """Scheduler configuration."""
    daily_times: list[str] = ["06:00"]

    @field_validator("daily_times")
    @classmethod
    def validate_daily_times(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("daily_times cannot be empty")
        import re
        time_pattern = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
        result = []
        for time_str in v:
            time_str = time_str.strip()
            if not time_pattern.match(time_str):
                raise ValueError(f"Invalid time format: {time_str}, expected HH:MM")
            result.append(time_str)
        return result


class CacheConfig(BaseModel):
    """Cache configuration."""
    ttl_hours: int = 24

    @field_validator("ttl_hours")
    @classmethod
    def validate_ttl(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("ttl_hours must be positive")
        return v


class FetchEndpointConfig(BaseModel):
    """API endpoint configuration."""
    model_config = ConfigDict(extra="allow")
    name: str
    url: str
    timeout_sec: int = 10

    @field_validator("name", "url")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("cannot be empty")
        return v


class FetchConfig(BaseModel):
    """Fetch configuration."""
    api_endpoints: list[FetchEndpointConfig]


class RenderConfig(BaseModel):
    """Render configuration."""
    viewport_width: int = 794
    viewport_height: int = 1123
    device_scale_factor: int = 2
    jpeg_quality: int = 90

    @field_validator("viewport_width", "viewport_height", "device_scale_factor", "jpeg_quality")
    @classmethod
    def validate_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = "INFO"
    file: str = ""  # 空字符串表示只输出到标准输出

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"must be one of {valid_levels}")
        return v.upper()


class AppConfig(BaseModel):
    """Main application configuration."""
    server: ServerConfig
    paths: PathsConfig
    scheduler: SchedulerConfig
    cache: CacheConfig
    fetch: FetchConfig
    render: RenderConfig
    logging: LoggingConfig


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides to configuration data.

    This function recursively updates the configuration dictionary with values
    from environment variables. Environment variables are expected to be
    uppercase with underscores (e.g., SERVER_PORT, RENDER_JPEG_QUALITY).

    Args:
        data: Configuration dictionary to update.

    Returns:
        Updated configuration dictionary.
    """
    # Server configuration
    if "server" in data:
        if server_host := os.getenv("SERVER_HOST"):
            data["server"]["host"] = server_host
        if server_port := os.getenv("SERVER_PORT"):
            try:
                data["server"]["port"] = int(server_port)
            except ValueError:
                raise ValueError(f"Invalid SERVER_PORT value: {server_port}")
        if server_base_domain := os.getenv("SERVER_BASE_DOMAIN"):
            data["server"]["base_domain"] = server_base_domain

    # Scheduler configuration
    if "scheduler" in data:
        if daily_times := os.getenv("SCHEDULER_DAILY_TIMES"):
            # Support comma-separated times: "06:00,12:00,18:00"
            times = [t.strip() for t in daily_times.split(",") if t.strip()]
            if times:
                data["scheduler"]["daily_times"] = times

    # Cache configuration
    if "cache" in data:
        if ttl_hours := os.getenv("CACHE_TTL_HOURS"):
            try:
                data["cache"]["ttl_hours"] = int(ttl_hours)
            except ValueError:
                raise ValueError(f"Invalid CACHE_TTL_HOURS value: {ttl_hours}")

    # Render configuration
    if "render" in data:
        if viewport_width := os.getenv("RENDER_VIEWPORT_WIDTH"):
            try:
                data["render"]["viewport_width"] = int(viewport_width)
            except ValueError:
                raise ValueError(f"Invalid RENDER_VIEWPORT_WIDTH value: {viewport_width}")
        if viewport_height := os.getenv("RENDER_VIEWPORT_HEIGHT"):
            try:
                data["render"]["viewport_height"] = int(viewport_height)
            except ValueError:
                raise ValueError(f"Invalid RENDER_VIEWPORT_HEIGHT value: {viewport_height}")
        if device_scale_factor := os.getenv("RENDER_DEVICE_SCALE_FACTOR"):
            try:
                data["render"]["device_scale_factor"] = int(device_scale_factor)
            except ValueError:
                raise ValueError(f"Invalid RENDER_DEVICE_SCALE_FACTOR value: {device_scale_factor}")
        if jpeg_quality := os.getenv("RENDER_JPEG_QUALITY"):
            try:
                data["render"]["jpeg_quality"] = int(jpeg_quality)
            except ValueError:
                raise ValueError(f"Invalid RENDER_JPEG_QUALITY value: {jpeg_quality}")

    # Logging configuration
    if "logging" in data:
        if log_level := os.getenv("LOG_LEVEL"):
            data["logging"]["level"] = log_level
        if log_file := os.getenv("LOG_FILE"):
            data["logging"]["file"] = log_file

    # Paths configuration
    if "paths" in data:
        if static_dir := os.getenv("PATHS_STATIC_DIR"):
            data["paths"]["static_dir"] = static_dir
        if state_path := os.getenv("PATHS_STATE_PATH"):
            data["paths"]["state_path"] = state_path

    return data


def load_config(path: str = "config.yaml") -> AppConfig:
    """
    Load configuration from YAML file.

    Args:
        path: Path to the configuration file.

    Returns:
        AppConfig: Validated configuration object.

    Raises:
        FileNotFoundError: If config file does not exist.
        ValueError: If configuration is invalid.
    """
    config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    try:
        with config_path.open("r", encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML format: {e}") from e

    if not data:
        raise ValueError("Configuration file is empty")

    # Apply environment variable overrides
    data = _apply_env_overrides(data)

    try:
        return AppConfig(**data)
    except Exception as e:
        raise ValueError(f"Configuration validation failed: {e}") from e
