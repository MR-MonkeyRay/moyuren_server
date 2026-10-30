"""Pydantic data models for request/response validation."""

import json
import os
import tempfile
from datetime import date as calendar_date
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from app.core.errors import ErrorCode, StorageError

DAILY_DATA_SCHEMA_VERSION = 1


class TemplatePreviewRequest(BaseModel):
    """Authenticated template preview using cached or deterministic sample data."""

    template: str
    date: str | None = None
    data: Literal["cached", "sample"] = "cached"

    @field_validator("date")
    @classmethod
    def validate_preview_date(cls, value: str | None) -> str | None:
        """Require an exact ISO calendar date when supplied."""
        if value is not None and calendar_date.fromisoformat(value).isoformat() != value:
            raise ValueError("date must use YYYY-MM-DD format")
        return value


class TemplateListItemResponse(BaseModel):
    """Public template metadata, including the optional HTML description."""

    name: str
    description: str | None = None
    image: str | None = None
    show_daily_english: bool = True


def _validate_image_filename(filename: str) -> str:
    """Require filenames confined to the images directory."""
    if not filename.strip() or any(part in filename for part in ("/", "\\", "..", "\x00")):
        raise ValueError("images must contain non-empty filenames without path separators or '..'")
    return filename


class ImageVariant(BaseModel):
    """Image representation stored alongside the rendered JPEG."""

    filename: str
    format: Literal["jpeg", "webp"]
    width: int = Field(gt=0, strict=True)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        """Apply the same filename contract as the base image mapping."""
        return _validate_image_filename(value)


class ImageVariantResponse(BaseModel):
    """Public variant metadata with an absolute image URL."""

    image: str
    format: Literal["jpeg", "webp"]
    width: int


class MoyurenImageResponse(BaseModel):
    """Response model for moyuren image metadata API."""

    date: str = Field(..., description="Image date in YYYY-MM-DD format")
    updated: str = Field(..., description="Generation time (e.g., 2025/01/13 07:22:32)")
    updated_at: int = Field(..., description="Generation timestamp in milliseconds (13 digits)")
    image: str = Field(..., description="Full URL to the image file")
    variants: list[ImageVariantResponse] = Field(default_factory=list)


class FunContentSchema(BaseModel):
    """Fun content schema."""

    type: str = Field(..., description="Content type (joke, quote, etc.)")
    title: str = Field(..., description="Content title with emoji")
    text: str = Field(..., description="Content text")


class DailyDataFile(BaseModel):
    """Versioned daily cache data with lossless optional source payloads."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = DAILY_DATA_SCHEMA_VERSION
    date: str
    updated: str
    updated_at: int = Field(ge=0, strict=True)
    images: dict[str, str]
    image_variants: dict[str, list[ImageVariant]] | None = None
    # Optional payloads stay as raw JSON: historical files may use older shapes.
    weekday: JsonValue = None
    lunar_date: JsonValue = None
    is_crazy_thursday: JsonValue = None
    date_info: JsonValue = None
    weekend: JsonValue = None
    solar_term: JsonValue = None
    guide: JsonValue = None
    holidays: JsonValue = None
    news_list: JsonValue = None
    news_meta: JsonValue = None
    fun_content: JsonValue = None
    kfc_content: JsonValue = None
    kfc_content_full: JsonValue = None
    stock_indices: JsonValue = None
    gold_price: JsonValue = None
    daily_english: JsonValue = None
    is_stale: bool | None = None
    source_date: str | None = None
    stale_sources: list[str] | None = None

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        """Require a calendar date in the canonical YYYY-MM-DD format."""
        if calendar_date.fromisoformat(value).isoformat() != value:
            raise ValueError("date must use YYYY-MM-DD format")
        return value

    @field_validator("images")
    @classmethod
    def validate_images(cls, value: dict[str, str]) -> dict[str, str]:
        """Require image filenames that stay within the images directory."""
        for filename in value.values():
            _validate_image_filename(filename)
        return value

    @classmethod
    def load(cls, path: Path) -> Self:
        """Read and validate a daily cache file, reporting storage errors."""
        try:
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            raise StorageError(
                message=f"Failed to read data file {path}: {exc}",
                code=ErrorCode.STORAGE_READ_FAILED,
            ) from exc

    def dump_json(self) -> str:
        """Serialize present fields and extras, always including the schema version."""
        data = self.model_dump(mode="json", exclude_unset=True)
        data["schema_version"] = self.schema_version
        return json.dumps(data, ensure_ascii=False, indent=2)

    def save_atomic(self, path: Path) -> None:
        """Write a UTF-8 temporary file and atomically replace the daily cache."""
        tmp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".data_", suffix=".tmp", delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tmp_file.write(self.dump_json())
            os.replace(tmp_path, path)
        except Exception as exc:
            raise StorageError(message=f"Failed to update data file {path}: {exc}") from exc
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)


class DateInfoSchema(BaseModel):
    """Date information schema with full calendar details."""

    year_month: str = Field(..., description="Year and month (e.g., 2026.02)")
    day: str = Field(..., description="Day of month")
    week_cn: str = Field(..., description="Weekday in Chinese (e.g., 星期一)")
    week_en: str = Field(..., description="Weekday in English (e.g., Mon)")
    lunar_year: str = Field(..., description="Lunar year (e.g., 乙巳年)")
    lunar_date: str = Field(..., description="Lunar date (e.g., 正月初五)")
    zodiac: str = Field(..., description="Chinese zodiac (e.g., 蛇)")
    constellation: str = Field(..., description="Constellation (e.g., 水瓶座)")
    moon_phase: str = Field(..., description="Moon phase (e.g., 新月)")
    festival_solar: str | None = Field(None, description="Solar festival name")
    festival_lunar: str | None = Field(None, description="Lunar festival name")
    legal_holiday: str | None = Field(None, description="Legal holiday name")
    is_holiday: bool = Field(..., description="Whether today is a holiday")


class WeekendSchema(BaseModel):
    """Weekend countdown information."""

    days_left: int = Field(..., description="Days until weekend (0 if weekend)")
    is_weekend: bool = Field(..., description="Whether today is weekend")


class SolarTermSchema(BaseModel):
    """Solar term information."""

    name: str = Field(..., description="Solar term name in Chinese (e.g., 立春)")
    name_en: str = Field(..., description="Solar term name in English (e.g., Beginning of Spring)")
    days_left: int = Field(..., description="Days until this solar term")
    date: str = Field(..., description="Solar term date in YYYY-MM-DD format")
    is_today: bool = Field(..., description="Whether the solar term is today")


class GuideSchema(BaseModel):
    """Yi/Ji guide schema for daily activities."""

    yi: list[str] = Field(..., description="Auspicious activities (宜)")
    ji: list[str] = Field(..., description="Inauspicious activities (忌)")


class NewsMetaSchema(BaseModel):
    """News source metadata."""

    date: str | None = Field(None, description="News date")
    updated: str | None = Field(None, description="News update time (e.g., 2025/01/13 07:22:32)")
    updated_at: int | None = Field(None, description="News update timestamp in milliseconds")


class HolidayDetailSchema(BaseModel):
    """Detailed holiday information."""

    name: str = Field(..., description="Holiday name")
    start_date: str = Field(..., description="Start date in YYYY-MM-DD format")
    end_date: str = Field(..., description="End date in YYYY-MM-DD format")
    duration: int = Field(..., description="Duration in days")
    days_left: int = Field(..., description="Days left until holiday starts")
    is_legal_holiday: bool = Field(..., description="Whether it's a legal holiday")
    is_off_day: bool = Field(..., description="Whether it's an off day")


class KfcContentSchema(BaseModel):
    """KFC Crazy Thursday content."""

    title: str = Field(..., description="Title (CRAZY THURSDAY)")
    sub_title: str = Field(..., description="Sub title (V我50)")
    content: str = Field(..., description="KFC promotional copy")


class StockIndexItemSchema(BaseModel):
    """Stock index item schema."""

    code: str = Field(..., description="Index code (e.g., 000001)")
    name: str = Field(..., description="Index name (e.g., 上证指数)")
    price: float | None = Field(None, description="Current price")
    change: float | None = Field(None, description="Price change")
    change_pct: float | None = Field(None, description="Change percentage")
    trend: str = Field(..., description="Trend direction (up/down/flat)")
    market: str = Field(..., description="Market code (A/HK/US)")
    is_trading_day: bool = Field(..., description="Whether market is trading today")


class StockIndicesSchema(BaseModel):
    """Stock indices data schema."""

    items: list[StockIndexItemSchema] = Field(default_factory=list, description="List of stock indices")
    updated: str = Field(..., description="Last update time (e.g., 2025/01/13 07:22:32)")
    updated_at: int = Field(..., description="Update timestamp in milliseconds")
    trading_day: dict[str, bool] = Field(..., description="Trading day status by market")
    is_stale: bool = Field(..., description="Whether data is stale/cached")
    is_data_missing: bool = Field(
        False, description="True for the placeholder returned when every quote fetch failed"
    )


class GoldPriceSchema(BaseModel):
    """Gold price data schema (raw values from the upstream API)."""

    today_price: str | float | None = Field(None, description="Today's gold price")
    sell_price: str | float | None = Field(None, description="Gold buy-back price")
    unit: str = Field("元/克", description="Price unit")


class MoyurenDetailResponse(BaseModel):
    """Response model for moyuren detail API."""

    date: str = Field(..., description="Image date in YYYY-MM-DD format")
    updated: str = Field(..., description="Generation time (e.g., 2025/01/13 07:22:32)")
    updated_at: int = Field(..., description="Generation timestamp in milliseconds (13 digits)")
    image: str = Field(..., description="Full URL to the image file")
    variants: list[ImageVariantResponse] = Field(default_factory=list)
    weekday: str = Field(..., description="Weekday in Chinese (e.g., 星期日)")
    lunar_date: str = Field(..., description="Lunar calendar date")
    fun_content: FunContentSchema | None = Field(None, description="Fun content (joke, quote, etc.)")
    is_crazy_thursday: bool = Field(..., description="Whether it's Thursday")
    kfc_content: str | None = Field(None, description="KFC Crazy Thursday content (only on Thursday)")
    # Full rendering data fields
    date_info: DateInfoSchema | None = Field(None, description="Full date information")
    weekend: WeekendSchema | None = Field(None, description="Weekend countdown info")
    solar_term: SolarTermSchema | None = Field(None, description="Solar term information")
    guide: GuideSchema | None = Field(None, description="Yi/Ji daily guide")
    news_list: list[str] | None = Field(None, description="News text list")
    news_meta: NewsMetaSchema | None = Field(None, description="News source metadata")
    holidays: list[HolidayDetailSchema] | None = Field(None, description="Detailed holiday list")
    kfc_content_full: KfcContentSchema | None = Field(None, description="Full KFC content object")
    stock_indices: StockIndicesSchema | None = Field(None, description="Stock market indices data")
    gold_price: GoldPriceSchema | None = Field(None, description="Gold price data")
    is_stale: bool = Field(False, description="Whether rendered data is stale")
    source_date: str | None = Field(None, description="Source data date in YYYY-MM-DD format")


class ErrorDetail(BaseModel):
    """Error detail model."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Standardized error response model."""

    error: ErrorDetail
