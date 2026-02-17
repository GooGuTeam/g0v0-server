"""Plugin API router registration utilities.

This module provides utilities for plugins to register their API routes
with the main application.

Functions:
    register_api: Create and register an API router for a plugin.

Variables:
    plugin_router: Main router that includes all plugin routes.
    plugin_routers: Dictionary mapping plugin IDs to their routers.
"""

from typing import Any

from .manager import manager

from fastapi import APIRouter

plugin_routers: dict[str, APIRouter] = {}
plugin_router = APIRouter(prefix="/api/plugins")


def register_api(**kwargs: Any) -> APIRouter:
    """Create and register an API router for the calling plugin.

    This function creates a new FastAPI router and associates it with
    the plugin that called it. The router will be mounted under
    /api/plugins/{plugin_id}/ when the plugin is loaded.

    Args:
        **kwargs: Arguments to pass to the APIRouter constructor.

    Returns:
        A new APIRouter instance for the plugin to use.

    Raises:
        RuntimeError: If the calling plugin cannot be determined.
    """
    router = APIRouter(**kwargs)
    plugin = manager.get_plugin_from_frame()
    if plugin is None:
        raise RuntimeError("Failed to get plugin from frame when registering API route")
    plugin_routers[plugin.meta.id] = router
    return router
