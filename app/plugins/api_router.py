from .manager import plugin_manager

from fastapi import APIRouter

plugin_routers: dict[str, APIRouter] = {}
plugin_router = APIRouter(prefix="/api/plugins")


def register_api() -> APIRouter:
    router = APIRouter()
    plugin = plugin_manager.get_plugin_from_frame()
    if plugin is None:
        raise RuntimeError("Failed to get plugin from frame when registering API route")
    plugin_routers[plugin.meta.id] = router
    return router
