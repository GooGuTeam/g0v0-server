"""Utility functions and helpers module.

This module re-exports functions from app.helpers for backward compatibility.
New code should import from app.helpers directly.
"""

# Re-export all functions from helpers for backward compatibility
from app.helpers import (
    BackgroundTasks,
    api_doc,
    are_adjacent_weeks,
    are_same_weeks,
    bg_tasks,
    camel_to_snake,
    check_image,
    extract_user_agent,
    hex_to_hue,
    is_async_callable,
    run_in_threadpool,
    safe_json_dumps,
    snake_to_camel,
    snake_to_pascal,
    truncate,
    type_is_optional,
    unix_timestamp_to_windows,
    utcnow,
)

__all__ = [
    "BackgroundTasks",
    "api_doc",
    "are_adjacent_weeks",
    "are_same_weeks",
    "bg_tasks",
    "camel_to_snake",
    "check_image",
    "extract_user_agent",
    "hex_to_hue",
    "is_async_callable",
    "run_in_threadpool",
    "safe_json_dumps",
    "snake_to_camel",
    "snake_to_pascal",
    "truncate",
    "type_is_optional",
    "unix_timestamp_to_windows",
    "utcnow",
]
