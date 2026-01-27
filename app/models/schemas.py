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


class ErrorDetail(BaseModel):
    """Error detail model."""
    code: str
    message: str
    detail: Optional[str] = None


class ErrorResponse(BaseModel):
    """Standardized error response model."""
    error: ErrorDetail
