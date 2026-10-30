"""本地字体包字符集辅助函数的单元测试。"""

from pathlib import Path

from scripts.build_fonts import (
    extract_template_non_ascii_characters,
    gb2312_hanzi_characters,
    gb2312_level_1_characters,
)


def test_gb2312_hanzi_level_counts() -> None:
    """GB2312 辅助集合应与标准汉字数量一致。"""
    level_1 = gb2312_level_1_characters()
    all_hanzi = gb2312_hanzi_characters()

    assert len(level_1) == 3755
    assert len(all_hanzi) == 6763
    assert level_1 < all_hanzi


def test_extract_template_non_ascii_characters(tmp_path: Path) -> None:
    """模板提取应收集每个 HTML 文件中的非 ASCII 字面量。"""
    (tmp_path / "first.html").write_text("<p>摸鱼日历 · café</p>", encoding="utf-8")
    (tmp_path / "second.html").write_text("<span>温度 23° → 晴</span>", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("不应读取", encoding="utf-8")

    characters = extract_template_non_ascii_characters(tmp_path)

    assert characters == set("摸鱼日历·é温度°→晴")
