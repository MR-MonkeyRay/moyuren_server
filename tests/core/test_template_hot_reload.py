"""Tests for throttled template discovery hot reload."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import AppConfig, TemplateRenderConfig, TemplatesConfig
from app.services.template_discovery import TemplateDiscovery


def _write_template(path: Path, description: str) -> None:
    path.write_text(
        "<html><head>"
        f'<meta name="moyuren:description" content="{description}">'
        "</head><body>preview</body></html>",
        encoding="utf-8",
    )


def _config(directory: Path, *, hot_reload: bool = True) -> AppConfig:
    templates = TemplatesConfig(
        dir=str(directory),
        hot_reload=hot_reload,
        hot_reload_interval_sec=5.0,
        config=TemplateRenderConfig(),
    )
    return AppConfig.model_construct(templates=templates)


def test_hot_reload_add_modify_delete_and_throttle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changes appear after the interval while intermediate calls do no file scan."""
    clock = [100.0]
    monkeypatch.setattr("app.core.config.time.monotonic", lambda: clock[0])
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    _write_template(first, "first")
    config = _config(tmp_path)

    assert [item.name for item in config.get_templates_config().items] == ["first"]
    _write_template(second, "second")
    with patch.object(
        TemplateDiscovery,
        "fingerprint",
        wraps=TemplateDiscovery().fingerprint,
    ) as fingerprint:
        clock[0] = 104.9
        assert [item.name for item in config.get_templates_config().items] == ["first"]
        fingerprint.assert_not_called()

    clock[0] = 105.0
    assert [item.name for item in config.get_templates_config().items] == ["first", "second"]
    _write_template(first, "modified description")
    clock[0] = 110.0
    by_name = {item.name: item for item in config.get_templates_config().items}
    assert by_name["first"].description == "modified description"
    second.unlink()
    clock[0] = 115.0
    assert [item.name for item in config.get_templates_config().items] == ["first"]


def test_hot_reload_discovery_error_keeps_previous_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed changed-directory discovery preserves the previous good snapshot."""
    clock = [100.0]
    monkeypatch.setattr("app.core.config.time.monotonic", lambda: clock[0])
    template = tmp_path / "first.html"
    _write_template(template, "first")
    config = _config(tmp_path)
    previous = config.get_templates_config().items

    template.unlink()
    clock[0] = 105.0
    assert config.get_templates_config().items == previous
    assert "keeping previous templates" in caplog.text


def test_hot_reload_invalid_changed_template_keeps_previous_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One invalid edit cannot silently remove a template from a good snapshot."""
    clock = [100.0]
    monkeypatch.setattr("app.core.config.time.monotonic", lambda: clock[0])
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    _write_template(first, "first")
    _write_template(second, "second")
    config = _config(tmp_path)
    previous = config.get_templates_config().items

    first.write_text(
        '<html><head><meta name="moyuren:viewport-width" content="bad"></head></html>',
        encoding="utf-8",
    )
    clock[0] = 105.0

    assert config.get_templates_config().items == previous


def test_hot_reload_removed_default_falls_back_to_first_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the configured default keeps the refreshed list usable."""
    clock = [100.0]
    monkeypatch.setattr("app.core.config.time.monotonic", lambda: clock[0])
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    _write_template(first, "first")
    _write_template(second, "second")
    config = _config(tmp_path)
    config.templates.default = "second"
    config.get_templates_config()

    second.unlink()
    clock[0] = 105.0

    templates = config.get_templates_config()
    assert [item.name for item in templates.items] == ["first"]
    assert templates.default == "first"


def test_disabled_hot_reload_never_rescans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabled hot reload keeps the initially discovered snapshot."""
    clock = [100.0]
    monkeypatch.setattr("app.core.config.time.monotonic", lambda: clock[0])
    _write_template(tmp_path / "first.html", "first")
    config = _config(tmp_path, hot_reload=False)
    assert [item.name for item in config.get_templates_config().items] == ["first"]

    _write_template(tmp_path / "second.html", "second")
    clock[0] = 1000.0
    with patch.object(TemplateDiscovery, "fingerprint") as fingerprint:
        assert [item.name for item in config.get_templates_config().items] == ["first"]
        fingerprint.assert_not_called()


def test_hot_reload_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested environment variables configure both hot reload settings."""
    monkeypatch.setenv("MOYUREN_TEMPLATES_HOT_RELOAD", "false")
    monkeypatch.setenv("MOYUREN_TEMPLATES_HOT_RELOAD_INTERVAL_SEC", "1.25")

    config = AppConfig.from_yaml("config.yaml")

    assert config.templates.hot_reload is False
    assert config.templates.hot_reload_interval_sec == 1.25
