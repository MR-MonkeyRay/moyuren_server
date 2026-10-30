"""Webhook 通知配置测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import AppConfig, NotifyConfig, WebhookConfig


def test_notify_defaults() -> None:
    """通知默认关闭，启用后默认仅定时生成触发。"""
    config = NotifyConfig()

    assert config.enabled is False
    assert config.once_per_day is True
    assert config.triggers == ["scheduled"]
    assert config.timeout_sec == 10.0
    assert config.webhooks == []


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"url": "https://example.com/hook", "url_env": "HOOK_URL"}, "exactly one"),
        ({}, "exactly one"),
        (
            {
                "url": "https://example.com/hook",
                "secret": "direct-secret",
                "secret_env": "HOOK_SECRET",
            },
            "mutually exclusive",
        ),
    ],
)
def test_webhook_value_and_environment_sources_are_exclusive(
    values: dict[str, str], message: str
) -> None:
    """URL 必须指定一个来源；可选密钥最多指定一个来源。"""
    with pytest.raises(ValidationError, match=message):
        WebhookConfig(name="test", type="generic", **values)


def test_webhook_resolves_environment_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量中的 URL 和密钥在运行时作为敏感值保护。"""
    monkeypatch.setenv("TEST_NOTIFY_URL", "https://example.com/hook/token-value")
    monkeypatch.setenv("TEST_NOTIFY_SECRET", "secret-value")

    config = WebhookConfig(
        name="test",
        type="generic",
        url_env="TEST_NOTIFY_URL",
        secret_env="TEST_NOTIFY_SECRET",
    )

    assert isinstance(config.url, SecretStr)
    assert config.url.get_secret_value() == "https://example.com/hook/token-value"
    assert isinstance(config.secret, SecretStr)
    assert config.secret.get_secret_value() == "secret-value"
    assert "token-value" not in repr(config)
    assert "secret-value" not in repr(config)
    assert "token-value" not in repr(config.model_dump())
    assert "secret-value" not in repr(config.model_dump())
    assert "token-value" not in config.model_dump_json()
    assert "secret-value" not in config.model_dump_json()
    assert "**********" in repr(config)


@pytest.mark.parametrize("env_name", ["ABSENT_NOTIFY_URL", "ABSENT_NOTIFY_SECRET"])
def test_missing_environment_variable_names_variable_without_value(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    """启动错误指出缺失的变量名，不泄露其他值。"""
    monkeypatch.delenv(env_name, raising=False)
    values: dict[str, str] = {"url": "https://example.com/hook"}
    if env_name.endswith("URL"):
        values = {"url_env": env_name}
    else:
        values["secret_env"] = env_name

    with pytest.raises(ValidationError) as exc_info:
        WebhookConfig(name="test", type="generic", **values)

    message = str(exc_info.value)
    assert env_name in message
    assert "https://example.com/hook" not in message


def test_app_config_reports_missing_webhook_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YAML 启动校验不在上层错误中泄露敏感值。"""
    env_name = "MISSING_T28_WEBHOOK_URL"
    monkeypatch.delenv(env_name, raising=False)
    source = Path(__file__).resolve().parents[2] / "config.yaml"
    content = source.read_text(encoding="utf-8")
    content += f"\nnotify:\n  webhooks:\n    - name: test\n      type: generic\n      url_env: {env_name}\n"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        AppConfig.from_yaml(str(config_path))

    assert env_name in str(exc_info.value)


def test_webhook_names_must_be_unique() -> None:
    """名称是稳定的指标和日志标签，因此不允许重复。"""
    with pytest.raises(ValidationError, match="webhook names must be unique"):
        NotifyConfig(
            webhooks=[
                WebhookConfig(name="same", type="wecom", url="https://one.example/hook"),
                WebhookConfig(name="same", type="feishu", url="https://two.example/hook"),
            ]
        )


def test_telegram_requires_chat_id() -> None:
    """Telegram sendPhoto 需要聊天 ID 才能指定接收方。"""
    with pytest.raises(ValidationError, match="chat_id is required"):
        WebhookConfig(
            name="telegram", type="telegram", url="https://api.telegram.org/botTOKEN"
        )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/hook",
        "example.com/hook",
        "https:///missing-host",
        "http://:80/hook",
        "http://host:bad/hook",
        "http://host:99999/hook",
    ],
)
def test_webhook_rejects_non_http_url(url: str) -> None:
    """Webhook 目标必须是绝对 HTTP(S) URL。"""
    with pytest.raises(ValidationError, match=r"valid http\(s\)"):
        WebhookConfig(name="test", type="generic", url=url)


def test_telegram_rejects_blank_chat_id() -> None:
    """纯空白不满足 Telegram 的聊天 ID 要求。"""
    with pytest.raises(ValidationError, match="chat_id is required"):
        WebhookConfig(
            name="telegram",
            type="telegram",
            url="https://api.telegram.org/botTOKEN",
            chat_id="  ",
        )


def test_notify_timeout_must_be_positive() -> None:
    """请求超时时间不能为零或负数。"""
    with pytest.raises(ValidationError):
        NotifyConfig(timeout_sec=0)

