"""Templates API endpoint."""

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.calendar import today_business

router = APIRouter(prefix="/api/v1", tags=["templates"])


@router.get("/templates")
async def get_templates(request: Request) -> JSONResponse:
    """获取支持的模板列表。"""
    config = request.app.state.config
    base_domain = config.server.base_domain.rstrip("/")
    templates_config = config.get_templates_config()

    # Read today's data to get latest image URLs
    data_dir = Path(config.paths.cache_dir) / "data"
    today_str = today_business().isoformat()
    data_file = data_dir / f"{today_str}.json"
    images: dict = {}
    if data_file.exists():
        try:
            with data_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                images = data.get("images", {})
        except (OSError, json.JSONDecodeError):
            pass

    result = []
    for item in templates_config.items:
        filename = images.get(item.name)
        image_url = f"{base_domain}/static/{filename}" if filename else None
        result.append({
            "name": item.name,
            "description": f"摸鱼日历{item.name}模板",
            "image": image_url,
        })

    return JSONResponse(
        content={"data": result},
        headers={"Cache-Control": "public, max-age=3600"},
    )
