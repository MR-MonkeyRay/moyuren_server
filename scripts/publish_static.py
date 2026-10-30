#!/usr/bin/env python3
"""为容器部署生成静态产物。"""

import argparse
import asyncio
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

# 将项目根目录加入导入路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import (
    CrazyThursdaySource,
    DailyEnglishSource,
    FunContentSource,
    GoldPriceSource,
    HolidaySource,
    NewsSource,
    StockIndexSource,
    load_config,
)
from app.core.errors import RenderError
from app.core.fileutil import set_output_permissions, write_atomic as write_output_atomic
from app.core.logging import setup_logging
from app.core.network import safe_exception_for_log
from app.models.schemas import DailyDataFile
from app.services.browser import browser_manager
from app.services.calendar import get_display_timezone, init_timezones, now_business
from app.services.compute import DataComputer
from app.services.fetcher import DataFetcher
from app.services.fun_content import FunContentService
from app.services.gold_price import GoldPriceService
from app.services.holiday import HolidayService
from app.services.kfc import KfcService
from app.services.renderer import ImageRenderer
from app.services.stock_index import StockIndexService
from app.services.daily_english import DailyEnglishService, build_dict_backend
from app.services.variants import generate_variants, image_width

_CACHE_VARIANT_PATTERN = re.compile(r"^(?:_w(?P<width>\d+))?\.(?P<extension>jpg|webp)$")


def _published_variant_name(source_stem: str, filename: str, prefix: str) -> str:
    """将缓存变体文件名映射为固定的发布产物名称。"""
    suffix = filename.removeprefix(source_stem)
    if suffix == ".jpg":
        return f"{prefix}.jpg"
    if suffix == ".webp":
        return f"{prefix}.webp"
    return f"{prefix}{suffix}"


def _existing_variant_files(source: Path) -> list[Path]:
    """查找同目录下所有有效图片格式及尺寸变体，包括按需生成的文件。"""
    candidates = [source, source.with_suffix(".webp")]
    candidates.extend(source.parent.glob(f"{source.stem}_w*.jpg"))
    candidates.extend(source.parent.glob(f"{source.stem}_w*.webp"))
    valid_paths: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        match = _CACHE_VARIANT_PATTERN.fullmatch(path.name.removeprefix(source.stem))
        if match is None:
            continue
        width = match.group("width")
        if width is not None and int(width) <= 0:
            continue
        valid_paths.append(path)
    return sorted(valid_paths)


def _published_variant_metadata(source: Path) -> list[dict[str, str | int]]:
    """根据磁盘上的所有图片格式及尺寸变体构建静态数据元信息。"""
    try:
        source_width = image_width(source)
    except RenderError:
        # 基础图无法读取时不发布派生图元数据；生成阶段已记录告警
        return []
    variants: list[dict[str, str | int]] = []
    for path in _existing_variant_files(source):
        match = _CACHE_VARIANT_PATTERN.fullmatch(path.name.removeprefix(source.stem))
        if match is None:
            continue
        configured_width = match.group("width")
        width = source_width if configured_width is None else int(configured_width)
        variants.append(
            {
                "filename": _published_variant_name(source.stem, path.name, "moyuren"),
                "format": "jpeg" if match.group("extension") == "jpg" else "webp",
                "width": width,
            }
        )
    return variants


def _latest_manifest(data: DailyDataFile) -> DailyDataFile:
    """构建根目录清单元信息，使用根目录图片产物的名称。"""
    payload = data.model_dump(mode="json", exclude_unset=True)
    payload["images"] = {
        template: _rename_published_prefix(filename, "moyuren", "latest")
        for template, filename in data.images.items()
    }
    if data.image_variants is not None:
        payload["image_variants"] = {
            template: [
                {
                    **variant.model_dump(mode="json"),
                    "filename": _rename_published_prefix(
                        variant.filename, "moyuren", "latest"
                    ),
                }
                for variant in variants
            ]
            for template, variants in data.image_variants.items()
        }
    return DailyDataFile.model_validate(payload)


def _rename_published_prefix(filename: str, source: str, target: str) -> str:
    """替换已发布图片及其变体共用的固定文件名前缀。"""
    return f"{target}{filename.removeprefix(source)}" if filename.startswith(source) else filename


def _publish_image_family(source: Path, date_dir: Path, output_dir: Path) -> int:
    """将原图及其变体复制为日期目录和最新产物的名称。"""
    copied = 0
    for source_file in _existing_variant_files(source):
        dated_name = _published_variant_name(source.stem, source_file.name, "moyuren")
        latest_name = _published_variant_name(source.stem, source_file.name, "latest")
        copy_atomic(source_file, date_dir / dated_name)
        copy_atomic(source_file, output_dir / latest_name)
        copied += 2
    return copied


def _build_timestamp_fields(now: datetime) -> dict[str, str | int | bool]:
    """根据同一业务时间快照构建输出时间戳。"""
    display_now = now.astimezone(get_display_timezone())
    business_date = now.date()
    return {
        "date": business_date.isoformat(),
        "updated": display_now.strftime("%Y/%m/%d %H:%M:%S"),
        "updated_at": int(now.timestamp() * 1000),
        "is_crazy_thursday": business_date.weekday() == 3,
    }


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="生成摸鱼日历静态产物"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="/output",
        help="产物输出目录（默认：/output）",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="https://moyuren.pages.dev",
        help="静态站点基础 URL（默认：https://moyuren.pages.dev）",
    )
    return parser.parse_args()


def write_atomic(content: str, target_path: Path) -> None:
    """将内容原子写入文件。"""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    write_output_atomic(target_path, content)


def copy_atomic(src_path: Path, target_path: Path) -> None:
    """原子复制文件。"""
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
        set_output_permissions(tmp_path)
        os.replace(tmp_path, target_path)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def generate_txt(
    data: dict, base_url: str, image_path: str = "latest.jpg"
) -> str:
    """生成 TXT 格式产物。"""
    lines = []

    # 标题和页头
    lines.append(f"摸鱼日历 {data['date']}")
    lines.append("=" * 40)

    # 日期信息
    weekday = data.get("weekday", "")
    lunar_date = data.get("lunar_date", "")
    if weekday or lunar_date:
        date_line = (
            f"{weekday} | 农历 {lunar_date}"
            if weekday and lunar_date
            else (weekday or lunar_date)
        )
        lines.append(date_line)
        lines.append("")

    # 假期倒计时
    holidays = data.get("holidays", [])
    if holidays:
        lines.append("假期倒计时：")
        for holiday in holidays:
            if isinstance(holiday, dict):
                name = holiday.get("name", "")
                days_left = holiday.get("days_left", "")
                duration = holiday.get("duration", "")
                if name:
                    lines.append(f"  {name}：还有 {days_left} 天（{duration}天假期）")
        lines.append("")

    # 新闻列表
    news_list = data.get("news_list", [])
    if news_list:
        lines.append("今日热点：")
        for news in news_list:
            if news:
                lines.append(f"  - {news}")
        lines.append("")

    # 趣味内容
    fun_content = data.get("fun_content")
    if fun_content and isinstance(fun_content, dict):
        title = fun_content.get("title", "")
        text = fun_content.get("text", "")
        if title and text:
            lines.append(f"{title}: {text}")
            lines.append("")

    # KFC 文案（仅周四）
    if data.get("is_crazy_thursday"):
        kfc_content = data.get("kfc_content")
        if kfc_content:
            lines.append(f"疯狂星期四: {kfc_content}")
            lines.append("")

    # 页脚
    lines.append(f"更新时间: {data['updated']}")
    lines.append(f"图片: {base_url}/{image_path}")
    lines.append("")

    return "\n".join(lines)


def generate_md(
    data: dict, base_url: str, image_path: str = "latest.jpg"
) -> str:
    """生成 Markdown 格式产物。"""
    lines = []

    # 标题
    lines.append(f"# 摸鱼日历 - {data['date']}")
    lines.append("")

    # 日期信息
    weekday = data.get("weekday", "")
    lunar_date = data.get("lunar_date", "")
    if weekday or lunar_date:
        date_line = (
            f"{weekday} | 农历 {lunar_date}"
            if weekday and lunar_date
            else (weekday or lunar_date)
        )
        lines.append(f"**{date_line}**")
        lines.append("")

    # 图片
    lines.append(f"![摸鱼日历]({base_url}/{image_path})")
    lines.append("")

    # 假期倒计时
    holidays = data.get("holidays", [])
    if holidays:
        lines.append("## 假期倒计时")
        lines.append("")
        for holiday in holidays:
            if isinstance(holiday, dict):
                name = holiday.get("name", "")
                days_left = holiday.get("days_left", "")
                duration = holiday.get("duration", "")
                if name:
                    lines.append(f"- {name}：还有 {days_left} 天（{duration}天假期）")
        lines.append("")

    # 新闻列表
    news_list = data.get("news_list", [])
    if news_list:
        lines.append("## 今日热点")
        lines.append("")
        for idx, news in enumerate(news_list, 1):
            if news:
                lines.append(f"{idx}. {news}")
        lines.append("")

    # 趣味内容
    fun_content = data.get("fun_content")
    if fun_content and isinstance(fun_content, dict):
        title = fun_content.get("title", "")
        text = fun_content.get("text", "")
        if title and text:
            lines.append(f"> **{title}**")
            lines.append(">")
            lines.append(f"> {text}")
            lines.append("")

    # KFC 文案（仅周四）
    if data.get("is_crazy_thursday"):
        kfc_content = data.get("kfc_content")
        if kfc_content:
            lines.append("> **疯狂星期四**")
            lines.append(">")
            lines.append(f"> {kfc_content}")
            lines.append("")

    # 页脚
    lines.append(f"*更新时间: {data['updated']}*")
    lines.append("")

    return "\n".join(lines)


async def main() -> None:
    """生成静态产物。"""
    args = parse_args()
    output_dir = Path(args.output)
    base_url = args.base_url.rstrip("/")

    # 加载配置
    config = load_config()
    logger = setup_logging(config.logging, logger_name="publish_static")
    browser_manager.configure(logger, proxy_url=config.network.proxy_url)
    stock_index_service = None
    try:
        logger.info("Output directory: %s", output_dir)
        logger.info("Base URL: %s", base_url)

        # 初始化时区
        init_timezones(
            business_tz=config.timezone.business, display_tz=config.timezone.display
        )
        now = now_business()
        business_date = now.date()

        # 确保目录存在
        cache_dir = Path(config.paths.cache_dir)
        (cache_dir / "images").mkdir(parents=True, exist_ok=True)
        (cache_dir / "data").mkdir(parents=True, exist_ok=True)
        (cache_dir / "holidays").mkdir(parents=True, exist_ok=True)

        # 初始化服务
        news_source = config.get_source(NewsSource)
        data_fetcher = DataFetcher(
            source=news_source,
            logger=logger,
            proxy_url=config.network.proxy_url,
            ghproxy_urls=config.network.ghproxy_urls,
            instances=config.instances,
        )
        holiday_cache_dir = cache_dir / "holidays"
        holiday_source = config.get_source(HolidaySource)
        holiday_service: HolidayService | None = None
        if holiday_source:
            holiday_service = HolidayService(
                logger=logger,
                cache_dir=holiday_cache_dir,
                ghproxy_urls=config.network.ghproxy_urls,
                timeout_sec=holiday_source.timeout_sec,
                proxy_url=config.network.proxy_url,
            )
        fun_content_source = config.get_source(FunContentSource)
        fun_content_service = FunContentService(
            fun_content_source, proxy_url=config.network.proxy_url, instances=config.instances
        )

        # 有配置时初始化 KFC 服务
        kfc_service = None
        crazy_thursday_source = config.get_source(CrazyThursdaySource)
        if crazy_thursday_source:
            kfc_service = KfcService(
                crazy_thursday_source, proxy_url=config.network.proxy_url, instances=config.instances
            )

        # 有配置时初始化股票指数服务
        stock_index_service = None
        stock_index_source = config.get_source(StockIndexSource)
        if stock_index_source:
            stock_index_service = StockIndexService(
                stock_index_source, proxy_url=config.network.proxy_url
            )

        # 有配置时初始化金价服务
        gold_price_service = None
        gold_price_source = config.get_source(GoldPriceSource)
        if gold_price_source:
            gold_price_service = GoldPriceService(
                gold_price_source, proxy_url=config.network.proxy_url, instances=config.instances
            )

        # 有配置时初始化每日英语服务
        daily_english_service = None
        daily_english_source = config.get_source(DailyEnglishSource)
        if daily_english_source and daily_english_source.enabled:
            dict_backend = build_dict_backend(
                cfg=daily_english_source.backend,
                cache_dir=config.paths.cache_dir,
                ghproxy_urls=config.network.ghproxy_urls,
                logger=logger,
                proxy_url=config.network.proxy_url,
            )
            daily_english_service = DailyEnglishService(
                config=daily_english_source,
                backend=dict_backend,
                logger=logger,
                proxy_url=config.network.proxy_url,
            )

        data_computer = DataComputer(
            text_limits=config.templates.config.text_limits,
            work_end_time=config.templates.config.work_end_time,
            themes=config.templates.config.themes,
        )

        # 获取模板配置
        templates_config = config.get_templates_config()

        image_renderer = ImageRenderer(
            templates_config=templates_config,
            images_dir=str(cache_dir / "images"),
            render_config=config.templates.config,
            logger=logger,
            proxy_url=config.network.proxy_url,
        )

        logger.info("Starting artifact generation...")

        try:
            # 1. 获取新闻
            raw_data = {"news": await data_fetcher.fetch()}

            # 1.1 获取节假日数据
            raw_data["holidays"] = []
            raw_data["make_up_workdays"] = []
            if holiday_service:
                try:
                    holidays = await holiday_service.fetch_holidays()
                    raw_data["holidays"] = holidays if holidays is not None else []
                    raw_data["make_up_workdays"] = holiday_service.make_up_workdays
                    if holidays is None:
                        logger.warning("Failed to fetch holidays, using default")
                    else:
                        logger.info("Fetched %s holidays", len(holidays))
                except Exception as e:
                    logger.warning(
                        "Failed to fetch holidays, using default: %s",
                        safe_exception_for_log(e, config.network.proxy_url),
                    )

            # 1.2 获取趣味内容
            try:
                fun_content = await fun_content_service.fetch_content(business_date)
                raw_data["fun_content"] = fun_content
                logger.info("Fetched fun content: %s", fun_content.get("title"))
            except Exception as e:
                logger.warning(
                    "Failed to fetch fun content, using default: %s",
                    safe_exception_for_log(e, config.network.proxy_url),
                )
                raw_data["fun_content"] = None

            # 1.3 获取 KFC 疯狂星期四文案（仅周四）
            raw_data["kfc_copy"] = None
            if kfc_service and business_date.weekday() == 3:
                try:
                    kfc_copy = await kfc_service.fetch_kfc_copy()
                    raw_data["kfc_copy"] = kfc_copy
                    if kfc_copy:
                        logger.info("Fetched KFC Crazy Thursday content")
                except Exception as e:
                    logger.warning(
                        "Failed to fetch KFC content: %s",
                        safe_exception_for_log(e, config.network.proxy_url),
                    )

            # 1.4 获取股票指数数据
            raw_data["stock_indices"] = None
            if stock_index_service:
                try:
                    stock_indices = await stock_index_service.fetch_indices()
                    raw_data["stock_indices"] = stock_indices
                    logger.info("Fetched %s stock indices", len(stock_indices.get("items", [])))
                except Exception as e:
                    logger.warning(
                        "Failed to fetch stock indices: %s",
                        safe_exception_for_log(e, config.network.proxy_url),
                    )

            # 1.5 获取金价数据
            raw_data["gold_price"] = None
            if gold_price_service:
                try:
                    gold_price = await gold_price_service.fetch_gold_price()
                    raw_data["gold_price"] = gold_price
                    if gold_price:
                        logger.info("Fetched gold price: %s", gold_price.get("today_price"))
                except Exception as e:
                    logger.warning(
                        "Failed to fetch gold price: %s",
                        safe_exception_for_log(e, config.network.proxy_url),
                    )

            # 1.6 获取每日英语
            raw_data["daily_english"] = None
            if daily_english_service:
                try:
                    await daily_english_service.ensure_ready()
                    daily_english = await daily_english_service.fetch_daily_word()
                    raw_data["daily_english"] = (
                        dict(daily_english) if daily_english else None
                    )
                    if daily_english:
                        logger.info("Fetched daily english: %s", daily_english.get("word"))
                except Exception as e:
                    logger.warning(
                        "Failed to fetch daily english: %s",
                        safe_exception_for_log(e, config.network.proxy_url),
                    )

            # 2. 计算模板上下文
            template_data = data_computer.compute(raw_data, now=now)
            logger.info("Template data computed")

            # 3. 渲染图片
            filename = await image_renderer.render(template_data, now=now)
            logger.info("Image rendered: %s", filename)

            src_image = cache_dir / "images" / filename
            try:
                await asyncio.to_thread(
                    generate_variants, src_image, config.templates.config, logger
                )
            except Exception as e:
                logger.warning("Failed to generate image variants for %s: %s", filename, e)

        except Exception as e:
            logger.error(
                "Rendering failed: %s",
                safe_exception_for_log(e, config.network.proxy_url),
            )
            sys.exit(10)

        # 4. 构建数据字典
        timestamp_fields = _build_timestamp_fields(now)
        today_str = business_date.isoformat()

        # 从 template_data 提取数据
        date_info = template_data.get("date", {})
        fun_content_raw = template_data.get("history", {})
        kfc_content_raw = template_data.get("kfc_content")

        # 构建 fun_content
        stored_fun_content: dict[str, str] | None = None
        if fun_content_raw:
            title = fun_content_raw.get("title", "")
            content_type = fun_content_raw.get("type")
            if not isinstance(content_type, str) or not content_type:
                content_type = "unknown"
                if "冷笑话" in title:
                    content_type = "dad_joke"
                elif "一言" in title:
                    content_type = "hitokoto"
                elif "段子" in title:
                    content_type = "duanzi"
                elif "摸鱼" in title:
                    content_type = "moyu_quote"

            stored_fun_content = {
                "type": content_type,
                "title": title,
                "text": fun_content_raw.get("content", ""),
            }

        # 构建 KFC 文案
        kfc_content = None
        if kfc_content_raw and isinstance(kfc_content_raw, dict):
            kfc_content = kfc_content_raw.get("content")

        data_payload: dict[str, Any] = {
            **timestamp_fields,
            "images": {"moyuren": "moyuren.jpg"},
            "image_variants": {"moyuren": _published_variant_metadata(src_image)},
            "weekday": date_info.get("week_cn", ""),
            "lunar_date": date_info.get("lunar_date", ""),
            "fun_content": stored_fun_content,
            "kfc_content": kfc_content,
            # 完整渲染数据字段
            "date_info": template_data.get("date"),
            "weekend": template_data.get("weekend"),
            "solar_term": template_data.get("solar_term"),
            "guide": template_data.get("guide"),
            "news_list": [
                item.get("text", "")
                for item in template_data.get("news_list", [])
                if isinstance(item, dict)
            ],
            "news_meta": template_data.get("news_meta"),
            "holidays": [
                {k: v for k, v in h.items() if k != "color"}
                for h in template_data.get("holidays", [])
                if isinstance(h, dict)
            ],
            "kfc_content_full": template_data.get("kfc_content"),
            "stock_indices": raw_data.get("stock_indices"),
            "is_stale": template_data.get("data_freshness", {}).get("is_stale", False),
            "source_date": template_data.get("data_freshness", {}).get("source_date"),
            "stale_sources": template_data.get("data_freshness", {}).get("stale_sources", []),
        }
        data_file = DailyDataFile.model_validate(data_payload)
        data = data_file.model_dump(mode="json")

        # 5. 在输出目录生成产物
        try:
            date_dir = output_dir / today_str
            date_dir.mkdir(parents=True, exist_ok=True)

            # JSON 产物
            write_atomic(data_file.dump_json(), date_dir / "data.json")
            write_atomic(_latest_manifest(data_file).dump_json(), output_dir / "latest.json")
            logger.info(
                "JSON artifacts written: %s, %s", date_dir / "data.json", output_dir / "latest.json"
            )

            # 图片产物（从缓存复制）
            if not src_image.exists():
                logger.error("Source image not found: %s", src_image)
                sys.exit(11)

            published_images = _publish_image_family(src_image, date_dir, output_dir)
            logger.info(
                "Image artifacts copied: %s, %s",
                date_dir / "moyuren.jpg",
                output_dir / "latest.jpg",
            )
            logger.info("Published %s image artifact(s)", published_images)

            # TXT 产物
            dated_image_path = f"{today_str}/moyuren.jpg"
            dated_txt_content = generate_txt(data, base_url, dated_image_path)
            latest_txt_content = generate_txt(data, base_url)
            write_atomic(dated_txt_content, date_dir / "data.txt")
            write_atomic(latest_txt_content, output_dir / "latest.txt")
            logger.info(
                "TXT artifacts written: %s, %s", date_dir / "data.txt", output_dir / "latest.txt"
            )

            # Markdown 产物
            dated_md_content = generate_md(data, base_url, dated_image_path)
            latest_md_content = generate_md(data, base_url)
            write_atomic(dated_md_content, date_dir / "data.md")
            write_atomic(latest_md_content, output_dir / "latest.md")
            logger.info(
                "Markdown artifacts written: %s, %s", date_dir / "data.md", output_dir / "latest.md"
            )

        except Exception as e:
            logger.error("Failed to write artifacts: %s", e, exc_info=True)
            sys.exit(11)

        # 6. 校验产物
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
                logger.error("Validation failed: missing files: %s", missing_files)
                sys.exit(12)

            logger.info("Artifact validation passed")

        except Exception as e:
            logger.error("Validation error: %s", e, exc_info=True)
            sys.exit(12)

        # 7. 输出摘要
        image_size = (output_dir / "latest.jpg").stat().st_size / 1024
        json_size = (output_dir / "latest.json").stat().st_size / 1024

        print("\n✅ Static artifacts generated successfully")
        print(f"   Date: {today_str}")
        print(f"   Output: {output_dir}")
        print(f"   Image: {image_size:.1f} KB")
        print(f"   JSON: {json_size:.1f} KB")
        print(f"   Artifacts: 8 files (4 in {today_str}/, 4 in root)")
    finally:
        if stock_index_service is not None:
            try:
                await stock_index_service.close()
            except Exception as e:
                logger.warning(
                    "Failed to close stock index service: %s", safe_exception_for_log(e)
                )
        try:
            await browser_manager.shutdown()
        except Exception as e:
            logger.warning(
                "Failed to shutdown browser manager: %s", safe_exception_for_log(e)
            )


if __name__ == "__main__":
    asyncio.run(main())
