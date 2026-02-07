"""Pydantic data models for request/response validation."""

from typing import Optional
from pydantic import BaseModel, Field


class MoyurenImageResponse(BaseModel):
    """Response model for moyuren image metadata API."""
    date: str = Field(..., description="Image date in YYYY-MM-DD format")
    updated: str = Field(..., description="Generation time (e.g., 2025/01/13 07:22:32)")
    updated_at: int = Field(..., description="Generation timestamp in milliseconds (13 digits)")
    image: str = Field(..., description="Full URL to the image file")


class FunContentSchema(BaseModel):
    """Fun content schema."""
    type: str = Field(..., description="Content type (joke, quote, etc.)")
    title: str = Field(..., description="Content title with emoji")
    text: str = Field(..., description="Content text")


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
    festival_solar: Optional[str] = Field(None, description="Solar festival name")
    festival_lunar: Optional[str] = Field(None, description="Lunar festival name")
    legal_holiday: Optional[str] = Field(None, description="Legal holiday name")
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
    date: Optional[str] = Field(None, description="News date")
    updated: Optional[str] = Field(None, description="News update time (e.g., 2025/01/13 07:22:32)")
    updated_at: Optional[int] = Field(None, description="News update timestamp in milliseconds")


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
    price: Optional[float] = Field(None, description="Current price")
    change: Optional[float] = Field(None, description="Price change")
    change_pct: Optional[float] = Field(None, description="Change percentage")
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



class MoyurenDetailResponse(BaseModel):
    """Response model for moyuren detail API."""
    date: str = Field(..., description="Image date in YYYY-MM-DD format")
    updated: str = Field(..., description="Generation time (e.g., 2025/01/13 07:22:32)")
    updated_at: int = Field(..., description="Generation timestamp in milliseconds (13 digits)")
    image: str = Field(..., description="Full URL to the image file")
    weekday: str = Field(..., description="Weekday in Chinese (e.g., 星期日)")
    lunar_date: str = Field(..., description="Lunar calendar date")
    fun_content: Optional[FunContentSchema] = Field(None, description="Fun content (joke, quote, etc.)")
    is_crazy_thursday: bool = Field(..., description="Whether it's Thursday")
    kfc_content: Optional[str] = Field(None, description="KFC Crazy Thursday content (only on Thursday)")
    # Full rendering data fields
    date_info: Optional[DateInfoSchema] = Field(None, description="Full date information")
    weekend: Optional[WeekendSchema] = Field(None, description="Weekend countdown info")
    solar_term: Optional[SolarTermSchema] = Field(None, description="Solar term information")
    guide: Optional[GuideSchema] = Field(None, description="Yi/Ji daily guide")
    news_list: Optional[list[str]] = Field(None, description="News text list")
    news_meta: Optional[NewsMetaSchema] = Field(None, description="News source metadata")
    holidays: Optional[list[HolidayDetailSchema]] = Field(None, description="Detailed holiday list")
    kfc_content_full: Optional[KfcContentSchema] = Field(None, description="Full KFC content object")
    stock_indices: Optional[StockIndicesSchema] = Field(None, description="Stock market indices data")


class ErrorDetail(BaseModel):
    """Error detail model."""
    code: str
    message: str
    detail: Optional[str] = None


class ErrorResponse(BaseModel):
    """Standardized error response model."""
    error: ErrorDetail
