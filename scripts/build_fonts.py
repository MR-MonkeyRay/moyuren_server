#!/usr/bin/env python3
"""Build deterministic, subsetted WOFF2 fonts for local template rendering."""

import argparse
import ast
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "templates" / "fonts"


@dataclass(frozen=True)
class FontSpec:
    """Describe one source font and its generated CSS face."""

    source_name: str
    output_name: str
    family: str
    weight: str
    charset: str


FONT_SPECS = (
    FontSpec(
        "NotoSansSC[wght].ttf",
        "moyuren-noto-sans-sc.woff2",
        "Moyuren Noto Sans SC",
        "100 900",
        "full_cjk",
    ),
    FontSpec(
        "LXGWWenKai-Regular.ttf",
        "moyuren-lxgw-wenkai-regular.woff2",
        "Moyuren LXGW WenKai",
        "400",
        "level_1_cjk",
    ),
    FontSpec(
        "LXGWWenKai-Medium.ttf",
        "moyuren-lxgw-wenkai-medium.woff2",
        "Moyuren LXGW WenKai",
        "700",
        "level_1_cjk",
    ),
    FontSpec(
        "ZCOOLKuaiLe-Regular.ttf",
        "moyuren-zcool-kuaile.woff2",
        "Moyuren ZCOOL KuaiLe",
        "400",
        "level_1_cjk",
    ),
    FontSpec(
        "JetBrainsMono[wght].ttf",
        "moyuren-jetbrains-mono.woff2",
        "Moyuren JetBrains Mono",
        "100 800",
        "latin",
    ),
    FontSpec(
        "Nunito[wght].ttf",
        "moyuren-nunito.woff2",
        "Moyuren Nunito",
        "200 1000",
        "latin",
    ),
    FontSpec(
        "Poppins-Regular.ttf",
        "moyuren-poppins-regular.woff2",
        "Moyuren Poppins",
        "400",
        "latin",
    ),
    FontSpec(
        "Poppins-SemiBold.ttf",
        "moyuren-poppins-semibold.woff2",
        "Moyuren Poppins",
        "600",
        "latin",
    ),
    FontSpec(
        "Poppins-Bold.ttf",
        "moyuren-poppins-bold.woff2",
        "Moyuren Poppins",
        "700",
        "latin",
    ),
    FontSpec(
        "Poppins-ExtraBold.ttf",
        "moyuren-poppins-extrabold.woff2",
        "Moyuren Poppins",
        "800",
        "latin",
    ),
    FontSpec(
        "Poppins-Black.ttf",
        "moyuren-poppins-black.woff2",
        "Moyuren Poppins",
        "900",
        "latin",
    ),
)

LICENSE_NAMES = (
    "OFL-JetBrainsMono.txt",
    "OFL-LXGWWenKai.txt",
    "OFL-NotoSansSC.txt",
    "OFL-Nunito.txt",
    "OFL-Poppins.txt",
    "OFL-ZCOOLKuaiLe.txt",
)

_CALENDAR_NAMES = """
立春 雨水 惊蛰 春分 清明 谷雨 立夏 小满 芒种 夏至 小暑 大暑
立秋 处暑 白露 秋分 寒露 霜降 立冬 小雪 大雪 冬至 小寒 大寒
元旦 情人节 妇女节 植树节 劳动节 青年节 儿童节 建党节 建军节
教师节 国庆节 平安夜 圣诞节 春节 元宵节 龙头节 上巳节 清明节
端午节 七夕节 中元节 中秋节 重阳节 寒衣节 下元节 腊八节 小年 除夕
"""


def _characters_in_ranges(ranges: Iterable[tuple[int, int]]) -> set[str]:
    """Return every character in inclusive Unicode code-point ranges."""
    return {chr(codepoint) for start, end in ranges for codepoint in range(start, end + 1)}


def gb2312_level_1_characters() -> set[str]:
    """Return the 3,755 GB2312 level-1 Han characters."""
    return _decode_gb2312_hanzi(range(0xB0, 0xD8))


def gb2312_hanzi_characters() -> set[str]:
    """Return all 6,763 level-1 and level-2 GB2312 Han characters."""
    return _decode_gb2312_hanzi(range(0xB0, 0xF8))


def _decode_gb2312_hanzi(lead_bytes: Iterable[int]) -> set[str]:
    """Decode the Han-character rows from the GB2312 code space."""
    characters: set[str] = set()
    for lead_byte in lead_bytes:
        for trail_byte in range(0xA1, 0xFF):
            try:
                character = bytes((lead_byte, trail_byte)).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if "\u4e00" <= character <= "\u9fff":
                characters.add(character)
    return characters


def extract_template_non_ascii_characters(template_dir: Path) -> set[str]:
    """Extract every non-ASCII character appearing in HTML templates."""
    characters: set[str] = set()
    for template_path in sorted(template_dir.glob("*.html")):
        characters.update(character for character in template_path.read_text(encoding="utf-8") if ord(character) > 127)
    return characters


def extract_python_string_characters(source_path: Path) -> set[str]:
    """Extract non-ASCII characters from Python string literals."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    characters: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            characters.update(character for character in node.value if ord(character) > 127)
    return characters


def base_characters() -> set[str]:
    """Return shared ASCII, punctuation, and fullwidth characters."""
    return _characters_in_ranges(
        (
            (0x20, 0x7E),
            (0xA0, 0xFF),
            (0x2000, 0x206F),
            (0x20A0, 0x20CF),
            (0x2100, 0x214F),
            (0x2190, 0x21FF),
            (0x2200, 0x22FF),
            (0x2460, 0x24FF),
            (0x25A0, 0x25FF),
            (0x2600, 0x26FF),
            (0x3000, 0x303F),
            (0xFF00, 0xFFEF),
        )
    )


def build_charsets(project_root: Path) -> dict[str, set[str]]:
    """Build the Latin and Chinese character sets used by each font face."""
    shared = base_characters()
    template_characters = extract_template_non_ascii_characters(project_root / "templates")
    calendar_characters = extract_python_string_characters(project_root / "app" / "services" / "calendar.py")
    dynamic_characters = {character for character in _CALENDAR_NAMES if not character.isspace()}
    dynamic_characters.update(calendar_characters)

    latin = shared | template_characters
    level_1_cjk = latin | gb2312_level_1_characters() | dynamic_characters
    full_cjk = latin | gb2312_hanzi_characters() | dynamic_characters
    return {
        "latin": latin,
        "level_1_cjk": level_1_cjk,
        "full_cjk": full_cjk,
    }


def _subset_font(source_path: Path, output_path: Path, characters: set[str]) -> None:
    """Subset one source font and save it as deterministic WOFF2."""
    options = subset.Options()
    options.flavor = "woff2"
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    options.layout_features = [
        feature
        for feature in options.layout_features
        if feature not in {"vert", "vrt2", "vhal", "vkrn", "vpal"}
    ]
    options.recalc_timestamp = False

    font = TTFont(source_path, recalcTimestamp=False)
    font_subsetter = subset.Subsetter(options=options)
    font_subsetter.populate(unicodes=sorted(ord(character) for character in characters))
    font_subsetter.subset(font)
    font.flavor = "woff2"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font.save(output_path, reorderTables=True)
    font.close()


def _render_css() -> str:
    """Render local @font-face declarations in a stable order."""
    blocks = []
    for spec in FONT_SPECS:
        blocks.append(
            "\n".join(
                (
                    "@font-face {",
                    f"  font-family: '{spec.family}';",
                    f"  src: url('./{spec.output_name}') format('woff2');",
                    "  font-style: normal;",
                    f"  font-weight: {spec.weight};",
                    "  font-display: swap;",
                    "}",
                )
            )
        )
    return "/* Generated by scripts/build_fonts.py. */\n\n" + "\n\n".join(blocks) + "\n"


def build_fonts(source_dir: Path, output_dir: Path, project_root: Path = PROJECT_ROOT) -> dict[str, set[str]]:
    """Build every local font face, stylesheet, and OFL license file."""
    missing_sources = [name for name in (*[spec.source_name for spec in FONT_SPECS], *LICENSE_NAMES) if not (source_dir / name).is_file()]
    if missing_sources:
        names = ", ".join(missing_sources)
        raise FileNotFoundError(f"Missing font source files: {names}")

    charsets = build_charsets(project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    for spec in FONT_SPECS:
        _subset_font(source_dir / spec.source_name, output_dir / spec.output_name, charsets[spec.charset])

    (output_dir / "fonts.css").write_text(_render_css(), encoding="utf-8", newline="\n")
    license_dir = output_dir / "licenses"
    license_dir.mkdir(parents=True, exist_ok=True)
    for license_name in LICENSE_NAMES:
        shutil.copyfile(source_dir / license_name, license_dir / license_name)
    return charsets


def _format_size(size_bytes: int) -> str:
    """Format a byte count as a compact binary size."""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MiB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KiB"
    return f"{size_bytes} B"


def _print_summary(output_dir: Path, charsets: dict[str, set[str]]) -> None:
    """Print generated character-set and file-size summaries."""
    print("Character sets:")
    for name in ("latin", "level_1_cjk", "full_cjk"):
        print(f"  {name}: {len(charsets[name]):,} code points")

    print("Generated files:")
    total_size = 0
    for path in sorted(file_path for file_path in output_dir.rglob("*") if file_path.is_file()):
        size = path.stat().st_size
        total_size += size
        print(f"  {path.relative_to(output_dir)}: {_format_size(size)}")
    print(f"  total: {_format_size(total_size)}")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True, help="Directory containing source TTF and OFL files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory (default: templates/fonts)")
    return parser.parse_args()


def main() -> None:
    """Run the local font bundle build."""
    args = parse_args()
    charsets = build_fonts(args.src.resolve(), args.out.resolve())
    _print_summary(args.out.resolve(), charsets)


if __name__ == "__main__":
    main()
