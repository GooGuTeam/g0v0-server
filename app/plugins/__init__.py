"""Plugin system for the g0v0-server.

This module provides a plugin system that allows extending the server
functionality through external plugins. Plugins can register API routes,
subscribe to events, and integrate with the server lifecycle.

Classes:
    EventHub: Event bus for plugin event handling.
    PluginManager: Manages plugin discovery, loading, and lifecycle.

Functions:
    register_api: Register API routes for a plugin.

Variables:
    hub: Global EventHub instance.
    manager: Global PluginManager instance.
    plugin_router: FastAPI router for all plugin routes.
    plugin_routers: Registry of plugin-specific routers.

References:
    - https://docs.g0v0.top/lazer/development/plugin/
"""

from .api_router import plugin_router, plugin_routers, register_api
from .event_hub import (
    EventHub,
    hub,
)
from .manager import manager as manager

__all__ = ["EventHub", "hub", "manager", "plugin_router", "plugin_routers", "register_api"]
