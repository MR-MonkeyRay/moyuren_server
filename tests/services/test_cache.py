"""app/services/cache.py 缓存清理服务测试。"""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.services.cache import CacheCleaner


def _daily_data(date_value: str, images: dict[str, str]) -> dict[str, object]:
    """构造缓存清理测试所需的日级数据字段。"""
    return {
        "date": date_value,
        "updated": "2026/02/10 07:22:32",
        "updated_at": 1,
        "images": images,
    }


def test_partial_family_deletion_counts_successful_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """同组文件删除失败时，保留成功计数并继续删除其余文件。"""
    cleaner = CacheCleaner(str(tmp_path), retain_days=30, logger=logging.getLogger("test"))
    base = cleaner.images_dir / "moyuren_20261029_060000.jpg"
    failed = base.with_suffix(".webp")
    resized = base.with_name(f"{base.stem}_w60.jpg")
    base.write_bytes(b"base")
    failed.write_bytes(b"failed")
    resized.write_bytes(b"resized")
    unlink = Path.unlink

    def fail_one(path: Path, missing_ok: bool = False) -> None:
        if path == failed:
            raise OSError("busy variant")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_one)
    assert cleaner._delete_image_family(base) == (2, len(b"base") + len(b"resized"))
    assert failed.exists()
    assert not base.exists()
    assert not resized.exists()
    assert "Failed to delete image family member" in caplog.text


class TestCacheCleaner:
    """CacheCleaner 类测试。"""

    def test_render_resource_cutoff_uses_business_midnight(self, tmp_path: Path) -> None:
        """验证资源保留边界为业务时区的零点。"""
        cleaner = CacheCleaner(str(tmp_path), retain_days=0, logger=logging.getLogger("test"))
        cutoff = datetime(2026, 2, 5, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        for name, fetched_at in [("expired", cutoff - 1), ("boundary", cutoff), ("recent", cutoff + 1)]:
            (cleaner.render_resources_dir / f"{name}.body").write_bytes(b"font")
            (cleaner.render_resources_dir / f"{name}.meta").write_text(json.dumps({"fetched_at": fetched_at}))

        with (
            patch("app.services.cache.today_business", return_value=date(2026, 2, 5)),
            patch("app.services.cache.get_business_timezone", return_value=ZoneInfo("Asia/Shanghai")),
        ):
            result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert not (cleaner.render_resources_dir / "expired.body").exists()
        assert (cleaner.render_resources_dir / "boundary.body").exists()
        assert (cleaner.render_resources_dir / "recent.body").exists()

    @pytest.fixture
    def logger(self) -> logging.Logger:
        """创建测试日志记录器。"""
        return logging.getLogger("test")

    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        """创建缓存目录结构。"""
        cache_dir = tmp_path / "cache"
        (cache_dir / "data").mkdir(parents=True)
        (cache_dir / "images").mkdir(parents=True)
        return cache_dir

    @pytest.fixture
    def cleaner(self, cache_dir: Path, logger: logging.Logger) -> CacheCleaner:
        """创建 CacheCleaner 实例。"""
        return CacheCleaner(cache_dir=str(cache_dir), retain_days=30, logger=logger)

    @pytest.fixture
    def mock_today(self):
        """mock today_business，使其返回固定日期。"""
        with patch("app.services.cache.today_business") as mock:
            mock.return_value = date(2026, 2, 10)
            yield mock

    def test_cleanup_no_files(self, cleaner: CacheCleaner, mock_today) -> None:
        """验证无文件时清理统计值为零。"""
        result = cleaner.cleanup()

        assert result["deleted_files"] == 0
        assert result["freed_bytes"] == 0
        assert result["oldest_kept"] == "2026-02-10"
        assert result["pruned_images"] == 0

    def test_cleanup_prunes_images_per_template_and_day(self, tmp_path: Path) -> None:
        """验证按模板和日期分别裁剪，仅保留最新图片。"""
        cleaner = CacheCleaner(
            str(tmp_path), 30, logging.getLogger("test"), max_images_per_template_per_day=3
        )
        for template, count in [("moyuren", 5), ("moyuren_cute", 4), ("moyuren-cute", 4)]:
            for hour in reversed(range(count)):
                image = cleaner.images_dir / f"{template}_20260210_{hour:02d}0000.jpg"
                image.write_bytes(b"image")
        other_day = cleaner.images_dir / "moyuren_20260209_000000.jpg"
        other_day.write_bytes(b"image")
        expired = cleaner.images_dir / "moyuren_20260110_000000.jpg"
        expired.write_bytes(b"expired")

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        assert {path.name for path in cleaner.images_dir.glob("*.jpg")} == {
            *(f"moyuren_20260210_{hour:02d}0000.jpg" for hour in range(2, 5)),
            *(f"moyuren_cute_20260210_{hour:02d}0000.jpg" for hour in range(1, 4)),
            *(f"moyuren-cute_20260210_{hour:02d}0000.jpg" for hour in range(1, 4)),
            other_day.name,
        }
        assert result["pruned_images"] == 4
        assert result["deleted_files"] == 5
        assert result["freed_bytes"] == 4 * len(b"image") + len(b"expired")
        assert result["oldest_kept"] == "2026-02-09"

    def test_cleanup_deletes_variants_with_pruned_base(self, tmp_path: Path) -> None:
        """裁剪原图时应删除其所有变体，并统计释放的字节数。"""
        cleaner = CacheCleaner(
            str(tmp_path), 30, logging.getLogger("test"), max_images_per_template_per_day=1
        )
        old_stem = "moyuren_20260210_010000"
        new_stem = "moyuren_20260210_020000"
        files = {
            f"{old_stem}.jpg": b"base",
            f"{old_stem}.webp": b"webp",
            f"{old_stem}_w320.jpg": b"small-jpeg",
            f"{old_stem}_w320.webp": b"small-webp",
            f"{new_stem}.jpg": b"new",
        }
        for filename, contents in files.items():
            (cleaner.images_dir / filename).write_bytes(contents)

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        assert {path.name for path in cleaner.images_dir.iterdir()} == {f"{new_stem}.jpg"}
        assert result["pruned_images"] == 1
        assert result["deleted_files"] == 4
        assert result["freed_bytes"] == sum(
            len(contents) for filename, contents in files.items() if filename.startswith(old_stem)
        )

    def test_cleanup_preserves_variants_of_referenced_base(self, tmp_path: Path) -> None:
        """数据文件引用原图时，应保护其所有现存变体。"""
        cleaner = CacheCleaner(
            str(tmp_path), 30, logging.getLogger("test"), max_images_per_template_per_day=1
        )
        referenced_stem = "moyuren_20260210_010000"
        newer_stem = "moyuren_20260210_020000"
        referenced_files = {
            f"{referenced_stem}.jpg",
            f"{referenced_stem}.webp",
            f"{referenced_stem}_w320.jpg",
            f"{referenced_stem}_w320.webp",
        }
        for filename in referenced_files | {f"{newer_stem}.jpg", f"{newer_stem}.webp"}:
            (cleaner.images_dir / filename).write_bytes(b"image")
        data_path = cleaner.data_dir / "2026-02-10.json"
        data_path.write_text(
            json.dumps(_daily_data("2026-02-10", {"moyuren": f"{referenced_stem}.jpg"}))
        )

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        assert {path.name for path in cleaner.images_dir.iterdir()} == referenced_files
        assert result["pruned_images"] == 1
        assert result["deleted_files"] == 2

    def test_cleanup_removes_orphan_variants(self, tmp_path: Path) -> None:
        """缺少原始 JPEG 的变体应作为孤立文件删除。"""
        cleaner = CacheCleaner(str(tmp_path), 30, logging.getLogger("test"))
        orphan_files = {
            "moyuren_20260210_010000.webp": b"full",
            "moyuren_20260210_010000_w320.jpg": b"jpeg",
            "moyuren_20260210_010000_w320.webp": b"webp",
        }
        for filename, contents in orphan_files.items():
            (cleaner.images_dir / filename).write_bytes(contents)

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        assert not list(cleaner.images_dir.iterdir())
        assert result["deleted_files"] == 3
        assert result["freed_bytes"] == sum(map(len, orphan_files.values()))

    def test_cleanup_preserves_variants_when_referenced_base_is_missing(
        self, tmp_path: Path
    ) -> None:
        """即使原图缺失、等待自动恢复，引用仍应保护整组图片。"""
        cleaner = CacheCleaner(str(tmp_path), 30, logging.getLogger("test"))
        stem = "moyuren_20260210_010000"
        variant = cleaner.images_dir / f"{stem}.webp"
        variant.write_bytes(b"webp")
        data_path = cleaner.data_dir / "2026-02-10.json"
        data_path.write_text(
            json.dumps(_daily_data("2026-02-10", {"moyuren": f"{stem}.jpg"}))
        )

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        assert variant.exists()
        assert result["deleted_files"] == 0

    def test_cleanup_cap_counts_only_base_images(self, tmp_path: Path) -> None:
        """多个变体不应占用每日原图保留名额。"""
        cleaner = CacheCleaner(
            str(tmp_path), 30, logging.getLogger("test"), max_images_per_template_per_day=2
        )
        for hour in range(3):
            stem = f"moyuren_20260210_{hour:02d}0000"
            for suffix in (".jpg", ".webp", "_w320.jpg", "_w320.webp"):
                (cleaner.images_dir / f"{stem}{suffix}").write_bytes(b"image")

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        assert {path.name for path in cleaner.images_dir.iterdir()} == {
            f"moyuren_20260210_{hour:02d}0000{suffix}"
            for hour in (1, 2)
            for suffix in (".jpg", ".webp", "_w320.jpg", "_w320.webp")
        }
        assert result["pruned_images"] == 1
        assert result["deleted_files"] == 4

    @pytest.mark.parametrize("reference_count", [1, 4])
    def test_cleanup_preserves_references_from_any_data_file(
        self, tmp_path: Path, reference_count: int
    ) -> None:
        """验证所有被引用图片均计入上限，即使引用来自不同日期的数据。"""
        cleaner = CacheCleaner(str(tmp_path), 30, logging.getLogger("test"))
        filenames = [f"moyuren_20260210_{hour:02d}0000.jpg" for hour in range(5)]
        for filename in filenames:
            (cleaner.images_dir / filename).write_bytes(b"image")
        for index, filename in enumerate(filenames[:reference_count]):
            data_path = cleaner.data_dir / f"2026-02-{10 - index:02d}.json"
            data_path.write_text(
                json.dumps(_daily_data(data_path.stem, {"moyuren": filename}))
            )

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        expected = set(filenames[:reference_count])
        if reference_count < 3:
            expected.update(filenames[-(3 - reference_count):])
        assert {path.name for path in cleaner.images_dir.glob("*.jpg")} == expected
        assert result["pruned_images"] == 5 - len(expected)
        assert result["deleted_files"] == result["pruned_images"]

    @pytest.mark.parametrize("data_filename", ["2025-12-01.json", "backup.json"])
    def test_cleanup_preserves_references_from_other_data_files(
        self, tmp_path: Path, data_filename: str
    ) -> None:
        """验证过期及非日级数据文件也能保护其引用的图片。"""
        cleaner = CacheCleaner(
            str(tmp_path), 30, logging.getLogger("test"), max_images_per_template_per_day=1
        )
        referenced = cleaner.images_dir / "moyuren_20260210_000000.jpg"
        newer = cleaner.images_dir / "moyuren_20260210_010000.jpg"
        referenced.write_bytes(b"referenced")
        newer.write_bytes(b"newer")
        data_file = cleaner.data_dir / data_filename
        data_file.write_text(
            json.dumps(_daily_data("2025-12-01", {"moyuren": referenced.name}))
        )
        data_size = data_file.stat().st_size

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        expired_data = data_filename == "2025-12-01.json"
        assert referenced.exists()
        assert not newer.exists()
        assert result["pruned_images"] == 1
        assert result["deleted_files"] == 1 + int(expired_data)
        assert result["freed_bytes"] == len(b"newer") + (data_size if expired_data else 0)

    @pytest.mark.parametrize(
        "contents", ["{invalid", "[]", '{"images": []}', '{"images": {"moyuren": null}}', "\xff"]
    )
    def test_cleanup_skips_pruning_for_invalid_data(
        self, tmp_path: Path, contents: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """验证无效数据会保护当天图片，同时不影响其他日期的裁剪。"""
        cleaner = CacheCleaner(str(tmp_path), 30, logging.getLogger("test"))
        for day in [9, 10]:
            for hour in range(5):
                image = cleaner.images_dir / f"moyuren_202602{day:02d}_{hour:02d}0000.jpg"
                image.write_bytes(b"image")
        (cleaner.data_dir / "2026-02-10.json").write_bytes(contents.encode("latin-1"))

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        assert len(list(cleaner.images_dir.glob("moyuren_20260210_*.jpg"))) == 5
        assert len(list(cleaner.images_dir.glob("moyuren_20260209_*.jpg"))) == 3
        assert result["pruned_images"] == 2
        assert "Skipping image pruning for 2026-02-10" in caplog.text

    def test_cleanup_skips_pruning_for_unreadable_data(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """验证无法读取日级数据时不裁剪对应日期的图片。"""
        cleaner = CacheCleaner(str(tmp_path), 30, logging.getLogger("test"))
        (cleaner.data_dir / "2026-02-10.json").write_text("{}")
        for hour in range(5):
            (cleaner.images_dir / f"moyuren_20260210_{hour:02d}0000.jpg").write_bytes(b"image")

        with (
            patch("app.services.cache.today_business", return_value=date(2026, 2, 10)),
            patch.object(Path, "read_text", side_effect=OSError("unreadable")),
        ):
            result = cleaner.cleanup()

        assert len(list(cleaner.images_dir.glob("*.jpg"))) == 5
        assert result["pruned_images"] == 0
        assert result["deleted_files"] == 0
        assert "Skipping image pruning for 2026-02-10" in caplog.text

    def test_cleanup_skips_pruning_for_partially_invalid_mapping(self, tmp_path: Path) -> None:
        """验证映射格式有误时，保守地禁用对应日期的图片裁剪。"""
        cleaner = CacheCleaner(str(tmp_path), 30, logging.getLogger("test"))
        filenames = [f"moyuren_20260210_{hour:02d}0000.jpg" for hour in range(5)]
        for filename in filenames:
            (cleaner.images_dir / filename).write_bytes(b"image")
        (cleaner.data_dir / "2026-02-10.json").write_text(
            json.dumps(
                {
                    **_daily_data("2026-02-10", {"moyuren": filenames[0]}),
                    "images": {"moyuren": filenames[0], "invalid": None},
                }
            )
        )

        with patch("app.services.cache.today_business", return_value=date(2026, 2, 10)):
            result = cleaner.cleanup()

        assert {path.name for path in cleaner.images_dir.glob("*.jpg")} == set(filenames)
        assert result["pruned_images"] == 0

    def test_cleanup_expired_data_files(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """验证清理会删除过期数据文件。"""
        # 创建过期数据文件（超过 30 天）
        expired_date = date(2026, 1, 1)  # 40 天前
        expired_file = cache_dir / "data" / f"{expired_date.isoformat()}.json"
        expired_file.write_text('{"test": "data"}')

        # 创建近期数据文件（30 天内）
        recent_date = date(2026, 2, 1)  # 9 天前
        recent_file = cache_dir / "data" / f"{recent_date.isoformat()}.json"
        recent_file.write_text('{"test": "data"}')

        result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert result["freed_bytes"] > 0
        assert not expired_file.exists()
        assert recent_file.exists()

    def test_cleanup_expired_image_files(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """验证清理会删除过期图片文件。"""
        # 创建过期图片文件（超过 30 天）
        expired_file = cache_dir / "images" / "moyuren_20260101_060000.jpg"
        expired_file.write_bytes(b"fake image data")

        # 创建近期图片文件（30 天内）
        recent_file = cache_dir / "images" / "moyuren_20260201_060000.jpg"
        recent_file.write_bytes(b"fake image data")

        result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert result["freed_bytes"] > 0
        assert not expired_file.exists()
        assert recent_file.exists()

    def test_cleanup_keeps_recent_files(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """验证清理会保留当天的文件。"""
        # 创建当天的数据文件
        today_data = cache_dir / "data" / "2026-02-10.json"
        today_data.write_text('{"test": "data"}')

        # 创建当天的图片文件
        today_image = cache_dir / "images" / "moyuren_20260210_072232.jpg"
        today_image.write_bytes(b"fake image data")

        result = cleaner.cleanup()

        assert result["deleted_files"] == 0
        assert result["freed_bytes"] == 0
        assert today_data.exists()
        assert today_image.exists()

    def test_cleanup_returns_correct_stats(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """验证清理返回的统计值正确。"""
        # 创建多个大小已知的过期文件
        expired_data1 = cache_dir / "data" / "2025-12-01.json"
        expired_data1.write_text('{"test": "data1"}')  # 约 17 字节
        size1 = expired_data1.stat().st_size

        expired_data2 = cache_dir / "data" / "2025-12-15.json"
        expired_data2.write_text('{"test": "data2"}')  # 约 17 字节
        size2 = expired_data2.stat().st_size

        expired_image = cache_dir / "images" / "moyuren_20251201_060000.jpg"
        expired_image.write_bytes(b"x" * 1000)  # 1000 字节
        size3 = expired_image.stat().st_size

        # 创建近期文件，用于验证 oldest_kept
        recent_file = cache_dir / "data" / "2026-02-01.json"
        recent_file.write_text('{"test": "recent"}')

        result = cleaner.cleanup()

        assert result["deleted_files"] == 3
        assert result["freed_bytes"] == size1 + size2 + size3
        assert result["oldest_kept"] == "2026-02-01"

    def test_cleanup_skips_invalid_filenames(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """验证清理会跳过文件名格式无效的文件。"""
        # 创建文件名无效的文件
        invalid_data = cache_dir / "data" / "invalid.json"
        invalid_data.write_text('{"test": "data"}')

        invalid_image1 = cache_dir / "images" / "invalid.jpg"
        invalid_image1.write_bytes(b"fake image")

        invalid_image2 = cache_dir / "images" / "moyuren_invalid.jpg"
        invalid_image2.write_bytes(b"fake image")

        result = cleaner.cleanup()

        # 应跳过所有文件名无效的文件
        assert result["deleted_files"] == 0
        assert invalid_data.exists()
        assert invalid_image1.exists()
        assert invalid_image2.exists()

    def test_cleanup_mixed_expired_and_recent(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """验证过期文件与近期文件混合时的清理行为。"""
        # 过期文件
        (cache_dir / "data" / "2025-11-01.json").write_text('{"old": 1}')
        (cache_dir / "data" / "2025-12-01.json").write_text('{"old": 2}')
        (cache_dir / "images" / "moyuren_20251101_060000.jpg").write_bytes(b"old1")
        (cache_dir / "images" / "moyuren_20251201_060000.jpg").write_bytes(b"old2")

        # 近期文件
        (cache_dir / "data" / "2026-02-01.json").write_text('{"new": 1}')
        (cache_dir / "data" / "2026-02-09.json").write_text('{"new": 2}')
        (cache_dir / "images" / "moyuren_20260201_060000.jpg").write_bytes(b"new1")
        (cache_dir / "images" / "moyuren_20260209_060000.jpg").write_bytes(b"new2")

        result = cleaner.cleanup()

        assert result["deleted_files"] == 4
        assert result["freed_bytes"] > 0
        assert result["oldest_kept"] == "2026-02-01"

        # 验证近期文件仍存在
        assert (cache_dir / "data" / "2026-02-01.json").exists()
        assert (cache_dir / "data" / "2026-02-09.json").exists()
        assert (cache_dir / "images" / "moyuren_20260201_060000.jpg").exists()
        assert (cache_dir / "images" / "moyuren_20260209_060000.jpg").exists()

        # 验证过期文件已删除
        assert not (cache_dir / "data" / "2025-11-01.json").exists()
        assert not (cache_dir / "data" / "2025-12-01.json").exists()
        assert not (cache_dir / "images" / "moyuren_20251101_060000.jpg").exists()
        assert not (cache_dir / "images" / "moyuren_20251201_060000.jpg").exists()

    def test_cleanup_with_different_retain_days(
        self, cache_dir: Path, logger: logging.Logger, mock_today
    ) -> None:
        """验证不同 retain_days 值下的清理行为。"""
        # 创建保留期为 7 天的清理器
        cleaner = CacheCleaner(cache_dir=str(cache_dir), retain_days=7, logger=logger)

        # 创建 10 天前的文件（应删除）
        old_file = cache_dir / "data" / "2026-01-31.json"
        old_file.write_text('{"test": "old"}')

        # 创建 5 天前的文件（应保留）
        recent_file = cache_dir / "data" / "2026-02-05.json"
        recent_file.write_text('{"test": "recent"}')

        result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_cleanup_handles_multiple_templates(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """验证清理能处理不同模板的图片。"""
        # 创建不同模板的过期图片
        (cache_dir / "images" / "moyuren_20251201_060000.jpg").write_bytes(b"old1")
        (cache_dir / "images" / "custom_20251201_060000.jpg").write_bytes(b"old2")
        (cache_dir / "images" / "another_template_20251201_060000.jpg").write_bytes(b"old3")

        # 创建近期图片
        (cache_dir / "images" / "moyuren_20260201_060000.jpg").write_bytes(b"new1")
        (cache_dir / "images" / "custom_20260201_060000.jpg").write_bytes(b"new2")

        result = cleaner.cleanup()

        assert result["deleted_files"] == 3
        assert (cache_dir / "images" / "moyuren_20260201_060000.jpg").exists()
        assert (cache_dir / "images" / "custom_20260201_060000.jpg").exists()

    def test_cleanup_boundary_date(
        self, cleaner: CacheCleaner, cache_dir: Path, mock_today
    ) -> None:
        """验证恰好处于保留边界（30 天前）时的清理行为。"""
        # 恰好 30 天前的文件（2026-01-11）应保留
        boundary_file = cache_dir / "data" / "2026-01-11.json"
        boundary_file.write_text('{"test": "boundary"}')

        # 31 天前的文件（2026-01-10）应删除
        expired_file = cache_dir / "data" / "2026-01-10.json"
        expired_file.write_text('{"test": "expired"}')

        result = cleaner.cleanup()

        assert result["deleted_files"] == 1
        assert boundary_file.exists()
        assert not expired_file.exists()

    def test_cleanup_expired_render_resources(
        self, cache_dir: Path, logger: logging.Logger, mock_today
    ) -> None:
        """验证清理会删除成对的过期渲染资源缓存。"""
        cleaner = CacheCleaner(cache_dir=str(cache_dir), retain_days=30, logger=logger)
        rr_dir = cache_dir / "render_resources"

        # 创建过期资源文件对（fetched_at = 2025-12-01，明显早于保留边界）
        expired_ts = datetime(2025, 12, 1, tzinfo=timezone.utc).timestamp()
        (rr_dir / "expired_key.body").write_bytes(b"expired-font-data")
        (rr_dir / "expired_key.meta").write_text(
            json.dumps({"url": "https://fonts.googleapis.cn/test", "content_type": "font/woff2", "fetched_at": expired_ts, "stored_at": "2025-12-01T00:00:00Z", "size": 18}),
            encoding="utf-8",
        )

        # 创建近期资源文件对（fetched_at = 2026-02-09，明显晚于保留边界）
        recent_ts = datetime(2026, 2, 9, tzinfo=timezone.utc).timestamp()
        (rr_dir / "recent_key.body").write_bytes(b"recent-font-data")
        (rr_dir / "recent_key.meta").write_text(
            json.dumps({"url": "https://fonts.googleapis.cn/test2", "content_type": "font/woff2", "fetched_at": recent_ts, "stored_at": "2026-02-09T00:00:00Z", "size": 18}),
            encoding="utf-8",
        )

        result = cleaner.cleanup()

        assert not (rr_dir / "expired_key.body").exists()
        assert not (rr_dir / "expired_key.meta").exists()
        assert (rr_dir / "recent_key.body").exists()
        assert (rr_dir / "recent_key.meta").exists()
        assert result["deleted_files"] >= 1

    def test_cleanup_orphan_render_resource_body(
        self, cache_dir: Path, logger: logging.Logger, mock_today
    ) -> None:
        """验证清理会删除没有匹配 meta 的孤立 body 文件。"""
        cleaner = CacheCleaner(cache_dir=str(cache_dir), retain_days=30, logger=logger)
        rr_dir = cache_dir / "render_resources"

        # 创建孤立的 body 文件（无 meta）
        (rr_dir / "orphan_key.body").write_bytes(b"orphan-data")

        result = cleaner.cleanup()

        assert not (rr_dir / "orphan_key.body").exists()
        assert result["deleted_files"] >= 1

    def test_cleanup_corrupted_render_resource_meta(
        self, cache_dir: Path, logger: logging.Logger, mock_today
    ) -> None:
        """验证清理会删除损坏的 meta 文件及其配对的 body 文件。"""
        cleaner = CacheCleaner(cache_dir=str(cache_dir), retain_days=30, logger=logger)
        rr_dir = cache_dir / "render_resources"

        # 创建损坏的 meta（无效 JSON）及配对的 body 文件
        (rr_dir / "corrupt_key.body").write_bytes(b"corrupt-data")
        (rr_dir / "corrupt_key.meta").write_text("not valid json\n", encoding="utf-8")

        result = cleaner.cleanup()

        assert not (rr_dir / "corrupt_key.body").exists()
        assert not (rr_dir / "corrupt_key.meta").exists()
        assert result["deleted_files"] >= 1

    def test_cleanup_unreadable_render_resource_meta_stat_failure(
        self, cache_dir: Path, logger: logging.Logger, mock_today
    ) -> None:
        """验证清理无效元数据时能容忍 stat 失败。"""
        cleaner = CacheCleaner(cache_dir=str(cache_dir), retain_days=30, logger=logger)
        rr_dir = cache_dir / "render_resources"
        meta_path = rr_dir / "race_key.meta"
        body_path = rr_dir / "race_key.body"
        meta_path.write_text("not valid json\n", encoding="utf-8")
        body_path.write_bytes(b"body")

        original_stat = Path.stat

        def fail_meta_stat(path: Path, *args, **kwargs):
            if path == meta_path:
                raise OSError("removed concurrently")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", fail_meta_stat):
            result = cleaner.cleanup()

        assert result["deleted_files"] >= 1
        assert result["freed_bytes"] == len(b"body")
        assert not meta_path.exists()
        assert not body_path.exists()

    def test_cleanup_stale_temp_files(
        self, cache_dir: Path, logger: logging.Logger, mock_today
    ) -> None:
        """验证清理会删除写入中断遗留的 .tmp 文件。"""
        cleaner = CacheCleaner(cache_dir=str(cache_dir), retain_days=30, logger=logger)
        rr_dir = cache_dir / "render_resources"

        # 创建遗留临时文件
        (rr_dir / "abc123.body.tmp").write_bytes(b"partial-data")
        (rr_dir / "abc123.meta.tmp").write_text("partial-meta", encoding="utf-8")

        result = cleaner.cleanup()

        assert not (rr_dir / "abc123.body.tmp").exists()
        assert not (rr_dir / "abc123.meta.tmp").exists()
        assert result["deleted_files"] >= 2
