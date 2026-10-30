"""Prometheus 指标端点。"""

from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.metrics import registry

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """导出专用注册表中的 Prometheus 指标；未启用时与未注册路由一样返回 404。"""
    config = getattr(request.app.state, "config", None)
    if config is None or not config.metrics.enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(
        content=generate_latest(registry),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
