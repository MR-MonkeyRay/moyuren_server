"""Webhook 投递、重试、状态与凭证安全测试。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import NotifyConfig, WebhookConfig
from app.core.metrics import registry
from app.services.notifier import WebhookNotifier


NOW = 1_777_777_777.25
DATA: dict[str, Any] = {
    "date": "2026-05-08",
    "weekday": "星期五",
    "lunar_date": "农历三月廿二",
    "updated_at": "2026-05-08T06:00:00+08:00",
    "is_stale": False,
}


def _config(
    webhook_type: str = "generic",
    *,
    name: str = "primary",
    url: str = "https://hooks.example.test/token-value",
    secret: str | None = None,
    chat_id: str | None = None,
    templates: list[str] | None = None,
    once_per_day: bool = False,
    triggers: list[str] | None = None,
) -> NotifyConfig:
    values: dict[str, Any] = {
        "name": name,
        "type": webhook_type,
        "url": url,
        "templates": templates,
    }
    if secret is not None:
        values["secret"] = secret
    if chat_id is not None:
        values["chat_id"] = chat_id
    return NotifyConfig(
        enabled=True,
        once_per_day=once_per_day,
        triggers=triggers or ["scheduled", "manual", "startup"],
        webhooks=[WebhookConfig(**values)],
    )


def _notifier(
    tmp_path: Path,
    config: NotifyConfig,
    handler: httpx.AsyncBaseTransport,
    *,
    logger: logging.Logger | None = None,
) -> WebhookNotifier:
    client = httpx.AsyncClient(transport=handler)
    return WebhookNotifier(
        config=config,
        cache_dir=tmp_path,
        base_domain="https://calendar.example.test/",
        logger=logger or logging.getLogger("test.notifier"),
        http_client=client,
    )


def _metric(name: str, result: str) -> float:
    return registry.get_sample_value(
        "moyuren_notify_total", {"webhook": name, "result": result}
    ) or 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("webhook_type", ["wecom", "feishu", "dingtalk", "telegram", "generic"])
async def test_provider_payloads_and_signatures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    webhook_type: str,
) -> None:
    """各平台收到的请求体、端点与签名符合文档约定。"""
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        responses = {
            "wecom": {"errcode": 0},
            "feishu": {"code": 0},
            "dingtalk": {"errcode": 0},
            "telegram": {"ok": True},
            "generic": {},
        }
        return httpx.Response(200, json=responses[webhook_type])

    monkeypatch.setattr("app.services.notifier.time.time", lambda: NOW)
    secret = "signing-secret"
    config = _config(
        webhook_type,
        secret=secret,
        chat_id="-100123" if webhook_type == "telegram" else None,
    )
    notifier = _notifier(tmp_path, config, httpx.MockTransport(handle))
    await notifier.notify({"classic": "calendar.png"}, DATA, trigger="scheduled")

    assert len(requests) == 1
    request = requests[0]
    body = json.loads(request.content)
    image_url = "https://calendar.example.test/static/calendar.png"
    title = "摸鱼日历 2026-05-08 星期五"
    if webhook_type == "wecom":
        assert body == {
            "msgtype": "news",
            "news": {"articles": [{
                "title": title,
                "description": f"{title}\n农历三月廿二",
                "url": image_url,
                "picurl": image_url,
            }]},
        }
    elif webhook_type == "feishu":
        timestamp = str(int(NOW))
        expected_sign = base64.b64encode(
            hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
        assert body == {
            "msg_type": "post",
            "content": {"post": {"zh_cn": {
                "title": title,
                "content": [[
                    {"tag": "text", "text": "农历三月廿二"},
                    {"tag": "a", "text": "查看日历", "href": image_url},
                ]],
            }}},
            "timestamp": timestamp,
            "sign": expected_sign,
        }
    elif webhook_type == "dingtalk":
        timestamp = str(int(NOW * 1000))
        expected_sign = base64.b64encode(
            hmac.new(
                secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256
            ).digest()
        ).decode()
        query = parse_qs(urlsplit(str(request.url)).query)
        assert query["timestamp"] == [timestamp]
        assert query["sign"] == [expected_sign]
        assert body == {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"### {title}\n![]({image_url})\n农历三月廿二",
            },
        }
    elif webhook_type == "telegram":
        assert request.url.path.endswith("/token-value/sendPhoto")
        assert body == {"chat_id": "-100123", "photo": image_url, "caption": f"{title}\n农历三月廿二"}
    else:
        expected = {
            "event": "moyuren.generated",
            "date": "2026-05-08",
            "template": "classic",
            "image_url": image_url,
            "updated_at": DATA["updated_at"],
            "is_stale": False,
        }
        expected_raw = json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode()
        signed = f"{int(NOW)}.".encode() + expected_raw
        expected_hmac = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        assert request.content == expected_raw
        assert request.headers["X-Moyuren-Timestamp"] == str(int(NOW))
        assert request.headers["X-Moyuren-Signature"] == f"sha256={expected_hmac}"

    await notifier.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("webhook_type", "response_body"),
    [
        ("wecom", {"errcode": 40013, "errmsg": "bad token-value"}),
        ("feishu", {"code": 9499, "msg": "bad token-value"}),
        ("dingtalk", {"errcode": 310000, "errmsg": "bad token-value"}),
        ("telegram", {"ok": False, "description": "bad token-value"}),
    ],
)
async def test_provider_error_body_is_failure_without_raising(
    tmp_path: Path, webhook_type: str, response_body: dict[str, Any]
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=response_body)

    name = f"provider-error-{webhook_type}"
    before = _metric(name, "failure")
    notifier = _notifier(
        tmp_path,
        _config(webhook_type, name=name, chat_id="1" if webhook_type == "telegram" else None),
        httpx.MockTransport(handle),
    )
    await notifier.notify({"classic": "calendar.png"}, DATA)

    assert calls == 1
    assert _metric(name, "failure") == before + 1


@pytest.mark.asyncio
async def test_stale_note_is_in_message_text(tmp_path: Path) -> None:
    """面向用户的平台消息标明数据源已过期。"""
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"errcode": 0})

    notifier = _notifier(tmp_path, _config("wecom"), httpx.MockTransport(handle))
    await notifier.notify(
        {"classic": "calendar.png"}, {**DATA, "is_stale": True}, trigger="scheduled"
    )

    description = bodies[0]["news"]["articles"][0]["description"]
    assert "农历三月廿二" in description
    assert "部分数据为缓存（非当日数据）" in description


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected_calls"), [(503, 2), (400, 1)])
async def test_http_retry_policy(
    tmp_path: Path, status: int, expected_calls: int
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={})

    notifier = _notifier(tmp_path, _config(), httpx.MockTransport(handle))
    await notifier.notify({"classic": "calendar.png"}, DATA)
    assert calls == expected_calls


@pytest.mark.asyncio
async def test_transport_error_retried_once_and_never_propagates(tmp_path: Path) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection failed token-value", request=request)

    notifier = _notifier(tmp_path, _config(), httpx.MockTransport(handle))
    await notifier.notify({"classic": "calendar.png"}, DATA)
    assert calls == 2


@pytest.mark.asyncio
async def test_template_and_trigger_filters_record_skips(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    name = "filtered-hook"
    before = _metric(name, "skipped")
    config = _config(name=name, templates=["classic"], triggers=["scheduled"])
    notifier = _notifier(tmp_path, config, httpx.MockTransport(handle))
    await notifier.notify({"classic": "one.png", "compact": "two.png"}, DATA, trigger="scheduled")
    await notifier.notify({"classic": "one.png"}, DATA, trigger="manual")

    assert [json.loads(request.content)["template"] for request in requests] == ["classic"]
    assert _metric(name, "skipped") == before + 2


@pytest.mark.asyncio
async def test_once_per_day_survives_restart_and_concurrent_calls(tmp_path: Path) -> None:
    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json={})

    config = _config(once_per_day=True)
    transport = httpx.MockTransport(handle)
    first = _notifier(tmp_path, config, transport)
    await asyncio.gather(
        first.notify({"classic": "one.png"}, DATA),
        first.notify({"classic": "two.png"}, {**DATA, "updated_at": "later"}),
    )
    state = json.loads((tmp_path / "notify_state.json").read_text())
    assert state["date"] == DATA["date"]
    assert state["sent"]["primary"]["classic"] == DATA["updated_at"]

    restarted = _notifier(tmp_path, config, transport)
    await restarted.notify({"classic": "three.png"}, {**DATA, "updated_at": "latest"})
    assert calls == 1


@pytest.mark.asyncio
async def test_once_per_day_false_resends(tmp_path: Path) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    notifier = _notifier(tmp_path, _config(once_per_day=False), httpx.MockTransport(handle))
    await notifier.notify({"classic": "one.png"}, DATA)
    await notifier.notify({"classic": "two.png"}, DATA)
    assert calls == 2


@pytest.mark.asyncio
async def test_corrupt_state_is_empty_and_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "notify_state.json").write_text("not json", encoding="utf-8")
    logger = logging.getLogger("test.notifier.corrupt")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with caplog.at_level(logging.WARNING, logger=logger.name):
        notifier = _notifier(
            tmp_path, _config(once_per_day=True), httpx.MockTransport(handle), logger=logger
        )
        await notifier.notify({"classic": "one.png"}, DATA)
        await notifier.notify({"classic": "two.png"}, DATA)

    assert caplog.messages.count("Notification state missing or invalid; starting empty") == 1


@pytest.mark.asyncio
async def test_failure_log_redacts_url_token_secret_and_provider_body(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    token = "super-private-token"
    secret = "super-private-secret"
    logger = logging.getLogger("test.notifier.redaction")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"{token} {secret}")

    notifier = _notifier(
        tmp_path,
        _config(url=f"https://hooks.example.test/{token}", secret=secret),
        httpx.MockTransport(handle),
        logger=logger,
    )
    with caplog.at_level(logging.INFO):
        await notifier.notify({"classic": "one.png"}, DATA)

    assert token not in caplog.text
    assert secret not in caplog.text
    assert "webhook=primary" in caplog.text
    assert "type=generic" in caplog.text
    assert "status=500" in caplog.text


@pytest.mark.asyncio
async def test_client_ownership_close_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    monkeypatch.setattr("app.services.notifier.create_async_client", lambda **kwargs: owned)
    notifier = WebhookNotifier(
        _config(), tmp_path, "https://calendar.example.test", logging.getLogger("test.owned")
    )
    await notifier.notify({"classic": "one.png"}, DATA)
    await notifier.close()
    assert owned.is_closed

    injected = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )
    notifier = WebhookNotifier(
        _config(), tmp_path, "https://calendar.example.test", logging.getLogger("test.injected"), injected
    )
    await notifier.close()
    assert not injected.is_closed
    await injected.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("webhook_type", ["dingtalk", "telegram"])
async def test_transport_logs_hide_signed_urls_and_standalone_tokens(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, webhook_type: str,
) -> None:
    """签名 URL 后缀和 Telegram 路径令牌不会传入任何日志处理器。"""
    token = "private-bot-token"
    secret = "private-signing-secret"
    url = (
        f"https://api.telegram.org/bot{token}" if webhook_type == "telegram"
        else f"https://hooks.example.test/send?access_token={token}"
    )

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"failed {request.url} token={token} secret={secret}", request=request,
        )

    notifier = _notifier(
        tmp_path,
        _config(webhook_type, url=url, secret=secret, chat_id="1"),
        httpx.MockTransport(handle),
    )
    with caplog.at_level(logging.DEBUG):
        await notifier.notify({"classic": "one.png"}, DATA)
    assert token not in caplog.text
    assert secret not in caplog.text
    assert "https://" not in caplog.text
    assert "&sign=" not in caplog.text
    assert "&timestamp=" not in caplog.text
    assert "ConnectError" in caplog.text


@pytest.mark.asyncio
async def test_failed_pair_does_not_block_other_webhook_or_consume_daily_state(
    tmp_path: Path,
) -> None:
    """仅持久化投递成功的组合；投递失败的组合仍可重试。"""
    calls: list[str] = []
    failing = True

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/bad" and failing:
            return httpx.Response(403)
        return httpx.Response(204)

    config = NotifyConfig(enabled=True, webhooks=[
        WebhookConfig(name="bad", type="generic", url="https://hooks.example.test/bad"),
        WebhookConfig(name="good", type="generic", url="https://hooks.example.test/good"),
    ])
    notifier = _notifier(tmp_path, config, httpx.MockTransport(handle))
    await notifier.notify({"classic": "one.png"}, DATA, trigger="scheduled")
    sent = json.loads((tmp_path / "notify_state.json").read_text())["sent"]
    assert "bad" not in sent
    assert "classic" in sent["good"]
    failing = False
    await notifier.notify({"classic": "two.png"}, DATA, trigger="scheduled")
    assert calls == ["/bad", "/good", "/bad"]
    sent = json.loads((tmp_path / "notify_state.json").read_text())["sent"]
    assert sent["bad"]["classic"] == DATA["updated_at"]


@pytest.mark.asyncio
async def test_next_date_sends_again_and_retry_can_succeed(tmp_path: Path) -> None:
    """5xx 重试成功后只记录一次成功，下一业务日仍可投递。"""
    calls = 0
    name = "retry-success-next-day"
    before = _metric(name, "success")

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.extensions["timeout"] == {
            "connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0,
        }
        return httpx.Response(503 if calls == 1 else 200, json={})

    notifier = _notifier(
        tmp_path, _config(name=name, once_per_day=True), httpx.MockTransport(handle),
    )
    await notifier.notify({"classic": "one.png"}, DATA)
    await notifier.notify({"classic": "two.png"}, {**DATA, "date": "2026-05-09"})
    assert calls == 3
    assert _metric(name, "success") == before + 2
    assert json.loads((tmp_path / "notify_state.json").read_text())["date"] == "2026-05-09"


@pytest.mark.asyncio
@pytest.mark.parametrize("response_body", [b"not-json", b"[]", b"{}"])
async def test_malformed_provider_responses_fail_safely(
    tmp_path: Path, response_body: bytes,
) -> None:
    """即使 HTTP 状态为 200，也必须包含平台要求的成功字段。"""
    before = _metric("malformed", "failure")
    notifier = _notifier(
        tmp_path, _config("feishu", name="malformed"),
        httpx.MockTransport(lambda request: httpx.Response(200, content=response_body)),
    )
    await notifier.notify({"classic": "one.png"}, DATA)
    assert _metric("malformed", "failure") == before + 1
    assert not (tmp_path / "notify_state.json").exists()
