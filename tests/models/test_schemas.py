"""app/models/schemas.py 中 Pydantic 数据模型的测试。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.errors import ErrorCode, StorageError
from app.models.schemas import (
    DAILY_DATA_SCHEMA_VERSION,
    DailyDataFile,
    DateInfoSchema,
    FunContentSchema,
    GuideSchema,
    MoyurenImageResponse,
    NewsMetaSchema,
    SolarTermSchema,
    WeekendSchema,
)


class TestDailyDataFile:
    """带版本号的日级缓存格式测试。"""

    @pytest.fixture
    def payload(self) -> dict[str, object]:
        """返回最简历史日级缓存数据。"""
        return {
            "date": "2026-02-04", "updated": "2026/02/04 10:00:00",
            "updated_at": 1738634400000, "images": {"moyuren": "moyuren.jpg"},
        }

    def test_round_trip(self, tmp_path: Path, payload: dict[str, object]) -> None:
        """保留可选字段、空值及嵌套和顶层的未知键。"""
        payload.update({
            "future": {"enabled": True},
            "fun_content": {"title": "新标题", "text": "中文", "type": "dad_joke", "extra": 7},
            "date_info": {"future": "value"}, "daily_english": None,
            "stock_indices": {"items": [{"price": 123, "extra": "kept"}]},
        })
        model = DailyDataFile.model_validate(payload)
        target = tmp_path / "data" / "2026-02-04.json"
        model.save_atomic(target)
        loaded = DailyDataFile.load(target)
        assert json.loads(loaded.dump_json()) == {**payload, "schema_version": DAILY_DATA_SCHEMA_VERSION}
        assert list(target.parent.iterdir()) == [target]

    def test_old_file_without_version(self, tmp_path: Path, payload: dict[str, object]) -> None:
        """加载旧数据，不补入缺失的可选字段。"""
        payload["fun_content"] = {"title": "一言", "content": "legacy"}
        target = tmp_path / "2026-02-04.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        model = DailyDataFile.load(target)
        assert model.schema_version == 1
        assert json.loads(model.dump_json()) == {**payload, "schema_version": 1}
        assert model.image_variants is None

    def test_variant_round_trip(self, tmp_path: Path, payload: dict[str, object]) -> None:
        """持久化有类型的图片变体，保留原始基础图片映射。"""
        payload["image_variants"] = {"moyuren": [
            {"filename": "moyuren.webp", "format": "webp", "width": 120},
            {"filename": "moyuren_w60.jpg", "format": "jpeg", "width": 60},
        ]}
        path = tmp_path / "2026-02-04.json"
        DailyDataFile.model_validate(payload).save_atomic(path)
        loaded = DailyDataFile.load(path)
        assert loaded.image_variants is not None
        assert loaded.image_variants["moyuren"][0].width == 120
        assert json.loads(loaded.dump_json()) == {**payload, "schema_version": 1}

    @pytest.mark.parametrize("filename", ["", " ", "../bad.webp", "bad/file.webp", "bad\\file.webp", "a..webp", "\x00.webp"])
    def test_invalid_variant_filename(self, payload: dict[str, object], filename: str) -> None:
        """变体沿用基础映射的文件名校验规则。"""
        payload["image_variants"] = {"moyuren": [
            {"filename": filename, "format": "webp", "width": 60},
        ]}
        with pytest.raises(ValidationError):
            DailyDataFile.model_validate(payload)

    def test_legacy_optional_shapes(self, payload: dict[str, object]) -> None:
        """接受历史可选字段格式，避免整个文件加载失败。"""
        payload.update({"weekend": False, "stock_indices": [], "fun_content": "legacy"})
        model = DailyDataFile.model_validate(payload)
        assert model.weekend is False
        assert model.stock_indices == []

    @pytest.mark.parametrize("value", ["2026-02-30", "20260204", "2026-2-4", "2026-02-04T00:00:00", "bad"])
    def test_invalid_date(self, payload: dict[str, object], value: str) -> None:
        """拒绝无效或格式不规范的日历日期。"""
        payload["date"] = value
        with pytest.raises(ValidationError):
            DailyDataFile.model_validate(payload)

    @pytest.mark.parametrize("value", [-1, True, False, 1.5, "123", None])
    def test_invalid_timestamp(self, payload: dict[str, object], value: object) -> None:
        """拒绝负时间戳、布尔值和非整数值。"""
        payload["updated_at"] = value
        with pytest.raises(ValidationError):
            DailyDataFile.model_validate(payload)

    @pytest.mark.parametrize("filename", ["", " ", "../outside.jpg", "foo/bar.jpg", "foo\\bar.jpg", "a..jpg", "\x00.jpg", None])
    def test_invalid_image(self, payload: dict[str, object], filename: object) -> None:
        """拒绝各映射项中的空文件名和路径穿越。"""
        payload["images"] = {"safe": "safe.jpg", "invalid": filename}
        with pytest.raises(ValidationError):
            DailyDataFile.model_validate(payload)

    @pytest.mark.parametrize("content", ["{", "[]", '{"date":"invalid"}', "\xff"])
    def test_load_wraps_invalid_data(self, tmp_path: Path, content: str) -> None:
        """按存储错误约定报告格式错误或无效的数据。"""
        target = tmp_path / "invalid.json"
        target.write_bytes(content.encode("latin-1"))
        with pytest.raises(StorageError) as error:
            DailyDataFile.load(target)
        assert error.value.code == ErrorCode.STORAGE_READ_FAILED
        assert error.value.__cause__ is not None

    def test_load_missing_file(self, tmp_path: Path) -> None:
        """统一封装文件读取失败。"""
        with pytest.raises(StorageError) as error:
            DailyDataFile.load(tmp_path / "missing.json")
        assert error.value.code == ErrorCode.STORAGE_READ_FAILED

    def test_failed_save_preserves_original(self, tmp_path: Path, payload: dict[str, object]) -> None:
        """替换失败时清理临时文件并保留原内容。"""
        target = tmp_path / "2026-02-04.json"
        target.write_text("old contents", encoding="utf-8")
        with patch("app.models.schemas.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(StorageError) as error:
                DailyDataFile.model_validate(payload).save_atomic(target)
        assert error.value.code == ErrorCode.STORAGE_WRITE_FAILED
        assert target.read_text(encoding="utf-8") == "old contents"
        assert list(tmp_path.iterdir()) == [target]


class TestMoyurenImageResponse:
    """MoyurenImageResponse 模型测试。"""

    def test_valid_moyuren_image_response(self) -> None:
        """测试创建有效的 MoyurenImageResponse。"""
        response = MoyurenImageResponse(
            date="2026-02-04",
            updated="2026/02/04 10:00:00",
            updated_at=1738634400000,
            image="https://example.com/image.jpg",
        )

        assert response.date == "2026-02-04"
        assert response.updated == "2026/02/04 10:00:00"
        assert response.updated_at == 1738634400000
        assert response.image == "https://example.com/image.jpg"


class TestFunContentSchema:
    """FunContentSchema 模型测试。"""

    def test_valid_fun_content(self) -> None:
        """测试创建有效的 FunContentSchema。"""
        content = FunContentSchema(
            type="joke", title="🤣 冷笑话", text="为什么程序员总是分不清万圣节和圣诞节？因为 Oct 31 = Dec 25"
        )

        assert content.type == "joke"
        assert content.title == "🤣 冷笑话"
        assert "程序员" in content.text


class TestDateInfoSchema:
    """DateInfoSchema 模型测试。"""

    def test_valid_date_info(self) -> None:
        """测试创建有效的 DateInfoSchema。"""
        date_info = DateInfoSchema(
            year_month="2026.02",
            day="4",
            week_cn="星期三",
            week_en="Wed",
            lunar_year="乙巳年",
            lunar_date="正月初七",
            zodiac="蛇",
            constellation="水瓶座",
            moon_phase="上弦月",
            is_holiday=False,
        )

        assert date_info.year_month == "2026.02"
        assert date_info.week_cn == "星期三"
        assert date_info.zodiac == "蛇"
        assert date_info.is_holiday is False

    def test_date_info_with_optional_fields(self) -> None:
        """测试 DateInfoSchema 的可选字段。"""
        date_info = DateInfoSchema(
            year_month="2026.02",
            day="4",
            week_cn="星期三",
            week_en="Wed",
            lunar_year="乙巳年",
            lunar_date="正月初七",
            zodiac="蛇",
            constellation="水瓶座",
            moon_phase="上弦月",
            festival_solar="立春",
            festival_lunar=None,
            legal_holiday=None,
            is_holiday=False,
        )

        assert date_info.festival_solar == "立春"
        assert date_info.festival_lunar is None


class TestWeekendSchema:
    """WeekendSchema 模型测试。"""

    def test_valid_weekend_schema(self) -> None:
        """测试创建有效的 WeekendSchema。"""
        weekend = WeekendSchema(days_left=2, is_weekend=False)

        assert weekend.days_left == 2
        assert weekend.is_weekend is False

    def test_weekend_on_saturday(self) -> None:
        """测试周末时的 WeekendSchema。"""
        weekend = WeekendSchema(days_left=0, is_weekend=True)

        assert weekend.days_left == 0
        assert weekend.is_weekend is True


class TestSolarTermSchema:
    """SolarTermSchema 模型测试。"""

    def test_valid_solar_term(self) -> None:
        """测试创建有效的 SolarTermSchema。"""
        solar_term = SolarTermSchema(
            name="立春", name_en="Beginning of Spring", days_left=0, date="2026-02-04", is_today=True
        )

        assert solar_term.name == "立春"
        assert solar_term.name_en == "Beginning of Spring"
        assert solar_term.is_today is True

    def test_solar_term_not_today(self) -> None:
        """测试节气不在当天的 SolarTermSchema。"""
        solar_term = SolarTermSchema(name="雨水", name_en="Rain Water", days_left=15, date="2026-02-19", is_today=False)

        assert solar_term.days_left == 15
        assert solar_term.is_today is False


class TestGuideSchema:
    """GuideSchema 模型测试。"""

    def test_valid_guide(self) -> None:
        """测试创建有效的 GuideSchema。"""
        guide = GuideSchema(yi=["摸鱼", "喝茶", "休息"], ji=["加班", "开会", "焦虑"])

        assert len(guide.yi) == 3
        assert len(guide.ji) == 3
        assert "摸鱼" in guide.yi
        assert "加班" in guide.ji

    def test_guide_with_empty_lists(self) -> None:
        """测试列表为空时的 GuideSchema。"""
        guide = GuideSchema(yi=[], ji=[])

        assert guide.yi == []
        assert guide.ji == []


class TestNewsMetaSchema:
    """NewsMetaSchema 模型测试。"""

    def test_valid_news_meta(self) -> None:
        """测试创建有效的 NewsMetaSchema。"""
        meta = NewsMetaSchema(date="2026年2月4日", updated="2026-02-04T06:00:00+08:00", updated_at=1738620000000)

        assert meta.date == "2026年2月4日"
        assert meta.updated == "2026-02-04T06:00:00+08:00"

    def test_news_meta_all_optional(self) -> None:
        """测试 NewsMetaSchema 的所有可选字段均未传入。"""
        meta = NewsMetaSchema()

        assert meta.date is None
        assert meta.updated is None
        assert meta.updated_at is None
