"""Helper utilities for the g0v0-server.

This module provides various helper classes and functions used throughout
the application, including asset proxying and GeoIP lookups.

Modules:
    asset_proxy: Asset URL replacement utilities.
    geoip: GeoLite2 database management and IP lookups.
    background_task: Background task management utilities.
    http: HTTP and API documentation utilities.
    strings: String manipulation utilities.
    time: Date and time utilities.
    type: Type conversion utilities.
"""

from .asset_proxy import asset_proxy_response, replace_asset_urls
from .background_task import (
    BackgroundTasks,
    bg_tasks,
    is_async_callable,
    run_in_threadpool,
)
from .color import hex_to_hue
from .geoip import GeoIPHelper, GeoIPLookupResult
from .http import (
    api_doc,
    check_image,
    extract_user_agent,
)
from .strings import (
    camel_to_snake,
    safe_json_dumps,
    snake_to_camel,
    snake_to_pascal,
    truncate,
)
from .time import (
    are_adjacent_weeks,
    are_same_weeks,
    unix_timestamp_to_windows,
    utcnow,
)
from .type import type_is_optional

__all__ = [
    "BackgroundTasks",
    "GeoIPHelper",
    "GeoIPLookupResult",
    "api_doc",
    "are_adjacent_weeks",
    "are_same_weeks",
    "asset_proxy_response",
    "bg_tasks",
    "camel_to_snake",
    "check_image",
    "extract_user_agent",
    "hex_to_hue",
    "is_async_callable",
    "replace_asset_urls",
    "run_in_threadpool",
    "safe_json_dumps",
    "snake_to_camel",
    "snake_to_pascal",
    "truncate",
    "type_is_optional",
    "unix_timestamp_to_windows",
    "utcnow",
]
