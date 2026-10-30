"""Prometheus 指标与轻量记录入口。"""

from typing import Literal
from urllib.parse import urlparse

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from app import __version__
from app.core.network import redact_url

FetchOutcome = Literal["cache", "fresh", "stale", "failed"]
GenerationResult = Literal["success", "partial", "failure"]
NotifyResult = Literal["success", "failure", "skipped"]

registry = CollectorRegistry()

generation_duration = Histogram(
    "moyuren_generation_duration_seconds",
    "Duration of a complete image generation run.",
    ["result"],
    registry=registry,
)
render_duration = Histogram(
    "moyuren_render_duration_seconds",
    "Duration of each template render, including failed attempts.",
    ["template"],
    registry=registry,
)
source_fetch_total = Counter(
    "moyuren_source_fetch_total",
    "Data source retrieval outcomes.",
    ["source", "result"],
    registry=registry,
)
notify_total = Counter(
    "moyuren_notify_total",
    "Webhook notification outcomes per template delivery.",
    ["webhook", "result"],
    registry=registry,
)
instance_consecutive_failures = Gauge(
    "moyuren_instance_consecutive_failures",
    "Consecutive failures last reported for an instance origin.",
    ["endpoint"],
    registry=registry,
)
instance_cooling_down = Gauge(
    "moyuren_instance_cooling_down",
    "Whether an instance origin was cooling down at its last health update.",
    ["endpoint"],
    registry=registry,
)
build_info = Gauge(
    "moyuren_build_info", "Application build version.", ["version"], registry=registry
)
build_info.labels(version=__version__).set(1)


def record_generation_duration(seconds: float, result: GenerationResult) -> None:
    """记录一次完整生成任务的耗时与结果。"""
    generation_duration.labels(result=result).observe(seconds)


def record_render_duration(template: str, seconds: float) -> None:
    """记录单个模板的渲染耗时。"""
    render_duration.labels(template=template).observe(seconds)


def record_source_fetch(source: str, result: FetchOutcome) -> None:
    """记录一次数据源获取结果。"""
    source_fetch_total.labels(source=source, result=result).inc()


def record_instance_health(url: str, failures: int, cooling_down: bool) -> None:
    """按无凭据、无路径的实例源地址更新健康指标。"""
    parsed = urlparse(redact_url(url))
    endpoint = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "<redacted-url>"
    instance_consecutive_failures.labels(endpoint=endpoint).set(failures)
    instance_cooling_down.labels(endpoint=endpoint).set(int(cooling_down))


def record_notify(webhook: str, result: NotifyResult) -> None:
    """记录每个 webhook 的模板通知结果。"""
    notify_total.labels(webhook=webhook, result=result).inc()
