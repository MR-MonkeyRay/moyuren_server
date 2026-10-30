"""异步 webhook 通知、签名与每日发送状态。"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

from app.core.config import NotifyConfig, NotifyTrigger, WebhookConfig
from app.core.errors import StorageError, WebhookDeliveryError
from app.core.metrics import record_notify
from app.core.network import create_async_client, safe_exception_for_log

# HTTPX logs complete request URLs, including bot tokens in their paths.
_webhook_request: ContextVar[bool] = ContextVar("webhook_request", default=False)


class _WebhookLogFilter(logging.Filter):
    """Keep HTTP library request diagnostics out of webhook delivery logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Suppress only records produced within a webhook request context."""
        return not _webhook_request.get()


_http_log_filter = _WebhookLogFilter()
for _logger_name in (
    "httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
    "httpcore.proxy", "httpcore.socks",
):
    logging.getLogger(_logger_name).addFilter(_http_log_filter)


class WebhookNotifier:
    """Deliver generated images without exposing credentials or blocking generation."""

    def __init__(
        self,
        config: NotifyConfig,
        cache_dir: Path,
        base_domain: str,
        logger: logging.Logger,
        http_client: httpx.AsyncClient | None = None,
        proxy_url: str | None = None,
    ) -> None:
        """Use the shared HTTP client or own a lazily created client."""
        self.config = config
        self._state_file = cache_dir / "notify_state.json"
        self._base_domain = base_domain.rstrip("/")
        self._logger = logger
        self._client = http_client
        self._owns_client = http_client is None
        self._proxy_url = proxy_url
        self._lock = asyncio.Lock()
        self._state = self._read_state()

    def _read_state(self) -> dict[str, Any]:
        """Read sent state once; missing or invalid state starts empty."""
        try:
            state = json.loads(self._state_file.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or not isinstance(state.get("date"), str):
                raise StorageError("Invalid notification state")
            sent = state.get("sent")
            if not isinstance(sent, dict) or not all(
                isinstance(name, str) and isinstance(templates, dict)
                and all(isinstance(template, str) and isinstance(updated, (int, float, str))
                        and not isinstance(updated, bool)
                        for template, updated in templates.items())
                for name, templates in sent.items()
            ):
                raise StorageError("Invalid notification sent state")
            return state
        except FileNotFoundError:
            # 首次启动尚无发送记录，属正常情况
            return {"date": "", "sent": {}}
        except (OSError, UnicodeError, ValueError, StorageError):
            self._logger.warning("Notification state missing or invalid; starting empty")
            return {"date": "", "sent": {}}

    def _save_state(self) -> None:
        """Atomically persist successful sends, retaining no secrets."""
        tmp_path: Path | None = None
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._state_file.parent,
                prefix=".notify_", suffix=".tmp", delete=False,
            ) as handle:
                tmp_path = Path(handle.name)
                json.dump(self._state, handle, ensure_ascii=False)
            os.replace(tmp_path, self._state_file)
        except OSError as exc:
            raise StorageError("Failed to persist notification state") from exc
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    async def close(self) -> None:
        """Close only a client owned by this service."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def notify(
        self,
        images: dict[str, str],
        data: dict[str, Any],
        trigger: NotifyTrigger = "manual",
    ) -> None:
        """Send one message per eligible pair; delivery failures never escape."""
        try:
            await self._notify(images, data, trigger)
        except Exception as exc:
            self._logger.warning("Notification processing failed: %s", type(exc).__name__)

    async def _notify(
        self, images: dict[str, str], data: dict[str, Any], trigger: NotifyTrigger,
    ) -> None:
        """Check eligibility and record successful sends under the state lock."""
        # Serialize state checks and sends so concurrent generations cannot duplicate a day.
        async with self._lock:
            date = str(data["date"])
            if self._state["date"] != date:
                self._state = {"date": date, "sent": {}}
            for webhook in self.config.webhooks:
                for template, filename in images.items():
                    sent = self._state["sent"].get(webhook.name, {})
                    if (
                        not self.config.enabled
                        or trigger not in self.config.triggers
                        or (webhook.templates is not None and template not in webhook.templates)
                        or (self.config.once_per_day and template in sent)
                    ):
                        record_notify(webhook.name, "skipped")
                        continue
                    try:
                        image_url = f"{self._base_domain}/static/{filename}"
                        await self._deliver(webhook, template, image_url, data)
                    except Exception as exc:
                        record_notify(webhook.name, "failure")
                        self._log_failure(webhook, exc)
                        continue
                    record_notify(webhook.name, "success")
                    self._state["sent"].setdefault(webhook.name, {})[template] = data.get("updated_at", "")
                    try:
                        self._save_state()
                    except Exception as exc:
                        self._logger.warning(
                            "Notification state write failed: %s", safe_exception_for_log(exc)
                        )

    def _log_failure(self, webhook: WebhookConfig, exc: Exception) -> None:
        """Log webhook identity and status with URL paths and secrets removed."""
        url = webhook.url.get_secret_value() if webhook.url else ""
        secret = webhook.secret.get_secret_value() if webhook.secret else ""
        status = (
            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError)
            else exc.status_code if isinstance(exc, WebhookDeliveryError) else None
        )
        parts = urlsplit(url)
        path_values = [unquote(part) for part in parts.path.split("/") if part]
        sensitive_values = (
            url, secret, *path_values,
            *(part[3:] for part in path_values if part.startswith("bot") and len(part) > 3),
            *(value for _, value in parse_qsl(parts.query)),
            parts.username or "", parts.password or "",
        )
        self._logger.warning(
            "Notification failed webhook=%s type=%s status=%s: %s",
            webhook.name, webhook.type, status,
            safe_exception_for_log(exc, url, secret, sensitive_values=sensitive_values),
        )

    def _payload(
        self, webhook: WebhookConfig, template: str, image_url: str, data: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """Build provider payloads and timestamped signatures."""
        url = webhook.url.get_secret_value() if webhook.url else ""
        secret = webhook.secret.get_secret_value() if webhook.secret else ""
        date = str(data["date"])
        title = f"摸鱼日历 {date} {data.get('weekday', '')}".strip()
        description = str(data.get("lunar_date", ""))
        if data.get("is_stale", False):
            description += "\n部分数据为缓存（非当日数据）"
        caption = f"{title}\n{description}".strip()
        headers = {"Content-Type": "application/json"}
        payload: dict[str, Any]
        if webhook.type == "wecom":
            payload = {"msgtype": "news", "news": {"articles": [{
                "title": title, "description": caption, "url": image_url, "picurl": image_url,
            }]}}
        elif webhook.type == "feishu":
            payload = {"msg_type": "post", "content": {"post": {"zh_cn": {
                "title": title, "content": [[
                    {"tag": "text", "text": description},
                    {"tag": "a", "text": "查看日历", "href": image_url},
                ]],
            }}}}
            if secret:
                timestamp = str(int(time.time()))
                payload.update(timestamp=timestamp, sign=base64.b64encode(hmac.new(
                    f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256,
                ).digest()).decode())
        elif webhook.type == "dingtalk":
            payload = {"msgtype": "markdown", "markdown": {
                "title": title, "text": f"### {title}\n![]({image_url})\n{description}",
            }}
            if secret:
                timestamp = str(int(time.time() * 1000))
                sign = base64.b64encode(hmac.new(
                    secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256,
                ).digest()).decode()
                parts = urlsplit(url)
                query = list(parse_qsl(parts.query, keep_blank_values=True))
                query.extend((("timestamp", timestamp), ("sign", sign)))
                url = urlunsplit(parts._replace(query=urlencode(query)))
        elif webhook.type == "telegram":
            parts = urlsplit(url)
            url = urlunsplit(parts._replace(path=parts.path.rstrip("/") + "/sendPhoto"))
            payload = {"chat_id": webhook.chat_id, "photo": image_url, "caption": caption}
        else:
            payload = {
                "event": "moyuren.generated", "date": date, "template": template,
                "image_url": image_url, "updated_at": data.get("updated_at"),
                "is_stale": bool(data.get("is_stale", False)),
            }
            if secret:
                headers["X-Moyuren-Timestamp"] = str(int(time.time()))
        return url, payload, headers

    async def _deliver(
        self, webhook: WebhookConfig, template: str, image_url: str, data: dict[str, Any],
    ) -> None:
        """Retry network/5xx failures once; reject provider failures without retry."""
        if self._client is None:
            self._client = create_async_client(proxy_url=self._proxy_url)
        for attempt in range(2):
            url, payload, headers = self._payload(webhook, template, image_url, data)
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if webhook.type == "generic" and webhook.secret:
                # 签名覆盖 "<timestamp>.<body>"，接收方可据时间戳拒绝重放
                signed = headers["X-Moyuren-Timestamp"].encode() + b"." + body
                signature = hmac.new(webhook.secret.get_secret_value().encode(), signed, hashlib.sha256)
                headers["X-Moyuren-Signature"] = f"sha256={signature.hexdigest()}"
            try:
                token = _webhook_request.set(True)
                try:
                    response = await self._client.post(
                        url, content=body, headers=headers,
                        timeout=httpx.Timeout(self.config.timeout_sec), follow_redirects=False,
                    )
                finally:
                    _webhook_request.reset(token)
                if response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(0.1)
                    continue
                if not response.is_success:
                    raise WebhookDeliveryError("Webhook HTTP response rejected", response.status_code)
                if webhook.type != "generic":
                    try:
                        result = response.json()
                    except (ValueError, UnicodeError) as exc:
                        raise WebhookDeliveryError(
                            "Invalid webhook provider response", response.status_code,
                        ) from exc
                    field = "ok" if webhook.type == "telegram" else (
                        "code" if webhook.type == "feishu" else "errcode"
                    )
                    success = result.get(field) if isinstance(result, dict) else None
                    if (webhook.type == "telegram" and success is not True) or (
                        webhook.type != "telegram" and (type(success) is not int or success != 0)
                    ):
                        # Provider error text may echo credentials; never include it in logs.
                        raise WebhookDeliveryError("Webhook provider rejected the message", response.status_code)
                return
            except httpx.TransportError:
                if attempt != 0:
                    raise
                await asyncio.sleep(0.1)
