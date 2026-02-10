#!/usr/bin/env python3
"""One-time image rendering script for testing."""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import (
    CrazyThursdaySource,
    FunContentSource,
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
from app.services.holiday import HolidayService
from app.services.kfc import KfcService
from app.services.renderer import ImageRenderer
from app.services.stock_index import StockIndexService


async def main():
    """Generate image once."""
    # Load config
    config = load_config()
    logger = setup_logging(config.logging, logger_name="render_once")

    # Initialize timezones
    init_timezones(business_tz=config.timezone.business, display_tz=config.timezone.display)

    # Ensure directories exist
    cache_dir = Path(config.paths.cache_dir)
    (cache_dir / "images").mkdir(parents=True, exist_ok=True)
    (cache_dir / "data").mkdir(parents=True, exist_ok=True)
    (cache_dir / "holidays").mkdir(parents=True, exist_ok=True)

    # Initialize services
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

    data_computer = DataComputer()

    # Get templates configuration
    templates_config = config.get_templates_config()

    image_renderer = ImageRenderer(
        templates_config=templates_config,
        images_dir=str(cache_dir / "images"),
        render_config=config.templates.config,
        logger=logger,
    )

    logger.info("Starting image generation...")

    # 1. Fetch data
    raw_data = await data_fetcher.fetch_all()
    logger.info(f"Fetched data from {len(raw_data)} endpoints")

    # 1.1 Fetch holiday data
    try:
        holidays = await holiday_service.fetch_holidays()
        raw_data["holidays"] = holidays
        logger.info(f"Fetched {len(holidays)} holidays")
    except Exception as e:
        logger.warning(f"Failed to fetch holidays, using default: {e}")
        raw_data["holidays"] = []

    # 1.2 Fetch fun content
    try:
        from datetime import date

        fun_content = await fun_content_service.fetch_content(date.today())
        raw_data["fun_content"] = fun_content
        logger.info(f"Fetched fun content: {fun_content.get('title')}")
    except Exception as e:
        logger.warning(f"Failed to fetch fun content, using default: {e}")
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
            logger.warning(f"Failed to fetch KFC content: {e}")

    # 1.4 Fetch stock index data
    raw_data["stock_indices"] = None
    if stock_index_service:
        try:
            stock_indices = await stock_index_service.fetch_indices()
            raw_data["stock_indices"] = stock_indices
            logger.info(f"Fetched {len(stock_indices.get('items', []))} stock indices")
        except Exception as e:
            logger.warning(f"Failed to fetch stock indices: {e}")

    # 2. Compute template context
    template_data = data_computer.compute(raw_data)
    logger.info("Template data computed")

    # 3. Render image
    filename = await image_renderer.render(template_data)
    logger.info(f"Image rendered: {filename}")

    # 4. Update data file
    data_dir = cache_dir / "data"
    today_str = now.strftime("%Y-%m-%d")
    data_file = data_dir / f"{today_str}.json"
    now = datetime.now(get_display_timezone())

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
        "images": {"moyuren": filename},
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

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=data_dir,
        prefix=".data_",
        suffix=".tmp",
        delete=False,
    ) as tmp_file:
        json.dump(data, tmp_file, ensure_ascii=False, indent=2)
        tmp_path = tmp_file.name

    os.replace(tmp_path, data_file)
    logger.info(f"Data file updated: {data_file}")

    # Print result
    image_path = cache_dir / "images" / filename
    print(f"\n✅ Image generated: {image_path}")
    print(f"   Size: {image_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    asyncio.run(main())
