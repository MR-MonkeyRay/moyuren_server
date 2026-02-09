"""Tests for app/models/schemas.py - Pydantic data models."""

from app.models.schemas import (
    DateInfoSchema,
    FunContentSchema,
    GuideSchema,
    MoyurenImageResponse,
    NewsMetaSchema,
    SolarTermSchema,
    WeekendSchema,
)


class TestMoyurenImageResponse:
    """Tests for MoyurenImageResponse model."""

    def test_valid_moyuren_image_response(self) -> None:
        """Test valid MoyurenImageResponse creation."""
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
    """Tests for FunContentSchema model."""

    def test_valid_fun_content(self) -> None:
        """Test valid FunContentSchema creation."""
        content = FunContentSchema(
            type="joke", title="🤣 冷笑话", text="为什么程序员总是分不清万圣节和圣诞节？因为 Oct 31 = Dec 25"
        )

        assert content.type == "joke"
        assert content.title == "🤣 冷笑话"
        assert "程序员" in content.text


class TestDateInfoSchema:
    """Tests for DateInfoSchema model."""

    def test_valid_date_info(self) -> None:
        """Test valid DateInfoSchema creation."""
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
        """Test DateInfoSchema with optional fields."""
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
    """Tests for WeekendSchema model."""

    def test_valid_weekend_schema(self) -> None:
        """Test valid WeekendSchema creation."""
        weekend = WeekendSchema(days_left=2, is_weekend=False)

        assert weekend.days_left == 2
        assert weekend.is_weekend is False

    def test_weekend_on_saturday(self) -> None:
        """Test WeekendSchema on weekend."""
        weekend = WeekendSchema(days_left=0, is_weekend=True)

        assert weekend.days_left == 0
        assert weekend.is_weekend is True


class TestSolarTermSchema:
    """Tests for SolarTermSchema model."""

    def test_valid_solar_term(self) -> None:
        """Test valid SolarTermSchema creation."""
        solar_term = SolarTermSchema(
            name="立春", name_en="Beginning of Spring", days_left=0, date="2026-02-04", is_today=True
        )

        assert solar_term.name == "立春"
        assert solar_term.name_en == "Beginning of Spring"
        assert solar_term.is_today is True

    def test_solar_term_not_today(self) -> None:
        """Test SolarTermSchema when not today."""
        solar_term = SolarTermSchema(name="雨水", name_en="Rain Water", days_left=15, date="2026-02-19", is_today=False)

        assert solar_term.days_left == 15
        assert solar_term.is_today is False


class TestGuideSchema:
    """Tests for GuideSchema model."""

    def test_valid_guide(self) -> None:
        """Test valid GuideSchema creation."""
        guide = GuideSchema(yi=["摸鱼", "喝茶", "休息"], ji=["加班", "开会", "焦虑"])

        assert len(guide.yi) == 3
        assert len(guide.ji) == 3
        assert "摸鱼" in guide.yi
        assert "加班" in guide.ji

    def test_guide_with_empty_lists(self) -> None:
        """Test GuideSchema with empty lists."""
        guide = GuideSchema(yi=[], ji=[])

        assert guide.yi == []
        assert guide.ji == []


class TestNewsMetaSchema:
    """Tests for NewsMetaSchema model."""

    def test_valid_news_meta(self) -> None:
        """Test valid NewsMetaSchema creation."""
        meta = NewsMetaSchema(date="2026年2月4日", updated="2026-02-04T06:00:00+08:00", updated_at=1738620000000)

        assert meta.date == "2026年2月4日"
        assert meta.updated == "2026-02-04T06:00:00+08:00"

    def test_news_meta_all_optional(self) -> None:
        """Test NewsMetaSchema with all optional fields."""
        meta = NewsMetaSchema()

        assert meta.date is None
        assert meta.updated is None
        assert meta.updated_at is None
