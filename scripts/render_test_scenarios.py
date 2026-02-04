#!/usr/bin/env python3
"""渲染测试场景：模拟节气、假期、周末等情况。"""

import argparse
import asyncio
import copy
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
from app.services.stock_index import StockIndexService


# 占位符，运行时替换为实际日期
_TODAY_PLACEHOLDER = "__TODAY__"


def get_today_str() -> str:
    """获取当前日期字符串（需在 init_timezones 之后调用）"""
    from app.services.calendar import now_business
    return now_business().strftime("%Y-%m-%d")


def replace_today_placeholder(data: dict | list | str, today_str: str):
    """递归替换数据中的 __TODAY__ 占位符"""
    if isinstance(data, dict):
        return {k: replace_today_placeholder(v, today_str) for k, v in data.items()}
    elif isinstance(data, list):
        return [replace_today_placeholder(item, today_str) for item in data]
    elif isinstance(data, str) and data == _TODAY_PLACEHOLDER:
        return today_str
    return data


SCENARIOS = {
    "weekday": {
        "description": "普通工作日（无特殊事件）",
        "overrides": {
            "weekend": {"days_left": 2, "is_weekend": False},
            "solar_term": {
                "name": "雨水",
                "name_en": "Rain Water",
                "days_left": 12,
                "date": "2026-02-19",
                "is_today": False,
            },
            "holidays": [],
            "kfc_content": None,
        },
    },
    "weekend": {
        "description": "周末",
        "overrides": {
            "weekend": {"days_left": 0, "is_weekend": True},
        },
    },
    "solar_term_today": {
        "description": "节气当日",
        "overrides": {
            "solar_term": {
                "name": "立春",
                "name_en": "Start of Spring",
                "days_left": 0,
                "date": _TODAY_PLACEHOLDER,
                "is_today": True,
            },
            "date": {"festival_solar": "立春"},
        },
    },
    "holiday_in_progress": {
        "description": "假期进行中",
        "overrides": {
            "holidays": [
                {
                    "name": "春节",
                    "start_date": _TODAY_PLACEHOLDER,
                    "end_date": "2026-02-23",
                    "duration": 9,
                    "days_left": 0,
                    "is_legal_holiday": True,
                    "is_off_day": True,
                }
            ]
        },
    },
    "makeup_workday": {
        "description": "补班日",
        "overrides": {
            "holidays": [
                {
                    "name": "春节（补班）",
                    "start_date": _TODAY_PLACEHOLDER,
                    "end_date": _TODAY_PLACEHOLDER,
                    "duration": 1,
                    "days_left": 0,
                    "is_legal_holiday": True,
                    "is_off_day": False,
                }
            ]
        },
    },
    "kfc_thursday": {
        "description": "疯狂星期四",
        "overrides": {
            "kfc_content": {
                "title": "CRAZY THURSDAY",
                "sub_title": "V我50",
                "content": "今天是疯狂星期四！V我50，请你吃炸鸡！工作再累也要犒劳自己，来份黄金脆皮鸡，外酥里嫩，一口下去烦恼全消！",
            }
        },
    },
    "stock_trading_day": {
        "description": "股票交易日",
        "overrides": {
            "stock_indices": {
                "indices": [
                    {
                        "code": "000001",
                        "name": "上证指数",
                        "price": 3268.45,
                        "change": 22.18,
                        "change_pct": 0.68,
                        "trend": "up",
                        "market": "A",
                        "is_trading_day": True,
                    },
                    {
                        "code": "399001",
                        "name": "深证成指",
                        "price": 10588.32,
                        "change": 45.67,
                        "change_pct": 0.43,
                        "trend": "up",
                        "market": "A",
                        "is_trading_day": True,
                    },
                ],
                "updated": "2026-02-05 10:30",
                "updated_at": 1770258600000,
                "trading_day": {"A": True, "HK": True, "US": False},
                "is_stale": False,
            }
        },
    },
    "stock_non_trading_day": {
        "description": "股票非交易日",
        "overrides": {
            "stock_indices": {
                "indices": [
                    {
                        "code": "000001",
                        "name": "上证指数",
                        "price": None,
                        "change": None,
                        "change_pct": None,
                        "trend": "flat",
                        "market": "A",
                        "is_trading_day": False,
                    }
                ],
                "updated": "2026-02-06 15:00",
                "updated_at": 1770361200000,
                "trading_day": {"A": False, "HK": False, "US": False},
                "is_stale": True,
            }
        },
    },
    "fallback_mode": {
        "description": "降级模式",
        "overrides": {"is_fallback_mode": True},
    },
    "lunar_festival": {
        "description": "农历节日",
        "overrides": {"date": {"festival_lunar": "元宵节"}},
    },
    "legal_holiday_today": {
        "description": "法定节假日当天",
        "overrides": {"date": {"legal_holiday": "春节", "is_holiday": True}},
    },
}

MIXED_OVERRIDES = {
    "weekend": {
        "days_left": 0,
        "is_weekend": True,
    },
    "solar_term": {
        "name": "立春",
        "name_en": "Beginning of Spring",
        "days_left": 0,
        "date": _TODAY_PLACEHOLDER,
        "is_today": True,
    },
    "holidays": [
        {
            "name": "春节（补班）",
            "start_date": _TODAY_PLACEHOLDER,
            "end_date": _TODAY_PLACEHOLDER,
            "duration": 1,
            "days_left": 0,
            "is_legal_holiday": True,
            "color": "#E67E22",
            "is_off_day": False,
        },
        {
            "name": "春节",
            "start_date": _TODAY_PLACEHOLDER,
            "end_date": "2026-02-08",
            "duration": 8,
            "days_left": 0,
            "is_legal_holiday": True,
            "color": "#E67E22",
            "is_off_day": True,
        },
        {
            "name": "清明节",
            "start_date": "2026-04-04",
            "end_date": "2026-04-06",
            "duration": 3,
            "days_left": 62,
            "is_legal_holiday": True,
            "color": "#E67E22",
            "is_off_day": True,
        },
        {
            "name": "劳动节",
            "start_date": "2026-05-01",
            "end_date": "2026-05-05",
            "duration": 5,
            "days_left": 89,
            "is_legal_holiday": True,
            "color": "#E67E22",
            "is_off_day": True,
        },
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染测试场景图片")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        help="渲染指定场景"
    )
    group.add_argument("--all", action="store_true", help="渲染全部场景")
    group.add_argument("--list", action="store_true", help="列出所有可用场景")
    return parser.parse_args()


def print_scenario_list() -> None:
    print("可用场景：")
    for name, data in SCENARIOS.items():
        print(f"  - {name}: {data['description']}")


def apply_scenario_overrides(base_data: dict, overrides: dict) -> dict:
    data = copy.deepcopy(base_data)

    def merge_dict(target: dict, patch: dict) -> dict:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                target[key] = merge_dict(target[key], value)
            else:
                target[key] = value
        return target

    return merge_dict(data, overrides)


async def get_base_template_data(
    data_fetcher: DataFetcher,
    holiday_service: HolidayService,
    fun_content_service: FunContentService,
    stock_index_service: StockIndexService | None,
    data_computer: DataComputer,
    logger,
) -> dict:
    raw_data = await data_fetcher.fetch_all()

    try:
        holidays = await holiday_service.fetch_holidays()
        raw_data["holidays"] = holidays
    except Exception as e:
        logger.warning(f"获取节假日失败: {e}")
        raw_data["holidays"] = []

    try:
        from datetime import date

        fun_content = await fun_content_service.fetch_content(date.today())
        raw_data["fun_content"] = fun_content
    except Exception as e:
        logger.warning(f"获取趣味内容失败: {e}")
        raw_data["fun_content"] = None

    raw_data["kfc_copy"] = None

    # 获取股票指数数据
    raw_data["stock_indices"] = None
    if stock_index_service:
        try:
            stock_indices = await stock_index_service.fetch_indices()
            raw_data["stock_indices"] = stock_indices
            logger.info(f"获取到 {len(stock_indices.get('items', []))} 条股票指数数据")
        except Exception as e:
            logger.warning(f"获取股票指数失败: {e}")

    return data_computer.compute(raw_data)


def build_output_filename(scenario_name: str | None, timestamp: str) -> str:
    if scenario_name:
        return f"moyuren_test_{scenario_name}_{timestamp}.jpg"
    return f"moyuren_test_{timestamp}.jpg"


async def render_scenario(
    scenario_name: str | None,
    template_data: dict,
    image_renderer: ImageRenderer,
    static_dir: str,
    logger,
) -> Path:
    filename = await image_renderer.render(template_data)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_name = build_output_filename(scenario_name, timestamp)

    source_path = Path(static_dir) / filename
    target_path = Path(static_dir) / output_name
    if source_path.exists():
        source_path.replace(target_path)
    else:
        logger.warning(f"未找到渲染输出文件: {source_path}")
        target_path = source_path
        output_name = filename

    logger.info(f"测试图片已生成: {output_name}")
    return target_path


async def main():
    """生成模拟特殊场景的测试图片"""
    args = parse_args()
    if args.list:
        print_scenario_list()
        return

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

    # Initialize stock index service
    stock_index_service = None
    if config.stock_index:
        stock_index_service = StockIndexService(config.stock_index)

    # Get templates configuration
    templates_config = config.get_templates_config()

    image_renderer = ImageRenderer(
        templates_config=templates_config,
        static_dir=config.paths.static_dir,
        render_config=config.render,
        logger=logger,
    )

    logger.info("开始生成测试场景图片...")

    # 获取当前日期（时区初始化后）
    today_str = get_today_str()

    base_template_data = await get_base_template_data(
        data_fetcher=data_fetcher,
        holiday_service=holiday_service,
        fun_content_service=fun_content_service,
        stock_index_service=stock_index_service,
        data_computer=data_computer,
        logger=logger,
    )

    if args.all:
        for scenario_name, scenario_data in SCENARIOS.items():
            logger.info(f"渲染场景: {scenario_name}")
            overrides = replace_today_placeholder(scenario_data["overrides"], today_str)
            scenario_template = apply_scenario_overrides(
                base_template_data,
                overrides,
            )
            image_path = await render_scenario(
                scenario_name,
                scenario_template,
                image_renderer,
                config.paths.static_dir,
                logger,
            )
            print(f"\n✅ 场景 {scenario_name} 已生成: {image_path.absolute()}")
            print(f"说明: {scenario_data['description']}")
        return

    if args.scenario:
        scenario_data = SCENARIOS[args.scenario]
        overrides = replace_today_placeholder(scenario_data["overrides"], today_str)
        scenario_template = apply_scenario_overrides(
            base_template_data,
            overrides,
        )
        image_path = await render_scenario(
            args.scenario,
            scenario_template,
            image_renderer,
            config.paths.static_dir,
            logger,
        )
        print(f"\n✅ 场景 {args.scenario} 已生成: {image_path.absolute()}")
        print(f"说明: {scenario_data['description']}")
        return

    mixed_overrides = replace_today_placeholder(MIXED_OVERRIDES, today_str)
    mixed_template = apply_scenario_overrides(base_template_data, mixed_overrides)
    logger.info("已覆盖测试数据：当日周末、当日节气、当日假日/补班")
    image_path = await render_scenario(
        None,
        mixed_template,
        image_renderer,
        config.paths.static_dir,
        logger,
    )

    print(f"\n✅ 测试图片已生成: {image_path.absolute()}")
    print("\n模拟场景：")
    print("  - 当日周末：🎉 周末愉快，摸鱼无罪！")
    print("  - 当日节气：今日 立春，顺应天时")
    print("  - 当日补班：😭 补班中")
    print("  - 当日假期：🥳 假期中")


if __name__ == "__main__":
    asyncio.run(main())
