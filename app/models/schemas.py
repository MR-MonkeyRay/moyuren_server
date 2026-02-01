"""Pydantic data models for request/response validation."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ImageMeta(BaseModel):
    """Image metadata model."""
    date: str = Field(..., description="Date string in YYYY-MM-DD format")
    timestamp: str = Field(..., description="ISO format timestamp")
    image: str = Field(..., description="URL to the generated image")

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Validate date format."""
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError("date must be in YYYY-MM-DD format") from e
        return v

    @field_validator("timestamp")
    @classmethod
    def validate_iso_format(cls, v: str) -> str:
        """Validate ISO format timestamp."""
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError("timestamp must be in ISO format") from e
        return v


class MoyurenResponse(ImageMeta):
    """Response model for moyuren image API."""
    pass


class MoyurenImageResponse(BaseModel):
    """Response model for moyuren image metadata API."""
    date: str = Field(..., description="Image date in YYYY-MM-DD format")
    updated: str = Field(..., description="Generation time in YYYY/MM/DD HH:MM:SS format")
    updated_at: int = Field(..., description="Generation timestamp in milliseconds (13 digits)")
    image: str = Field(..., description="Full URL to the image file")


class FunContentSchema(BaseModel):
    """Fun content schema."""
    type: str = Field(..., description="Content type (joke, quote, etc.)")
    title: str = Field(..., description="Content title with emoji")
    text: str = Field(..., description="Content text")


class CountdownSchema(BaseModel):
    """Holiday countdown schema."""
    name: str = Field(..., description="Holiday name")
    date: str = Field(..., description="Holiday date in YYYY-MM-DD format")
    days_left: int = Field(..., description="Days left until holiday")


class MoyurenDetailResponse(BaseModel):
    """Response model for moyuren detail API."""
    date: str = Field(..., description="Image date in YYYY-MM-DD format")
    updated: str = Field(..., description="Generation time in YYYY/MM/DD HH:MM:SS format")
    updated_at: int = Field(..., description="Generation timestamp in milliseconds (13 digits)")
    weekday: str = Field(..., description="Weekday in Chinese (e.g., 星期日)")
    lunar_date: str = Field(..., description="Lunar calendar date")
    fun_content: Optional[FunContentSchema] = Field(None, description="Fun content (joke, quote, etc.)")
    countdowns: list[CountdownSchema] = Field(default_factory=list, description="Holiday countdowns")
    is_crazy_thursday: bool = Field(..., description="Whether it's Thursday")
    kfc_content: Optional[str] = Field(None, description="KFC Crazy Thursday content (only on Thursday)")


class ErrorDetail(BaseModel):
    """Error detail model."""
    code: str
    message: str
    detail: Optional[str] = None


class ErrorResponse(BaseModel):
    """Standardized error response model."""
    error: ErrorDetail
