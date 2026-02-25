#!/usr/bin/env python3
"""Static artifact generation script for container deployment."""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import (
    CrazyThursdaySource,
    FunContentSource,
    GoldPriceSource,
    HolidaySource,
    NewsSource,
    StockIndexSource,
    load_config,
)
from app.core.logging import setup_logging
from app.services.calendar import get_display_timezone, init_timezones
from app.services.compute import DataComputer
from app.services.fetcher import DataFetcher
from app.services.fun_content import FunContentService
from app.services.gold_price import GoldPriceService
from app.services.holiday import HolidayService
from app.services.kfc import KfcService
from app.services.renderer import ImageRenderer
from app.services.stock_index import StockIndexService


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Generate static artifacts for moyuren calendar")
    parser.add_argument(
        "--output",
        type=str,
        default="/output",
        help="Output directory for artifacts (default: /output)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="https://moyuren.pages.dev",
        help="Base URL for static site (default: https://moyuren.pages.dev)",
    )
    return parser.parse_args()


def write_atomic(content: str, target_path: Path):
    """Write content to file atomically."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_path.parent,
            prefix=".tmp_",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name
        os.replace(tmp_path, target_path)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def copy_atomic(src_path: Path, target_path: Path):
    """Copy file atomically."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_path.parent,
            prefix=".tmp_",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            with open(src_path, "rb") as src_file:
                shutil.copyfileobj(src_file, tmp_file)
            tmp_path = tmp_file.name
        os.replace(tmp_path, target_path)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def generate_txt(data: dict, base_url: str) -> str:
    """Generate TXT format artifact."""
    lines = []

    # Title and header
    lines.append(f"摸鱼日历 {data['date']}")
    lines.append("=" * 40)

    # Date info
    weekday = data.get('weekday', '')
    lunar_date = data.get('lunar_date', '')
    if weekday or lunar_date:
        date_line = f"{weekday} | 农历 {lunar_date}" if weekday and lunar_date else (weekday or lunar_date)
        lines.append(date_line)
        lines.append("")

    # Holidays countdown
    holidays = data.get('holidays', [])
    if holidays:
        lines.append("假期倒计时：")
        for holiday in holidays:
            if isinstance(holiday, dict):
                name = holiday.get('name', '')
                days_left = holiday.get('days_left', '')
                duration = holiday.get('duration', '')
                if name:
                    lines.append(f"  {name}：还有 {days_left} 天（{duration}天假期）")
        lines.append("")

    # News list
    news_list = data.get('news_list', [])
    if news_list:
        lines.append("今日热点：")
        for news in news_list:
            if news:
                lines.append(f"  - {news}")
        lines.append("")

    # Fun content
    fun_content = data.get('fun_content')
    if fun_content and isinstance(fun_content, dict):
        title = fun_content.get('title', '')
        text = fun_content.get('text', '')
        if title and text:
            lines.append(f"{title}: {text}")
            lines.append("")

    # KFC content (only on Thursday)
    if data.get('is_crazy_thursday'):
        kfc_content = data.get('kfc_content')
        if kfc_content:
            lines.append(f"疯狂星期四: {kfc_content}")
            lines.append("")

    # Footer
    lines.append(f"更新时间: {data['updated']}")
    lines.append(f"图片: {base_url}/latest.jpg")
    lines.append("")

    return "\n".join(lines)


def generate_md(data: dict, base_url: str) -> str:
    """Generate Markdown format artifact."""
    lines = []

    # Title
    lines.append(f"# 摸鱼日历 - {data['date']}")
    lines.append("")

    # Date info
    weekday = data.get('weekday', '')
    lunar_date = data.get('lunar_date', '')
    if weekday or lunar_date:
        date_line = f"{weekday} | 农历 {lunar_date}" if weekday and lunar_date else (weekday or lunar_date)
        lines.append(f"**{date_line}**")
        lines.append("")

    # Image
    lines.append(f"![摸鱼日历]({base_url}/latest.jpg)")
    lines.append("")

    # Holidays countdown
    holidays = data.get('holidays', [])
    if holidays:
        lines.append("## 假期倒计时")
        lines.append("")
        for holiday in holidays:
            if isinstance(holiday, dict):
                name = holiday.get('name', '')
                days_left = holiday.get('days_left', '')
                duration = holiday.get('duration', '')
                if name:
                    lines.append(f"- {name}：还有 {days_left} 天（{duration}天假期）")
        lines.append("")

    # News list
    news_list = data.get('news_list', [])
    if news_list:
        lines.append("## 今日热点")
        lines.append("")
        for idx, news in enumerate(news_list, 1):
            if news:
                lines.append(f"{idx}. {news}")
        lines.append("")

    # Fun content
    fun_content = data.get('fun_content')
    if fun_content and isinstance(fun_content, dict):
        title = fun_content.get('title', '')
        text = fun_content.get('text', '')
        if title and text:
            lines.append(f"> **{title}**")
            lines.append(">")
            lines.append(f"> {text}")
            lines.append("")

    # KFC content (only on Thursday)
    if data.get('is_crazy_thursday'):
        kfc_content = data.get('kfc_content')
        if kfc_content:
            lines.append("> **疯狂星期四**")
            lines.append(">")
            lines.append(f"> {kfc_content}")
            lines.append("")

    # Footer
    lines.append(f"*更新时间: {data['updated']}*")
    lines.append("")

    return "\n".join(lines)


async def main():
    """Generate static artifacts."""
    args = parse_args()
    output_dir = Path(args.output)
    base_url = args.base_url.rstrip("/")

    # Load config
    config = load_config()
    logger = setup_logging(config.logging, logger_name="publish_static")

    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Base URL: {base_url}")

    # Initialize timezones
    init_timezones(business_tz=config.timezone.business, display_tz=config.timezone.display)

    # Ensure directories exist
    cache_dir = Path(config.paths.cache_dir)
    (cache_dir / "images").mkdir(parents=True, exist_ok=True)
    (cache_dir / "data").mkdir(parents=True, exist_ok=True)
    (cache_dir / "holidays").mkdir(parents=True, exist_ok=True)

    # Initialize services (same as render_once.py:48-88)
    news_source = config.get_source(NewsSource)
    data_fetcher = DataFetcher(
        source=news_source,
        logger=logger,
    )
    holiday_cache_dir = cache_dir / "holidays"
    holiday_source = config.get_source(HolidaySource)
    holiday_service = HolidayService(
        logger=logger,
        cache_dir=holiday_cache_dir,
        mirror_urls=holiday_source.mirror_urls if holiday_source else [],
        timeout_sec=holiday_source.timeout_sec if holiday_source else 10,
    )
    fun_content_source = config.get_source(FunContentSource)
    fun_content_service = FunContentService(fun_content_source)

    # Initialize KFC service if config exists
    kfc_service = None
    crazy_thursday_source = config.get_source(CrazyThursdaySource)
    if crazy_thursday_source:
        kfc_service = KfcService(crazy_thursday_source)

    # Initialize stock index service if config exists
    stock_index_service = None
    stock_index_source = config.get_source(StockIndexSource)
    if stock_index_source:
        stock_index_service = StockIndexService(stock_index_source)

    # Initialize gold price service if config exists
    gold_price_service = None
    gold_price_source = config.get_source(GoldPriceSource)
    if gold_price_source:
        gold_price_service = GoldPriceService(gold_price_source)

    data_computer = DataComputer()

    # Get templates configuration
    templates_config = config.get_templates_config()

    image_renderer = ImageRenderer(
        templates_config=templates_config,
        images_dir=str(cache_dir / "images"),
        render_config=config.templates.config,
        logger=logger,
    )

    logger.info("Starting artifact generation...")

    try:
        # 1. Fetch data (same as render_once.py:92-134)
        raw_data = await data_fetcher.fetch_all()
        logger.info(f"Fetched data from {len(raw_data)} endpoints")

        # 1.1 Fetch holiday data
        try:
            holidays = await holiday_service.fetch_holidays()
            raw_data["holidays"] = holidays
            logger.info(f"Fetched {len(holidays)} holidays")
        except Exception as e:
            logger.warning(f"Failed to fetch holidays, using default: {type(e).__name__}")
            raw_data["holidays"] = []

        # 1.2 Fetch fun content
        try:
            fun_content = await fun_content_service.fetch_content(date.today())
            raw_data["fun_content"] = fun_content
            logger.info(f"Fetched fun content: {fun_content.get('title')}")
        except Exception as e:
            logger.warning(f"Failed to fetch fun content, using default: {type(e).__name__}")
            raw_data["fun_content"] = None

        # 1.3 Fetch KFC Crazy Thursday content (Only on Thursday)
        raw_data["kfc_copy"] = None
        if kfc_service and date.today().weekday() == 3:
            try:
                kfc_copy = await kfc_service.fetch_kfc_copy()
                raw_data["kfc_copy"] = kfc_copy
                if kfc_copy:
                    logger.info("Fetched KFC Crazy Thursday content")
            except Exception as e:
                logger.warning(f"Failed to fetch KFC content: {type(e).__name__}")

        # 1.4 Fetch stock index data
        raw_data["stock_indices"] = None
        if stock_index_service:
            try:
                stock_indices = await stock_index_service.fetch_indices()
                raw_data["stock_indices"] = stock_indices
                logger.info(f"Fetched {len(stock_indices.get('items', []))} stock indices")
            except Exception as e:
                logger.warning(f"Failed to fetch stock indices: {type(e).__name__}")

        # 1.5 Fetch gold price data
        raw_data["gold_price"] = None
        if gold_price_service:
            try:
                gold_price = await gold_price_service.fetch_gold_price()
                raw_data["gold_price"] = gold_price
                if gold_price:
                    logger.info(f"Fetched gold price: {gold_price.get('today_price')}")
            except Exception as e:
                logger.warning(f"Failed to fetch gold price: {type(e).__name__}")

        # 2. Compute template context
        template_data = data_computer.compute(raw_data)
        logger.info("Template data computed")

        # 3. Render image
        filename = await image_renderer.render(template_data)
        logger.info(f"Image rendered: {filename}")

    except Exception as e:
        logger.error(f"Rendering failed: {e}", exc_info=True)
        sys.exit(10)

    # 4. Build data dict (same as render_once.py:150-202)
    now = datetime.now(get_display_timezone())
    today_str = now.strftime("%Y-%m-%d")

    # Extract data from template_data
    date_info = template_data.get("date", {})
    fun_content_raw = template_data.get("history", {})
    kfc_content_raw = template_data.get("kfc_content")

    # Build fun_content
    fun_content = None
    if fun_content_raw:
        # Determine type from title
        title = fun_content_raw.get("title", "")
        content_type = "unknown"
        if "冷笑话" in title:
            content_type = "dad_joke"
        elif "一言" in title:
            content_type = "hitokoto"
        elif "段子" in title:
            content_type = "duanzi"
        elif "摸鱼" in title:
            content_type = "moyu_quote"

        fun_content = {"type": content_type, "title": title, "text": fun_content_raw.get("content", "")}

    # Build KFC content
    kfc_content = None
    if kfc_content_raw and isinstance(kfc_content_raw, dict):
        kfc_content = kfc_content_raw.get("content")

    data = {
        "date": now.strftime("%Y-%m-%d"),
        "updated": now.strftime("%Y/%m/%d %H:%M:%S"),
        "updated_at": int(now.timestamp() * 1000),
        "images": {"moyuren": "moyuren.jpg"},
        # New content fields
        "weekday": date_info.get("week_cn", ""),
        "lunar_date": date_info.get("lunar_date", ""),
        "fun_content": fun_content,
        "is_crazy_thursday": now.weekday() == 3,
        "kfc_content": kfc_content,
        # Full rendering data fields
        "date_info": template_data.get("date"),
        "weekend": template_data.get("weekend"),
        "solar_term": template_data.get("solar_term"),
        "guide": template_data.get("guide"),
        "news_list": [item.get("text", "") for item in template_data.get("news_list", []) if isinstance(item, dict)],
        "news_meta": template_data.get("news_meta"),
        "holidays": [
            {k: v for k, v in h.items() if k != "color"}
            for h in template_data.get("holidays", [])
            if isinstance(h, dict)
        ],
        "kfc_content_full": template_data.get("kfc_content"),
        "stock_indices": raw_data.get("stock_indices"),
    }

    # 5. Generate artifacts to output directory
    try:
        date_dir = output_dir / today_str
        date_dir.mkdir(parents=True, exist_ok=True)

        # JSON artifacts
        json_content = json.dumps(data, ensure_ascii=False, indent=2)
        write_atomic(json_content, date_dir / "data.json")
        write_atomic(json_content, output_dir / "latest.json")
        logger.info(f"JSON artifacts written: {date_dir / 'data.json'}, {output_dir / 'latest.json'}")

        # Image artifacts (copy from cache)
        src_image = cache_dir / "images" / filename
        if not src_image.exists():
            logger.error(f"Source image not found: {src_image}")
            sys.exit(11)

        copy_atomic(src_image, date_dir / "moyuren.jpg")
        copy_atomic(src_image, output_dir / "latest.jpg")
        logger.info(f"Image artifacts copied: {date_dir / 'moyuren.jpg'}, {output_dir / 'latest.jpg'}")

        # TXT artifacts
        txt_content = generate_txt(data, base_url)
        write_atomic(txt_content, date_dir / "data.txt")
        write_atomic(txt_content, output_dir / "latest.txt")
        logger.info(f"TXT artifacts written: {date_dir / 'data.txt'}, {output_dir / 'latest.txt'}")

        # Markdown artifacts
        md_content = generate_md(data, base_url)
        write_atomic(md_content, date_dir / "data.md")
        write_atomic(md_content, output_dir / "latest.md")
        logger.info(f"Markdown artifacts written: {date_dir / 'data.md'}, {output_dir / 'latest.md'}")

    except Exception as e:
        logger.error(f"Failed to write artifacts: {e}", exc_info=True)
        sys.exit(11)

    # 6. Validate artifacts
    try:
        required_files = [
            date_dir / "data.json",
            date_dir / "moyuren.jpg",
            date_dir / "data.txt",
            date_dir / "data.md",
            output_dir / "latest.json",
            output_dir / "latest.jpg",
            output_dir / "latest.txt",
            output_dir / "latest.md",
        ]

        missing_files = [f for f in required_files if not f.exists()]
        if missing_files:
            logger.error(f"Validation failed: missing files: {missing_files}")
            sys.exit(12)

        logger.info("Artifact validation passed")

    except Exception as e:
        logger.error(f"Validation error: {e}", exc_info=True)
        sys.exit(12)

    # 7. Print summary
    image_size = (output_dir / "latest.jpg").stat().st_size / 1024
    json_size = (output_dir / "latest.json").stat().st_size / 1024

    print(f"\n✅ Static artifacts generated successfully")
    print(f"   Date: {today_str}")
    print(f"   Output: {output_dir}")
    print(f"   Image: {image_size:.1f} KB")
    print(f"   JSON: {json_size:.1f} KB")
    print(f"   Artifacts: 8 files (4 in {today_str}/, 4 in root)")


if __name__ == "__main__":
    asyncio.run(main())
