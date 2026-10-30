"""app/services/template_discovery.py 模板发现服务测试。"""

from pathlib import Path

import pytest

from app.core.config import TemplateRenderConfig
from app.services.template_discovery import TemplateDiscovery


def _create_template(dir_path: Path, name: str, meta_tags: str = "") -> Path:
    """在 dir_path 下创建带 meta 标签的模板文件。

    Args:
        dir_path: 模板目录路径
        name: 模板文件名（不含 .html 后缀）
        meta_tags: meta 标签 HTML 字符串

    Returns:
        创建的文件路径
    """
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    {meta_tags}
    <title>Test</title>
</head>
<body><p>Hello</p></body>
</html>'''
    file_path = dir_path / f"{name}.html"
    file_path.write_text(html, encoding="utf-8")
    return file_path


class TestTemplateDiscovery:
    """TemplateDiscovery 类测试。"""

    @pytest.fixture
    def discovery(self) -> TemplateDiscovery:
        """创建 TemplateDiscovery 实例。"""
        return TemplateDiscovery()

    @pytest.fixture
    def global_config(self) -> TemplateRenderConfig:
        """创建全局渲染配置。"""
        return TemplateRenderConfig(
            device_scale_factor=3,
            jpeg_quality=100,
            use_china_cdn=True,
        )

    def test_discover_normal_templates(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ):
        """测试正常扫描：两个模板都有完整 meta 标签。"""
        # 创建两个模板
        _create_template(
            tmp_path,
            "template1",
            '''
            <meta name="moyuren:viewport-width" content="1200">
            <meta name="moyuren:viewport-height" content="800">
            <meta name="moyuren:device-scale-factor" content="2">
            <meta name="moyuren:jpeg-quality" content="90">
            <meta name="moyuren:show-kfc" content="false">
            <meta name="moyuren:show-stock" content="true">
            ''',
        )
        _create_template(
            tmp_path,
            "template2",
            '''
            <meta name="moyuren:viewport-width" content="1000">
            <meta name="moyuren:viewport-height" content="600">
            ''',
        )

        # 执行扫描
        items = discovery.discover(str(tmp_path), global_config)

        # 验证结果
        assert len(items) == 2
        assert items[0].name == "template1"
        assert items[1].name == "template2"

        # 验证 template1 配置
        assert items[0].viewport.width == 1200
        assert items[0].viewport.height == 800
        assert items[0].device_scale_factor == 2
        assert items[0].jpeg_quality == 90
        assert items[0].show_kfc is False
        assert items[0].show_stock is True

        # 验证 template2 配置（使用默认值）
        assert items[1].viewport.width == 1000
        assert items[1].viewport.height == 600
        assert items[1].device_scale_factor == 3  # 使用 global_config
        assert items[1].jpeg_quality == 100  # 使用 global_config
        assert items[1].show_kfc is True  # 默认值
        assert items[1].show_stock is True  # 默认值

    def test_discover_partial_meta(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ):
        """测试部分 meta：缺少某些字段时使用默认值。"""
        _create_template(
            tmp_path,
            "minimal",
            # 只提供 viewport，其他使用默认值
            '''
            <meta name="moyuren:viewport-width" content="1024">
            <meta name="moyuren:viewport-height" content="768">
            ''',
        )

        items = discovery.discover(str(tmp_path), global_config)

        assert len(items) == 1
        assert items[0].name == "minimal"
        assert items[0].viewport.width == 1024
        assert items[0].viewport.height == 768
        assert items[0].device_scale_factor == 3  # global_config
        assert items[0].jpeg_quality == 100  # global_config
        assert items[0].show_kfc is True  # 默认值
        assert items[0].show_stock is True  # 默认值

    def test_discover_description_strips_truncates_and_defaults_to_none(
        self,
        discovery: TemplateDiscovery,
        global_config: TemplateRenderConfig,
        tmp_path: Path,
    ) -> None:
        """描述 meta 去除首尾空白、限制长度，缺失时为 None。"""
        _create_template(
            tmp_path,
            "described",
            f'<meta name="moyuren:description" content="  {"简" * 205}  ">',
        )
        _create_template(tmp_path, "plain")

        items = {
            item.name: item
            for item in discovery.discover(str(tmp_path), global_config)
        }

        assert items["described"].description == "简" * 200
        assert items["plain"].description is None

    def test_production_templates_have_matching_feature_meta(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig
    ) -> None:
        """两个正式模板声明同一组功能 meta，并如实标注每日英语。"""
        templates_dir = Path(__file__).resolve().parents[2] / "templates"

        items = {item.name: item for item in discovery.discover(str(templates_dir), global_config)}

        assert items["moyuren"].show_kfc is True
        assert items["moyuren"].show_stock is True
        assert items["moyuren"].show_daily_english is False
        assert items["moyuren_cute"].show_kfc is True
        assert items["moyuren_cute"].show_stock is True
        assert items["moyuren_cute"].show_daily_english is True
        assert items["moyuren"].description == "杂志风格版摸鱼日历，清晰呈现日期、热点与生活信息。"
        assert items["moyuren_cute"].description == "棉花糖波普版摸鱼日历，以明快可爱的卡片展示每日信息。"

    def test_discover_skips_meta_without_attribute_values(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ) -> None:
        """格式错误的 meta 标签被忽略，有效标签仍然生效。"""
        _create_template(
            tmp_path,
            "partial",
            '''
            <meta name="moyuren:viewport-width">
            <meta name>
            <meta name content="1024">
            <meta name="moyuren:viewport-height" content="768">
            ''',
        )

        items = discovery.discover(str(tmp_path), global_config)

        assert len(items) == 1
        assert items[0].name == "partial"
        assert items[0].viewport.width == 794
        assert items[0].viewport.height == 768
        assert items[0].device_scale_factor == global_config.device_scale_factor

    def test_discover_no_meta(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ):
        """测试无 meta 标签时使用 ViewportConfig 默认值。"""
        _create_template(tmp_path, "nometa", "")

        items = discovery.discover(str(tmp_path), global_config)

        assert len(items) == 1
        assert items[0].name == "nometa"
        assert items[0].viewport.width == 794  # ViewportConfig 默认值
        assert items[0].viewport.height == 1123  # ViewportConfig 默认值
        assert items[0].device_scale_factor == 3  # global_config
        assert items[0].jpeg_quality == 100  # global_config

    def test_discover_skip_invalid_filename(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ):
        """测试非法文件名被跳过。"""
        # 创建合法模板
        _create_template(tmp_path, "valid", "")

        # 创建非法文件名模板
        invalid_file = tmp_path / "bad file.html"
        invalid_file.write_text("<html><head></head><body></body></html>", encoding="utf-8")

        items = discovery.discover(str(tmp_path), global_config)

        # 只返回合法模板
        assert len(items) == 1
        assert items[0].name == "valid"

    def test_discover_skip_invalid_meta_value(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ):
        """测试 meta 值类型错误时跳过该模板。"""
        # 创建合法模板
        _create_template(tmp_path, "valid", "")

        # 创建 meta 值类型错误的模板
        _create_template(
            tmp_path,
            "invalid",
            '''
            <meta name="moyuren:viewport-width" content="abc">
            ''',
        )

        items = discovery.discover(str(tmp_path), global_config)

        # 只返回合法模板
        assert len(items) == 1
        assert items[0].name == "valid"

    def test_discover_empty_directory(self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path):
        """测试空目录抛出 ValueError。"""
        with pytest.raises(ValueError, match="No valid templates found"):
            discovery.discover(str(tmp_path), global_config)

    def test_discover_directory_not_found(self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig):
        """测试目录不存在抛出 ValueError。"""
        with pytest.raises(ValueError, match="Templates directory not found"):
            discovery.discover("/nonexistent/path", global_config)

    def test_discover_sorted_by_name(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ):
        """测试结果按 name 排序。"""
        # 创建模板，文件名故意乱序
        _create_template(tmp_path, "z_last", "")
        _create_template(tmp_path, "a_first", "")
        _create_template(tmp_path, "m_middle", "")

        items = discovery.discover(str(tmp_path), global_config)

        # 验证按 name 排序
        assert len(items) == 3
        assert items[0].name == "a_first"
        assert items[1].name == "m_middle"
        assert items[2].name == "z_last"

    def test_discover_meta_attribute_order(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ):
        """测试属性顺序不同仍能解析：content 在前，name 在后。"""
        _create_template(
            tmp_path,
            "reversed",
            '''
            <meta content="1920" name="moyuren:viewport-width">
            <meta content="1080" name="moyuren:viewport-height">
            ''',
        )

        items = discovery.discover(str(tmp_path), global_config)

        assert len(items) == 1
        assert items[0].viewport.width == 1920
        assert items[0].viewport.height == 1080

    def test_discover_global_config_fallback(
        self, discovery: TemplateDiscovery, tmp_path: Path
    ):
        """测试全局配置 fallback 生效：meta 无 device-scale-factor 时使用 global_config 的值。"""
        # 创建自定义 global_config
        custom_config = TemplateRenderConfig(
            device_scale_factor=5,
            jpeg_quality=85,
            use_china_cdn=False,
        )

        _create_template(
            tmp_path,
            "fallback",
            # 不提供 device-scale-factor 和 jpeg-quality
            '''
            <meta name="moyuren:viewport-width" content="800">
            <meta name="moyuren:viewport-height" content="600">
            ''',
        )

        items = discovery.discover(str(tmp_path), custom_config)

        assert len(items) == 1
        assert items[0].device_scale_factor == 5  # 使用 custom_config
        assert items[0].jpeg_quality == 85  # 使用 custom_config

    def test_discover_invalid_bool_value(
        self, discovery: TemplateDiscovery, global_config: TemplateRenderConfig, tmp_path: Path
    ):
        """测试布尔值严格校验：非 true/false 值会导致模板被跳过。"""
        # 创建合法模板
        _create_template(tmp_path, "valid", "")

        # 创建布尔值错误的模板
        _create_template(
            tmp_path,
            "invalid_bool",
            '''
            <meta name="moyuren:show-kfc" content="yes">
            ''',
        )

        items = discovery.discover(str(tmp_path), global_config)

        # 只返回合法模板
        assert len(items) == 1
        assert items[0].name == "valid"
