"""最近每日图片的 RSS 2.0 订阅源。"""

import asyncio
import html
import logging
import re
import stat
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app.api.v1.common import build_image_url, cache_etag, check_not_modified
from app.core.config import AppConfig, SchedulerConfig
from app.core.errors import ErrorCode, StorageError, error_response, get_http_status
from app.models.schemas import DailyDataFile

router = APIRouter(prefix="/api/v1", tags=["feed"])
_ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
_DATE_FILENAME = re.compile(r"\d{4}-\d{2}-\d{2}\.json", re.ASCII)
_XML_INVALID = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
ET.register_namespace("atom", _ATOM_NAMESPACE)


@dataclass(frozen=True)
class _FeedItem:
    """已校验的元数据与图片大小，在事件循环外收集。"""

    data: DailyDataFile
    filename: str
    published: datetime
    image_size: int | None


def _clean_text(value: str) -> str:
    """移除 XML 1.0 禁止的字符，包括孤立代理项。"""
    return _XML_INVALID.sub("", value)


def _load_items(
    cache_dir: Path, template: str, limit: int, logger: logging.Logger
) -> list[_FeedItem]:
    """按日期从新到旧读取数据文件，凑够足量有效且匹配模板的条目。"""
    try:
        paths = sorted((cache_dir / "data").glob("*.json"), reverse=True)
    except OSError as exc:
        logger.debug("Skipping unreadable feed data directory: %s", exc)
        return []

    items: list[_FeedItem] = []
    for path in paths:
        if not _DATE_FILENAME.fullmatch(path.name):
            continue
        try:
            date.fromisoformat(path.stem)
            data = DailyDataFile.load(path)
            if data.date != path.stem:
                logger.debug("Skipping feed file with mismatched date: %s", path)
                continue
            filename = data.images.get(template)
            if filename is None:
                continue
            published = datetime.fromtimestamp(data.updated_at / 1000, tz=UTC)
        except (StorageError, ValueError, OverflowError, OSError) as exc:
            logger.debug("Skipping invalid or unreadable feed file %s: %s", path, exc)
            continue

        image_size: int | None = None
        try:
            images_dir = (cache_dir / "images").resolve()
            image_path = (images_dir / filename).resolve()
            if image_path.is_relative_to(images_dir):
                image_stat = image_path.stat()
                if stat.S_ISREG(image_stat.st_mode):
                    image_size = image_stat.st_size
        except (OSError, ValueError, RuntimeError) as exc:
            logger.debug("Omitting unavailable feed enclosure %s: %s", filename, exc)
        items.append(_FeedItem(data, filename, published, image_size))
        if len(items) >= limit:
            break
    return items


def _description(item: _FeedItem, image_url: str) -> str:
    """先对源文本做 HTML 转义，再由 XML 序列化处理外层转义。"""
    data = item.data
    parts = [
        f'<img src="{html.escape(_clean_text(image_url), quote=True)}" '
        f'alt="摸鱼日历 {data.date}"/>'
    ]
    if isinstance(data.lunar_date, str) and data.lunar_date:
        parts.append(f"<p>{html.escape(_clean_text(data.lunar_date))}</p>")
    if isinstance(data.news_list, list):
        headlines = [headline for headline in data.news_list if isinstance(headline, str)][:5]
        if headlines:
            parts.append("<ul>")
            parts.extend(f"<li>{html.escape(_clean_text(headline))}</li>" for headline in headlines)
            parts.append("</ul>")
    if data.is_stale:
        source_date = html.escape(_clean_text(data.source_date or "未知"))
        parts.append(f"<p>数据日期 {source_date} （非当日）</p>")
    return "".join(parts)


def _ttl(scheduler: SchedulerConfig) -> int | None:
    """调度间隔明确时，以分钟数输出 RSS ttl。"""
    if scheduler.mode == "hourly":
        return 60
    if scheduler.mode == "daily" and len(scheduler.daily_times) == 1:
        return 24 * 60
    return None


def _build_feed(
    items: list[_FeedItem], config: AppConfig, template: str, default: str, self_url: str
) -> bytes:
    """用 ElementTree 序列化 RSS，统一处理全部 XML 文本与属性。"""
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    title = "摸鱼日历" if template == default else f"摸鱼日历 {template}"
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = config.server.base_domain
    ET.SubElement(channel, "description").text = "每日摸鱼日历图片、农历与新闻"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, f"{{{_ATOM_NAMESPACE}}}link", {
        "href": self_url, "rel": "self", "type": "application/rss+xml",
    })
    if items:
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(items[0].published)
    ttl = _ttl(config.scheduler)
    if ttl is not None:
        ET.SubElement(channel, "ttl").text = str(ttl)
    for item in items:
        data = item.data
        image_url = build_image_url(config.server.base_domain, item.filename)
        weekday = data.weekday if isinstance(data.weekday, str) else ""
        weekday = _clean_text(weekday).strip()
        if not weekday:
            weekday = "星期" + "一二三四五六日"[date.fromisoformat(data.date).weekday()]
        entry = ET.SubElement(channel, "item")
        ET.SubElement(entry, "title").text = f"摸鱼日历 {data.date} {weekday}"
        ET.SubElement(entry, "link").text = image_url
        ET.SubElement(entry, "guid", {"isPermaLink": "false"}).text = (
            f"moyuren:{template}:{data.date}:{data.updated_at}"
        )
        ET.SubElement(entry, "pubDate").text = format_datetime(item.published)
        if item.image_size is not None:
            ET.SubElement(entry, "enclosure", {
                "url": image_url, "type": "image/jpeg", "length": str(item.image_size),
            })
        ET.SubElement(entry, "description").text = _description(item, image_url)
    for element in rss.iter():
        if element.text is not None:
            element.text = _clean_text(element.text)
        for key, value in element.attrib.items():
            element.set(key, _clean_text(value))
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


async def _require_enabled(request: Request) -> None:
    """feed 关闭时在查询参数校验之前就隐藏端点，表现如同路由不存在。"""
    config: AppConfig | None = getattr(request.app.state, "config", None)
    if config is None or not config.feed.enabled:
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/feed.xml", response_class=Response, dependencies=[Depends(_require_enabled)])
@router.head(
    "/feed.xml", response_class=Response, include_in_schema=False,
    dependencies=[Depends(_require_enabled)],
)
async def get_feed(
    request: Request,
    template: str | None = Query(None, description="模板名称，默认使用配置的默认模板"),
    limit: int | None = Query(None, ge=1, description="条目数量，上限为 feed.max_items"),
) -> Response:
    """以 RSS 返回最近图片，支持条件 GET 与 HEAD。"""
    config: AppConfig = request.app.state.config
    selected_limit = config.feed.max_items if limit is None else limit
    if selected_limit > config.feed.max_items:
        raise RequestValidationError([{
            "type": "less_than_equal", "loc": ("query", "limit"),
            "msg": f"Input should be less than or equal to {config.feed.max_items}",
            "input": selected_limit, "ctx": {"le": config.feed.max_items},
        }])
    templates = await asyncio.to_thread(config.get_templates_config)
    default = templates.default or (templates.items[0].name if templates.items else "moyuren")
    selected_template = default if template is None else template
    if not any(item.name == selected_template for item in templates.items):
        code = ErrorCode.API_TEMPLATE_NOT_FOUND
        return JSONResponse(
            error_response(code, f"No image found for template: {selected_template}"),
            status_code=get_http_status(code),
        )
    logger: logging.Logger = request.app.state.logger
    items = await asyncio.to_thread(
        _load_items, Path(config.paths.cache_dir), selected_template, selected_limit, logger
    )
    updated_at = items[0].data.updated_at if items else 0
    identity = f"feed:{selected_template}:{selected_limit}"
    headers = {
        "Cache-Control": "public, max-age=300, must-revalidate",
        "ETag": cache_etag(updated_at, identity),
        "Last-Modified": format_datetime(datetime.fromtimestamp(updated_at // 1000, tz=UTC), usegmt=True),
    }
    if check_not_modified(request, updated_at, identity):
        return Response(status_code=304, headers=headers)
    self_url = f"{config.server.base_domain.rstrip('/')}/api/v1/feed.xml"
    if request.url.query:
        self_url += f"?{request.url.query}"
    content = _build_feed(items, config, selected_template, default, self_url)
    headers["Content-Type"] = "application/rss+xml; charset=utf-8"
    headers["Content-Length"] = str(len(content))
    return Response(content=b"" if request.method == "HEAD" else content, headers=headers)
