from fastapi import Request

from app.core.services import AppServices


def get_services(request: Request) -> AppServices:
    """获取服务容器"""
    return request.app.state.services
