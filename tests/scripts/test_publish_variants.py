"""静态图片变体发布辅助函数测试。"""

from pathlib import Path

from PIL import Image

from app.models.schemas import DailyDataFile
from scripts.publish_static import (
    _existing_variant_files,
    _latest_manifest,
    _publish_image_family,
    _published_variant_metadata,
    _published_variant_name,
)


def test_published_variant_name_maps_cache_family() -> None:
    """缓存中的变体名应映射到按日期与 latest 两组产物。"""
    stem = "moyuren_20261029_072232"

    assert _published_variant_name(stem, f"{stem}.jpg", "moyuren") == "moyuren.jpg"
    assert _published_variant_name(stem, f"{stem}.webp", "moyuren") == "moyuren.webp"
    assert (
        _published_variant_name(stem, f"{stem}_w640.jpg", "moyuren")
        == "moyuren_w640.jpg"
    )
    assert (
        _published_variant_name(stem, f"{stem}_w640.webp", "latest")
        == "latest_w640.webp"
    )


def test_publish_discovers_lazy_unrecorded_variants(tmp_path: Path) -> None:
    """发布应包含每个有效的现存同级文件，与配置无关。"""
    source = tmp_path / "moyuren_20261029_072232.jpg"
    Image.new("RGB", (800, 400), "white").save(source, "JPEG")
    Image.new("RGB", (800, 400), "white").save(source.with_suffix(".webp"), "WEBP")
    Image.new("RGB", (333, 167), "white").save(
        source.with_name(f"{source.stem}_w333.webp"), "WEBP"
    )
    source.with_name(f"{source.stem}_wbad.webp").write_bytes(b"ignored")
    source.with_name(f"{source.stem}_w0.webp").write_bytes(b"ignored")

    assert [path.name for path in _existing_variant_files(source)] == [
        source.name,
        f"{source.stem}.webp",
        f"{source.stem}_w333.webp",
    ]
    assert _published_variant_metadata(source) == [
        {"filename": "moyuren.jpg", "format": "jpeg", "width": 800},
        {"filename": "moyuren.webp", "format": "webp", "width": 800},
        {"filename": "moyuren_w333.webp", "format": "webp", "width": 333},
    ]


def test_latest_manifest_points_to_root_artifacts() -> None:
    """根目录 JSON 清单应使用与复制文件相同的 latest 前缀。"""
    dated = DailyDataFile.model_validate(
        {
            "date": "2026-10-29",
            "updated": "2026/10/29 07:22:32",
            "updated_at": 1,
            "images": {"moyuren": "moyuren.jpg"},
            "image_variants": {
                "moyuren": [
                    {"filename": "moyuren.jpg", "format": "jpeg", "width": 800},
                    {"filename": "moyuren.webp", "format": "webp", "width": 800},
                    {"filename": "moyuren_w333.webp", "format": "webp", "width": 333},
                ]
            },
        }
    )

    latest = _latest_manifest(dated)

    assert latest.images == {"moyuren": "latest.jpg"}
    assert latest.image_variants is not None
    assert [variant.filename for variant in latest.image_variants["moyuren"]] == [
        "latest.jpg",
        "latest.webp",
        "latest_w333.webp",
    ]
    assert dated.images == {"moyuren": "moyuren.jpg"}
    assert set(latest.model_dump(exclude_unset=True)) == set(dated.model_dump(exclude_unset=True))


def test_publish_image_family_copies_matching_manifest_names(tmp_path: Path) -> None:
    """按日期与根目录两组产物都应包含每个发现的变体。"""
    source = tmp_path / "cache" / "moyuren_20261029_072232.jpg"
    source.parent.mkdir()
    Image.new("RGB", (800, 400), "white").save(source, "JPEG")
    Image.new("RGB", (800, 400), "white").save(source.with_suffix(".webp"), "WEBP")
    Image.new("RGB", (333, 167), "white").save(
        source.with_name(f"{source.stem}_w333.webp"), "WEBP"
    )
    date_dir = tmp_path / "out" / "2026-10-29"
    output_dir = tmp_path / "out"

    copied = _publish_image_family(source, date_dir, output_dir)

    assert copied == 6
    assert {path.name for path in date_dir.iterdir()} == {
        "moyuren.jpg",
        "moyuren.webp",
        "moyuren_w333.webp",
    }
    assert {path.name for path in output_dir.iterdir() if path.is_file()} == {
        "latest.jpg",
        "latest.webp",
        "latest_w333.webp",
    }
