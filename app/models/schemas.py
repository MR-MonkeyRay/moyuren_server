"""用于请求和响应校验的 Pydantic 数据模型。"""

import json
import os
import tempfile
from datetime import date as calendar_date
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from app.core.errors import ErrorCode, StorageError
from app.core.fileutil import set_output_permissions

DAILY_DATA_SCHEMA_VERSION = 1


class TemplatePreviewRequest(BaseModel):
    """需鉴权的模板预览，使用缓存或确定性样例数据。"""

    template: str
    date: str | None = None
    data: Literal["cached", "sample"] = "cached"

    @field_validator("date")
    @classmethod
    def validate_preview_date(cls, value: str | None) -> str | None:
        """提供日期时，要求严格使用 ISO 日期格式。"""
        if value is not None and calendar_date.fromisoformat(value).isoformat() != value:
            raise ValueError("date must use YYYY-MM-DD format")
        return value


class TemplateListItemResponse(BaseModel):
    """公开的模板元数据，含可选的 HTML 描述。"""

    name: str
    description: str | None = None
    image: str | None = None
    show_daily_english: bool = True


def _validate_image_filename(filename: str) -> str:
    """要求文件名不越出图片目录。"""
    if not filename.strip() or any(part in filename for part in ("/", "\\", "..", "\x00")):
        raise ValueError("images must contain non-empty filenames without path separators or '..'")
    return filename


class ImageVariant(BaseModel):
    """与渲染后的 JPEG 一同存储的图片变体。"""

    filename: str
    format: Literal["jpeg", "webp"]
    width: int = Field(gt=0, strict=True)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        """使用与原图映射相同的文件名约束。"""
        return _validate_image_filename(value)


class ImageVariantResponse(BaseModel):
    """公开的图片变体元数据，包含图片的绝对 URL。"""

    image: str
    format: Literal["jpeg", "webp"]
    width: int


class MoyurenImageResponse(BaseModel):
    """摸鱼图片元数据 API 的响应模型。"""

    date: str = Field(..., description="图片日期，格式为 YYYY-MM-DD")
    updated: str = Field(..., description="生成时间（例如 2025/01/13 07:22:32）")
    updated_at: int = Field(..., description="生成时间的毫秒时间戳（13 位）")
    image: str = Field(..., description="图片文件的完整 URL")
    variants: list[ImageVariantResponse] = Field(default_factory=list)


class FunContentSchema(BaseModel):
    """趣味内容模型。"""

    type: str = Field(..., description="内容类型（joke、quote 等）")
    title: str = Field(..., description="含 emoji 的内容标题")
    text: str = Field(..., description="内容正文")


class DailyDataFile(BaseModel):
    """带版本的日级缓存数据，无损保留可选的来源数据。"""

    model_config = ConfigDict(extra="allow")

    schema_version: int = DAILY_DATA_SCHEMA_VERSION
    date: str
    updated: str
    updated_at: int = Field(ge=0, strict=True)
    images: dict[str, str]
    image_variants: dict[str, list[ImageVariant]] | None = None
    # 可选数据保留原始 JSON：历史文件可能使用旧格式。
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
        """要求日期使用规范的 YYYY-MM-DD 格式。"""
        if calendar_date.fromisoformat(value).isoformat() != value:
            raise ValueError("date must use YYYY-MM-DD format")
        return value

    @field_validator("images")
    @classmethod
    def validate_images(cls, value: dict[str, str]) -> dict[str, str]:
        """要求图片文件名不越出图片目录。"""
        for filename in value.values():
            _validate_image_filename(filename)
        return value

    @classmethod
    def load(cls, path: Path) -> Self:
        """读取并校验日级缓存文件，出错时报告存储异常。"""
        try:
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as exc:
            raise StorageError(
                message=f"Failed to read data file {path}: {exc}",
                code=ErrorCode.STORAGE_READ_FAILED,
            ) from exc

    def dump_json(self) -> str:
        """序列化已设置字段和额外字段，始终包含结构版本号。"""
        data = self.model_dump(mode="json", exclude_unset=True)
        data["schema_version"] = self.schema_version
        return json.dumps(data, ensure_ascii=False, indent=2)

    def save_atomic(self, path: Path) -> None:
        """写入 UTF-8 临时文件，并原子替换日级缓存。"""
        tmp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".data_", suffix=".tmp", delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tmp_file.write(self.dump_json())
            set_output_permissions(tmp_path)
            os.replace(tmp_path, path)
        except Exception as exc:
            raise StorageError(message=f"Failed to update data file {path}: {exc}") from exc
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)


class DateInfoSchema(BaseModel):
    """包含完整日历详情的日期信息模型。"""

    year_month: str = Field(..., description="年月（例如 2026.02）")
    day: str = Field(..., description="当月第几日")
    week_cn: str = Field(..., description="中文星期（例如 星期一）")
    week_en: str = Field(..., description="英文星期（例如 Mon）")
    lunar_year: str = Field(..., description="农历年份（例如 乙巳年）")
    lunar_date: str = Field(..., description="农历日期（例如 正月初五）")
    zodiac: str = Field(..., description="生肖（例如 蛇）")
    constellation: str = Field(..., description="星座（例如 水瓶座）")
    moon_phase: str = Field(..., description="月相（例如 新月）")
    festival_solar: str | None = Field(None, description="公历节日名称")
    festival_lunar: str | None = Field(None, description="农历节日名称")
    legal_holiday: str | None = Field(None, description="法定节假日名称")
    is_holiday: bool = Field(..., description="今天是否为节假日")


class WeekendSchema(BaseModel):
    """周末倒计时信息。"""

    days_left: int = Field(..., description="距周末的天数（周末为 0）")
    is_weekend: bool = Field(..., description="今天是否为周末")


class SolarTermSchema(BaseModel):
    """节气信息。"""

    name: str = Field(..., description="节气中文名（例如 立春）")
    name_en: str = Field(..., description="节气英文名（例如 Beginning of Spring）")
    days_left: int = Field(..., description="距该节气的天数")
    date: str = Field(..., description="节气日期，格式为 YYYY-MM-DD")
    is_today: bool = Field(..., description="今天是否为该节气")


class GuideSchema(BaseModel):
    """每日活动宜忌模型。"""

    yi: list[str] = Field(..., description="适宜的活动（宜）")
    ji: list[str] = Field(..., description="忌讳的活动（忌）")


class NewsMetaSchema(BaseModel):
    """新闻来源元数据。"""

    date: str | None = Field(None, description="新闻日期")
    updated: str | None = Field(None, description="新闻更新时间（例如 2025/01/13 07:22:32）")
    updated_at: int | None = Field(None, description="新闻更新的毫秒时间戳")


class HolidayDetailSchema(BaseModel):
    """节假日详细信息。"""

    name: str = Field(..., description="节假日名称")
    start_date: str = Field(..., description="开始日期，格式为 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期，格式为 YYYY-MM-DD")
    duration: int = Field(..., description="持续天数")
    days_left: int = Field(..., description="距节假日开始的天数")
    is_legal_holiday: bool = Field(..., description="是否为法定节假日")
    is_off_day: bool = Field(..., description="是否为休息日")


class KfcContentSchema(BaseModel):
    """KFC 疯狂星期四内容。"""

    title: str = Field(..., description="标题（CRAZY THURSDAY）")
    sub_title: str = Field(..., description="副标题（V我50）")
    content: str = Field(..., description="KFC 宣传文案")


class StockIndexItemSchema(BaseModel):
    """单个股票指数模型。"""

    code: str = Field(..., description="指数代码（例如 000001）")
    name: str = Field(..., description="指数名称（例如 上证指数）")
    price: float | None = Field(None, description="当前指数点位")
    change: float | None = Field(None, description="涨跌额")
    change_pct: float | None = Field(None, description="涨跌幅百分比")
    trend: str = Field(..., description="涨跌方向（up/down/flat）")
    market: str = Field(..., description="市场代码（A/HK/US）")
    is_trading_day: bool = Field(..., description="今天是否为该市场的交易日")


class StockIndicesSchema(BaseModel):
    """股票指数数据模型。"""

    items: list[StockIndexItemSchema] = Field(default_factory=list, description="股票指数列表")
    updated: str = Field(..., description="最近更新时间（例如 2025/01/13 07:22:32）")
    updated_at: int = Field(..., description="更新的毫秒时间戳")
    trading_day: dict[str, bool] = Field(..., description="各市场的交易日状态")
    is_stale: bool = Field(..., description="数据是否已过期（TTL 内的缓存不视为过期）")
    is_data_missing: bool = Field(
        False, description="所有行情获取均失败时，返回的占位数据中此值为 True"
    )


class GoldPriceSchema(BaseModel):
    """金价数据模型（保留上游 API 的原始值）。"""

    today_price: str | float | None = Field(None, description="今日金价")
    sell_price: str | float | None = Field(None, description="黄金回收价")
    unit: str = Field("元/克", description="价格单位")


class MoyurenDetailResponse(BaseModel):
    """摸鱼详情 API 的响应模型。"""

    date: str = Field(..., description="图片日期，格式为 YYYY-MM-DD")
    updated: str = Field(..., description="生成时间（例如 2025/01/13 07:22:32）")
    updated_at: int = Field(..., description="生成时间的毫秒时间戳（13 位）")
    image: str = Field(..., description="图片文件的完整 URL")
    variants: list[ImageVariantResponse] = Field(default_factory=list)
    weekday: str = Field(..., description="中文星期（例如 星期日）")
    lunar_date: str = Field(..., description="农历日期")
    fun_content: FunContentSchema | None = Field(None, description="趣味内容（笑话、一言等）")
    is_crazy_thursday: bool = Field(..., description="是否为星期四")
    kfc_content: str | None = Field(None, description="KFC 疯狂星期四内容（仅星期四提供）")
    # 完整渲染数据字段
    date_info: DateInfoSchema | None = Field(None, description="完整日期信息")
    weekend: WeekendSchema | None = Field(None, description="周末倒计时信息")
    solar_term: SolarTermSchema | None = Field(None, description="节气信息")
    guide: GuideSchema | None = Field(None, description="每日宜忌指南")
    news_list: list[str] | None = Field(None, description="新闻文本列表")
    news_meta: NewsMetaSchema | None = Field(None, description="新闻来源元数据")
    holidays: list[HolidayDetailSchema] | None = Field(None, description="节假日详情列表")
    kfc_content_full: KfcContentSchema | None = Field(None, description="完整 KFC 内容对象")
    stock_indices: StockIndicesSchema | None = Field(None, description="股票市场指数数据")
    gold_price: GoldPriceSchema | None = Field(None, description="金价数据")
    is_stale: bool = Field(False, description="渲染数据是否过期")
    source_date: str | None = Field(None, description="来源数据日期，格式为 YYYY-MM-DD")


class ErrorDetail(BaseModel):
    """错误详情模型。"""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """标准错误响应模型。"""

    error: ErrorDetail
