"""Tests for configuration environment overrides and legacy migration."""

import logging
import os
import shutil
from pathlib import Path

import pytest
import yaml

from app.core.config import AppConfig, load_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Load the repository YAML from an isolated working directory."""
    prefixes = ("moyuren_", *(f"{name}_" for name in AppConfig.model_fields))
    for name in os.environ:
        if name.lower().startswith(prefixes):
            monkeypatch.delenv(name)
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    shutil.copyfile(config_path, tmp_path / "config.yaml")
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("use_dotenv", [False, True])
def test_unrelated_variables_are_ignored(
    use_dotenv: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Ignore unknown names even when they resemble configuration sections."""
    variables = {
        "SERVER_NAME": "foo",
        "CACHE_DIR": "/x",
        "MOYUREN_FOO": "bar",
        "MOYUREN_SERVER_NAME": "foo",
        "MOYUREN_CACHE_DIR": "/x",
        "MOYUREN_SERVER_PORT_EXTRA": "not-a-port",
        "MOYUREN_SCHEDULER_UNKNOWN": "not-json",
        "COMPOSE_PROJECT_NAME": "moyu",
        "DATA_SOURCES_URL": "not-json",
    }
    if use_dotenv:
        Path(".env").write_text(
            "\n".join(f"{name}={value}" for name, value in variables.items()),
            encoding="utf-8",
        )
    else:
        for name, value in variables.items():
            monkeypatch.setenv(name, value)

    config = load_config()

    assert config.server.port == 8000
    assert not caplog.records


@pytest.mark.parametrize("use_dotenv", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    ("name", "value", "section", "key", "expected"),
    [
        ("SERVER_PORT", "8123", "server", "port", 8123),
        ("SERVER_HOST", "127.0.0.1", "server", "host", "127.0.0.1"),
        (
            "SERVER_BASE_DOMAIN",
            "https://example.org",
            "server",
            "base_domain",
            "https://example.org",
        ),
        ("OPS_API_KEY", "secret-value", "ops", "api_key", "secret-value"),
        (
            "SCHEDULER_DAILY_TIMES",
            '["07:00","18:30"]',
            "scheduler",
            "daily_times",
            ["07:00", "18:30"],
        ),
        ("SCHEDULER_MODE", "hourly", "scheduler", "mode", "hourly"),
        ("SCHEDULER_MINUTE_OF_HOUR", "15", "scheduler", "minute_of_hour", 15),
        ("CACHE_RETAIN_DAYS", "14", "cache", "retain_days", 14),
        ("LOGGING_LEVEL", "debug", "logging", "level", "DEBUG"),
        ("LOGGING_FILE", "output.log", "logging", "file", "output.log"),
        (
            "PATHS_CACHE_DIR",
            "/tmp/moyuren-cache",
            "paths",
            "cache_dir",
            "/tmp/moyuren-cache",
        ),
        ("TIMEZONE_BUSINESS", "UTC", "timezone", "business", "UTC"),
        ("TIMEZONE_DISPLAY", "UTC", "timezone", "display", "UTC"),
        (
            "NETWORK_PROXY_URL",
            "http://proxy.example:8080",
            "network",
            "proxy_url",
            "http://proxy.example:8080",
        ),
        (
            "NETWORK_GHPROXY_URLS",
            '["https://proxy.example"]',
            "network",
            "ghproxy_urls",
            ["https://proxy.example"],
        ),
        (
            "INSTANCES_LIST_URL",
            "https://example.org/list",
            "instances",
            "list_url",
            "https://example.org/list",
        ),
        (
            "INSTANCES_URLS",
            '["https://instance.example/"]',
            "instances",
            "urls",
            ["https://instance.example"],
        ),
    ],
)
def test_known_overrides(
    name: str,
    value: str,
    section: str,
    key: str,
    expected: object,
    legacy: bool,
    use_dotenv: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Parse known nested keys and warn once only when using a legacy name."""
    variable = name if legacy else f"MOYUREN_{name}"
    if use_dotenv:
        Path(".env").write_text(f"{variable}='{value}'\n", encoding="utf-8")
    else:
        monkeypatch.setenv(variable, value)

    config = load_config()

    assert getattr(getattr(config, section), key) == expected
    if legacy:
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.name == "moyuren"
        assert record.levelno == logging.WARNING
        assert record.getMessage() == (
            f"Environment variable {name} is deprecated; use MOYUREN_{name} instead."
        )
        assert value not in record.getMessage()
    else:
        assert not caplog.records


@pytest.mark.parametrize(
    ("env", "dotenv", "expected", "warn"),
    [
        ({"MOYUREN_SERVER_PORT": "8123", "SERVER_PORT": "invalid"}, {}, 8123, False),
        ({}, {"MOYUREN_SERVER_PORT": "8123", "SERVER_PORT": "invalid"}, 8123, False),
        ({"MOYUREN_SERVER_PORT": "8123"}, {"SERVER_PORT": "invalid"}, 8123, False),
        ({"SERVER_PORT": "8124"}, {"MOYUREN_SERVER_PORT": "8125"}, 8124, True),
        ({"SERVER_PORT": "8124"}, {"SERVER_PORT": "invalid"}, 8124, True),
        ({"MOYUREN_SERVER_PORT": "8123"}, {"MOYUREN_SERVER_PORT": "8125"}, 8123, False),
        ({}, {"SERVER_PORT": "8124"}, 8124, True),
    ],
)
def test_source_precedence(
    env: dict[str, str],
    dotenv: dict[str, str],
    expected: int,
    warn: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Prefer process env to dotenv, and canonical names within each source."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    Path(".env").write_text(
        "\n".join(f"{name}={value}" for name, value in dotenv.items()),
        encoding="utf-8",
    )

    assert load_config().server.port == expected
    assert len(caplog.records) == int(warn)


def test_sources_merge_individual_fields(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Preserve lower-priority overrides for different keys in the same section."""
    monkeypatch.setenv("MOYUREN_SERVER_PORT", "8123")
    Path(".env").write_text("SERVER_HOST=127.0.0.1\n", encoding="utf-8")

    config = load_config()

    assert config.server.port == 8123
    assert config.server.host == "127.0.0.1"
    assert len(caplog.records) == 1
    assert "SERVER_HOST is deprecated" in caplog.records[0].getMessage()


@pytest.mark.parametrize("nested", [False, True])
def test_unknown_yaml_keys_remain_forbidden(nested: bool) -> None:
    """Keep strict YAML validation independent of environment filtering."""
    path = Path("config.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    target = data["server"] if nested else data
    target["unknown_setting"] = "typo"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="extra_forbidden"):
        load_config()
