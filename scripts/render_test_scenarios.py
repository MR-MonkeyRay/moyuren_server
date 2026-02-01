#!/usr/bin/env python3
"""渲染测试场景：模拟当日节气、当日假日、当日周末"""

import asyncio
import sys
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
from app.services.renderer import ImageRenderer
from app.services.calendar import init_timezones


async def main():
    """生成模拟特殊场景的测试图片"""
    # Load config
    config = load_config()
    logger = setup_logging(config.logging, logger_name="render_test")

    # Initialize timezones
    init_timezones(
        business_tz=config.timezone.business,
        display_tz=config.timezone.display
    )

    # Ensure directories exist
    Path(config.paths.static_dir).mkdir(parents=True, exist_ok=True)

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
    data_computer = DataComputer()
    image_renderer = ImageRenderer(
        template_path=config.paths.template_path,
        static_dir=config.paths.static_dir,
        render_config=config.render,
        logger=logger,
    )

    logger.info("开始生成测试场景图片...")

    # 1. Fetch data
    raw_data = await data_fetcher.fetch_all()

    # 1.1 Fetch holiday data
    try:
        holidays = await holiday_service.fetch_holidays()
        raw_data["holidays"] = holidays
    except Exception as e:
        logger.warning(f"获取节假日失败: {e}")
        raw_data["holidays"] = []

    # 1.2 Fetch fun content
    try:
        from datetime import date
        fun_content = await fun_content_service.fetch_content(date.today())
        raw_data["fun_content"] = fun_content
    except Exception:
        raw_data["fun_content"] = None

    raw_data["kfc_copy"] = None

    # 2. Compute template context
    template_data = data_computer.compute(raw_data)

    # 3. 覆盖数据以模拟特殊场景

    # 模拟当日周末
    template_data["weekend"] = {
        "days_left": 0,
        "is_weekend": True
    }

    # 模拟当日节气
    template_data["solar_term"] = {
        "name": "立春",
        "name_en": "Beginning of Spring",
        "days_left": 0,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "is_today": True
    }

    # 模拟当日假日（包含一个正在进行的假期和一个补班日）
    today_str = datetime.now().strftime("%Y-%m-%d")
    template_data["holidays"] = [
        {
            "name": "春节（补班）",
            "start_date": today_str,
            "end_date": today_str,
            "duration": 1,
            "days_left": 0,
            "is_legal_holiday": True,
            "color": "#E67E22",
            "is_off_day": False  # 补班日
        },
        {
            "name": "春节",
            "start_date": today_str,
            "end_date": "2026-02-08",
            "duration": 8,
            "days_left": 0,
            "is_legal_holiday": True,
            "color": "#E67E22",
            "is_off_day": True  # 假期中
        },
        {
            "name": "清明节",
            "start_date": "2026-04-04",
            "end_date": "2026-04-06",
            "duration": 3,
            "days_left": 62,
            "is_legal_holiday": True,
            "color": "#E67E22",
            "is_off_day": True
        },
        {
            "name": "劳动节",
            "start_date": "2026-05-01",
            "end_date": "2026-05-05",
            "duration": 5,
            "days_left": 89,
            "is_legal_holiday": True,
            "color": "#E67E22",
            "is_off_day": True
        },
    ]

    logger.info("已覆盖测试数据：当日周末、当日节气、当日假日/补班")

    # 4. Render image
    filename = await image_renderer.render(template_data)
    logger.info(f"测试图片已生成: {filename}")

    # 输出图片路径
    image_path = Path(config.paths.static_dir) / filename
    print(f"\n✅ 测试图片已生成: {image_path.absolute()}")
    print("\n模拟场景：")
    print("  - 当日周末：🎉 周末愉快，摸鱼无罪！")
    print("  - 当日节气：今日 立春，顺应天时")
    print("  - 当日补班：😭 补班中")
    print("  - 当日假期：🥳 假期中")


if __name__ == "__main__":
    asyncio.run(main())
