"""Template auto-discovery service module."""

import logging
import re
from html.parser import HTMLParser
from pathlib import Path

from app.core.config import TemplateItemConfig, TemplateRenderConfig, ViewportConfig

logger = logging.getLogger(__name__)

# 合法模板文件名模式
_TEMPLATE_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.html$")

_READ_LIMIT = 16384  # 读取文件前 16KB


class _MetaParser(HTMLParser):
    """解析 HTML <head> 段中的 moyuren:* meta 标签。"""

    def __init__(self):
        super().__init__()
        self.meta_data: dict[str, str] = {}
        self.in_head = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "head":
            self.in_head = True
        elif tag.lower() == "meta" and self.in_head:
            attrs_dict = dict(attrs)
            name = attrs_dict.get("name", "")
            content = attrs_dict.get("content")
            if name.lower().startswith("moyuren:") and content is not None:
                key = name[8:].lower()  # 去掉 "moyuren:" 前缀
                self.meta_data[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "head":
            self.in_head = False


class TemplateDiscovery:
    """扫描 templates 目录，解析 HTML meta 标签，返回 TemplateItemConfig 列表。"""

    def discover(self, templates_dir: str, global_config: TemplateRenderConfig) -> list[TemplateItemConfig]:
        """扫描目录，返回按文件名排序的模板配置列表。

        Args:
            templates_dir: 模板目录路径
            global_config: 全局渲染配置（作为 fallback）

        Returns:
            按 name 排序的 TemplateItemConfig 列表

        Raises:
            ValueError: 目录为空或无有效模板
        """
        # 规范化为绝对路径
        dir_path = Path(templates_dir).resolve()
        if not dir_path.is_dir():
            raise ValueError(f"Templates directory not found: {templates_dir}")

        items: list[TemplateItemConfig] = []

        for html_file in sorted(dir_path.glob("*.html")):
            filename = html_file.name

            # 跳过不合法文件名
            if not _TEMPLATE_FILENAME_PATTERN.match(filename):
                logger.warning(f"Skipping template with invalid filename: {filename}")
                continue

            try:
                meta = self._parse_meta(html_file)
                item = self._build_item(html_file, meta, global_config)
                items.append(item)
                logger.info(f"Discovered template: {item.name} ({html_file})")
            except UnicodeDecodeError as e:
                logger.warning(f"Skipping template {filename}: encoding error - {e}")
                continue
            except ValueError as e:
                logger.warning(f"Skipping template {filename}: configuration error - {e}")
                continue
            except OSError as e:
                logger.warning(f"Skipping template {filename}: file read error - {e}")
                continue
            except Exception:
                logger.exception(f"Skipping template {filename}: unexpected error")
                continue

        if not items:
            raise ValueError(f"No valid templates found in: {templates_dir}")

        return items

    def _parse_meta(self, html_path: Path) -> dict[str, str]:
        """使用 HTMLParser 提取 <head> 段中的 moyuren meta 标签。

        只读取文件前 16KB，解析 moyuren:* meta 标签。
        """
        with open(html_path, encoding="utf-8") as f:
            content = f.read(_READ_LIMIT)
        parser = _MetaParser()
        parser.feed(content)
        return parser.meta_data

    def _parse_bool(self, value: str, field_name: str) -> bool:
        """严格解析布尔值，仅接受 true/false（大小写不敏感）。

        Args:
            value: 待解析的字符串
            field_name: 字段名（用于错误提示）

        Returns:
            布尔值

        Raises:
            ValueError: 值不是 true 或 false
        """
        lower_value = value.lower()
        if lower_value == "true":
            return True
        elif lower_value == "false":
            return False
        else:
            raise ValueError(f"Invalid boolean value for {field_name}: '{value}' (must be 'true' or 'false')")

    def _build_item(
        self, html_path: Path, meta: dict[str, str], global_config: TemplateRenderConfig
    ) -> TemplateItemConfig:
        """构建 TemplateItemConfig，优先级：meta > global_config > ViewportConfig 默认值。"""
        name = html_path.stem  # 文件名去掉 .html 后缀

        # viewport: meta > ViewportConfig 默认值
        default_viewport = ViewportConfig()
        viewport_width = int(meta.get("viewport-width", str(default_viewport.width)))
        viewport_height = int(meta.get("viewport-height", str(default_viewport.height)))

        # device_scale_factor: meta > global_config
        dsf_str = meta.get("device-scale-factor")
        device_scale_factor = int(dsf_str) if dsf_str is not None else global_config.device_scale_factor

        # jpeg_quality: meta > global_config
        jq_str = meta.get("jpeg-quality")
        jpeg_quality = int(jq_str) if jq_str is not None else global_config.jpeg_quality

        # show_kfc / show_stock: meta > 默认 true，严格校验
        show_kfc = self._parse_bool(meta.get("show-kfc", "true"), "show-kfc")
        show_stock = self._parse_bool(meta.get("show-stock", "true"), "show-stock")
        show_daily_english = self._parse_bool(meta.get("show-daily-english", "true"), "show-daily-english")

        return TemplateItemConfig(
            name=name,
            path=str(html_path),
            viewport=ViewportConfig(width=viewport_width, height=viewport_height),
            device_scale_factor=device_scale_factor,
            jpeg_quality=jpeg_quality,
            show_kfc=show_kfc,
            show_stock=show_stock,
            show_daily_english=show_daily_english,
        )
