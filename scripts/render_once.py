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

from app.core.config import load_config
from app.core.logging import setup_logging
from app.services.compute import DataComputer
from app.services.fetcher import DataFetcher
from app.services.fun_content import FunContentService
from app.services.holiday import HolidayService
from app.services.kfc import KfcService
from app.services.renderer import ImageRenderer


async def main():
    """Generate image once."""
    # Load config
    config = load_config()
    logger = setup_logging(config.logging, logger_name="render_once")

    # Ensure directories exist
    Path(config.paths.static_dir).mkdir(parents=True, exist_ok=True)
    Path(config.paths.state_path).parent.mkdir(parents=True, exist_ok=True)

    # Initialize services
    data_fetcher = DataFetcher(
        endpoints=config.fetch.api_endpoints,
        logger=logger,
    )
    holiday_cache_dir = Path(config.paths.state_path).parent / "holidays"
    holiday_service = HolidayService(
        logger=logger,
        cache_dir=holiday_cache_dir,
        mirror_urls=config.holiday.mirror_urls,
        timeout_sec=config.holiday.timeout_sec,
    )
    fun_content_service = FunContentService(config.fun_content)

    # Initialize KFC service if config exists
    kfc_service = None
    if config.crazy_thursday:
        kfc_service = KfcService(config.crazy_thursday)

    data_computer = DataComputer()
    image_renderer = ImageRenderer(
        template_path=config.paths.template_path,
        static_dir=config.paths.static_dir,
        render_config=config.render,
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

    # 2. Compute template context
    template_data = data_computer.compute(raw_data)
    logger.info("Template data computed")

    # 3. Render image
    filename = await image_renderer.render(template_data)
    logger.info(f"Image rendered: {filename}")

    # 4. Update state file
    state_file = Path(config.paths.state_path)
    now = datetime.now()
    state_data = {
        "date": now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(),
        "filename": filename,
    }

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=state_file.parent,
        prefix=".latest_",
        suffix=".tmp",
        delete=False,
    ) as tmp_file:
        json.dump(state_data, tmp_file, ensure_ascii=False, indent=2)
        tmp_path = tmp_file.name

    os.replace(tmp_path, state_file)
    logger.info(f"State file updated: {config.paths.state_path}")

    # Print result
    image_path = Path(config.paths.static_dir) / filename
    print(f"\n✅ Image generated: {image_path}")
    print(f"   Size: {image_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    asyncio.run(main())
