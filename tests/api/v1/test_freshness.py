"""HTTP freshness responses using local metadata without application lifespan."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import app.api.v1.moyuren as moyuren_module
from app.models.schemas import DailyDataFile


@pytest.mark.parametrize(
    ("stored_freshness", "expected_stale", "expected_date"),
    [
        ({}, False, None),
        ({"is_stale": True, "source_date": "2026-03-10"}, True, "2026-03-10"),
        ({"is_stale": None, "source_date": None}, False, None),
    ],
)
@pytest.mark.asyncio
async def test_detail_api_returns_stored_freshness(
    stored_freshness: dict[str, Any],
    expected_stale: bool,
    expected_date: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose freshness fields over HTTP and preserve legacy default values."""
    app = FastAPI()
    app.include_router(moyuren_module.router)
    app.state.config = SimpleNamespace(
        paths=SimpleNamespace(cache_dir=str(tmp_path)),
        server=SimpleNamespace(base_domain="https://example.com"),
    )
    monkeypatch.setattr(moyuren_module, "today_business", lambda: date(2026, 3, 12))
    DailyDataFile.model_validate({
        "date": "2026-03-11", "updated": "2026/03/11 10:00:00", "updated_at": 1,
        "images": {"moyuren": "moyuren.jpg"}, **stored_freshness,
    }).save_atomic(tmp_path / "data" / "2026-03-11.json")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        detail = await client.get("/api/v1/moyuren?date=2026-03-11&detail=true")
        simple = await client.get("/api/v1/moyuren?date=2026-03-11")

    assert detail.status_code == 200
    assert detail.json()["is_stale"] is expected_stale
    assert detail.json()["source_date"] == expected_date
    assert detail.json()["image"] == "https://example.com/static/moyuren.jpg"
    assert simple.status_code == 200
    assert set(simple.json()) == {"date", "updated", "updated_at", "image", "variants"}
