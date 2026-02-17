"""String manipulation utilities.

This module provides functions for string case conversion, truncation,
and JSON serialization.
"""

import json

from fastapi.encoders import jsonable_encoder


def camel_to_snake(name: str) -> str:
    """Convert a camelCase string to snake_case."""
    result = []
    last_chr = ""
    for char in name:
        if char.isupper():
            if not last_chr.isupper() and result:
                result.append("_")
            result.append(char.lower())
        else:
            result.append(char)
        last_chr = char
    return "".join(result)


def snake_to_camel(name: str, use_abbr: bool = True) -> str:
    """Convert a snake_case string to camelCase.

    Args:
        name: The snake_case string.
        use_abbr: Whether to uppercase common abbreviations.

    Returns:
        The camelCase string.
    """
    if not name:
        return name

    parts = name.split("_")
    if not parts:
        return name

    # Common abbreviations list
    abbreviations = {
        "id",
        "url",
        "api",
        "http",
        "https",
        "xml",
        "json",
        "css",
        "html",
        "sql",
        "db",
    }

    result = []
    for part in parts:
        if part.lower() in abbreviations and use_abbr:
            result.append(part.upper())
        else:
            if result:
                result.append(part.capitalize())
            else:
                result.append(part.lower())

    return "".join(result)


def snake_to_pascal(name: str, use_abbr: bool = True) -> str:
    """Convert a snake_case string to PascalCase.

    Args:
        name: The snake_case string.
        use_abbr: Whether to uppercase common abbreviations.

    Returns:
        The PascalCase string.
    """
    if not name:
        return name

    parts = name.split("_")
    if not parts:
        return name

    # Common abbreviations list
    abbreviations = {
        "id",
        "url",
        "api",
        "http",
        "https",
        "xml",
        "json",
        "css",
        "html",
        "sql",
        "db",
    }

    result = []
    for part in parts:
        if part.lower() in abbreviations and use_abbr:
            result.append(part.upper())
        else:
            result.append(part.capitalize())

    return "".join(result)


def truncate(text: str, limit: int = 100, ellipsis: str = "...") -> str:
    """Truncate text to a maximum length with an ellipsis.

    Args:
        text: The text to truncate.
        limit: Maximum length before truncation.
        ellipsis: The ellipsis string to append.

    Returns:
        The truncated text.
    """
    if len(text) > limit:
        return text[:limit] + ellipsis
    return text


def safe_json_dumps(data) -> str:
    """Safely dump data to JSON string with FastAPI encoding.

    Args:
        data: The data to serialize.

    Returns:
        The JSON string.
    """
    return json.dumps(jsonable_encoder(data), ensure_ascii=False)
