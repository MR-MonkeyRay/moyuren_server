"""模板列表 API 端点。"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.errors import StorageError
from app.models.schemas import DailyDataFile, TemplateListItemResponse
from app.services.calendar import today_business

router = APIRouter(prefix="/api/v1", tags=["templates"])


@router.get("/templates", response_model=dict[str, list[TemplateListItemResponse]])
async def get_templates(request: Request) -> JSONResponse:
    """获取支持的模板列表。"""
    config = request.app.state.config
    base_domain = config.server.base_domain.rstrip("/")
    templates_config = await asyncio.to_thread(config.get_templates_config)

    # 读取当天数据，取各模板最新图片 URL
    data_dir = Path(config.paths.cache_dir) / "data"
    today_str = today_business().isoformat()
    data_file = data_dir / f"{today_str}.json"
    images: dict[str, str] = {}
    if data_file.exists():
        try:
            images = DailyDataFile.load(data_file).images
        except StorageError:
            pass

    result = []
    for item in templates_config.items:
        filename = images.get(item.name)
        image_url = f"{base_domain}/static/{filename}" if filename else None
        result.append({
            "name": item.name,
            "description": item.description,
            "image": image_url,
            "show_daily_english": item.show_daily_english,
        })

    return JSONResponse(
        content={"data": result},
        headers={"Cache-Control": "public, max-age=300, must-revalidate"},
    )
