"""Utility functions and helpers module.

This module provides various utility functions for string manipulation,
date/time handling, image validation, user agent parsing, background task
management, and type introspection.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
import functools
import inspect
from io import BytesIO
import json
import re
from types import NoneType, UnionType
from typing import TYPE_CHECKING, Any, ParamSpec, TypedDict, TypeVar, Union, get_args, get_origin

from fastapi.encoders import jsonable_encoder
from PIL import Image

if TYPE_CHECKING:
    from app.models.model import UserAgentInfo


def unix_timestamp_to_windows(timestamp: int) -> int:
    """Convert a Unix timestamp to a Windows timestamp."""
    return (timestamp + 62135596800) * 10_000_000


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


def are_adjacent_weeks(dt1: datetime, dt2: datetime) -> bool:
    """Check if two datetime objects are in adjacent weeks.

    Args:
        dt1: The first datetime.
        dt2: The second datetime.

    Returns:
        True if the dates are in adjacent weeks, False otherwise.
    """
    y1, w1, _ = dt1.isocalendar()
    y2, w2, _ = dt2.isocalendar()

    # Sort by (year, week), ensure dt1 <= dt2
    if (y1, w1) > (y2, w2):
        y1, w1, y2, w2 = y2, w2, y1, w1

    # Same year, adjacent week numbers
    if y1 == y2 and w2 - w1 == 1:
        return True

    # Year boundary: check if y2 is next year, w2 == 1, and w1 is last week of y1
    if y2 == y1 + 1 and w2 == 1:
        # Determine last week number of y1
        last_week_y1 = datetime(y1, 12, 28).isocalendar()[1]  # 12-28 is guaranteed in last week
        if w1 == last_week_y1:
            return True

    return False


def are_same_weeks(dt1: datetime, dt2: datetime) -> bool:
    """Check if two datetime objects are in the same week.

    Args:
        dt1: The first datetime.
        dt2: The second datetime.

    Returns:
        True if the dates are in the same week, False otherwise.
    """
    return dt1.isocalendar()[:2] == dt2.isocalendar()[:2]


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


def check_image(content: bytes, size: int, width: int, height: int) -> str:
    """Validate an image's format, size, and dimensions.

    Args:
        content: The image content as bytes.
        size: Maximum allowed file size in bytes.
        width: Maximum allowed width in pixels.
        height: Maximum allowed height in pixels.

    Returns:
        The image format string (lowercase).

    Raises:
        RequestError: If the image fails validation.
    """
    from app.models.error import ErrorType, RequestError

    if len(content) > size:  # 10MB limit
        raise RequestError(ErrorType.FILE_SIZE_EXCEEDS_LIMIT)
    elif len(content) == 0:
        raise RequestError(ErrorType.FILE_EMPTY)
    try:
        with Image.open(BytesIO(content)) as img:
            if img.format not in ["PNG", "JPEG", "GIF"]:
                raise RequestError(ErrorType.INVALID_IMAGE_FORMAT)
            if img.size[0] > width or img.size[1] > height:
                raise RequestError(ErrorType.IMAGE_DIMENSIONS_EXCEED_LIMIT, {"args": f"{width}x{height}"})
            return img.format.lower()
    except RequestError:
        raise
    except Exception as e:
        raise RequestError(ErrorType.ERROR_PROCESSING_IMAGE, {"args": str(e)})


def extract_user_agent(user_agent: str | None) -> "UserAgentInfo":
    """Parse a User-Agent string to extract browser, OS, and device info.

    Args:
        user_agent: The User-Agent header string.

    Returns:
        UserAgentInfo containing parsed browser, OS, and device information.
    """
    from app.models.model import UserAgentInfo

    raw_ua = user_agent or ""
    ua = raw_ua.strip()
    lower_ua = ua.lower()

    info = UserAgentInfo(raw_ua=raw_ua)

    if not ua:
        return info

    client_identifiers = ("osu!", "osu!lazer", "osu-framework")
    if any(identifier in lower_ua for identifier in client_identifiers):
        info.browser = "osu!"
        info.is_client = True
        return info

    browser_patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"OPR/(\d+(?:\.\d+)*)"), "Opera"),
        (re.compile(r"Edg/(\d+(?:\.\d+)*)"), "Edge"),
        (re.compile(r"Chrome/(\d+(?:\.\d+)*)"), "Chrome"),
        (re.compile(r"Firefox/(\d+(?:\.\d+)*)"), "Firefox"),
        (re.compile(r"Version/(\d+(?:\.\d+)*).*Safari"), "Safari"),
        (re.compile(r"Safari/(\d+(?:\.\d+)*)"), "Safari"),
        (re.compile(r"MSIE (\d+(?:\.\d+)*)"), "Internet Explorer"),
        (re.compile(r"Trident/.*rv:(\d+(?:\.\d+)*)"), "Internet Explorer"),
    )

    for pattern, name in browser_patterns:
        match = pattern.search(ua)
        if match:
            info.browser = name
            info.version = match.group(1)
            break

    os_patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"windows nt 10"), "Windows 10"),
        (re.compile(r"windows nt 6\.3"), "Windows 8.1"),
        (re.compile(r"windows nt 6\.2"), "Windows 8"),
        (re.compile(r"windows nt 6\.1"), "Windows 7"),
        (re.compile(r"windows nt 6\.0"), "Windows Vista"),
        (re.compile(r"windows nt 5\.1"), "Windows XP"),
        (re.compile(r"mac os x"), "macOS"),
        (re.compile(r"iphone os"), "iOS"),
        (re.compile(r"ipad;"), "iPadOS"),
        (re.compile(r"android"), "Android"),
        (re.compile(r"linux"), "Linux"),
    )

    for pattern, name in os_patterns:
        if pattern.search(lower_ua):
            info.os = name
            break

    info.is_mobile = any(keyword in lower_ua for keyword in ("mobile", "iphone", "android", "ipod"))
    info.is_tablet = any(keyword in lower_ua for keyword in ("ipad", "tablet"))
    # Only classify as PC if not mobile or tablet
    if (
        not info.is_mobile
        and not info.is_tablet
        and any(keyword in lower_ua for keyword in ("windows", "macintosh", "linux", "x11"))
    ):
        info.is_pc = True

    if info.is_tablet:
        info.platform = "tablet"
    elif info.is_mobile:
        info.platform = "mobile"
    elif info.is_pc:
        info.platform = "pc"

    return info


# Reference: https://github.com/encode/starlette/blob/master/starlette/_utils.py
T = TypeVar("T")
AwaitableCallable = Callable[..., Awaitable[T]]


def is_async_callable(obj: Any) -> bool:
    """Check if an object is an async callable.

    Args:
        obj: The object to check.

    Returns:
        True if the object is async callable, False otherwise.
    """
    while isinstance(obj, functools.partial):
        obj = obj.func

    return inspect.iscoroutinefunction(obj)


P = ParamSpec("P")


async def run_in_threadpool(func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run a synchronous function in a thread pool.

    Args:
        func: The synchronous function to run.
        *args: Positional arguments for the function.
        **kwargs: Keyword arguments for the function.

    Returns:
        The result of the function.
    """
    func = functools.partial(func, *args, **kwargs)
    return await asyncio.get_event_loop().run_in_executor(None, func)


class BackgroundTasks:
    """A simple background task manager for fire-and-forget coroutines.

    Similar to FastAPI's BackgroundTasks but for use outside request handlers.
    """

    def __init__(self, tasks: Sequence[asyncio.Task] | None = None):
        """Initialize the task manager.

        Args:
            tasks: Optional sequence of existing tasks to manage.
        """
        self.tasks = set(tasks) if tasks else set()

    def add_task(self, func: Callable[P, Any], *args: P.args, **kwargs: P.kwargs) -> None:
        """Add a function to run as a background task.

        Args:
            func: The function to run (can be sync or async).
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
        """
        coro = func(*args, **kwargs) if is_async_callable(func) else run_in_threadpool(func, *args, **kwargs)
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def stop(self) -> None:
        """Cancel all running tasks and clear the task set."""
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()


bg_tasks = BackgroundTasks()


def utcnow() -> datetime:
    """Get the current UTC datetime.

    Returns:
        The current datetime with UTC timezone.
    """
    return datetime.now(tz=UTC)


def hex_to_hue(hex_color: str) -> int:
    """Convert a hex color string to a hue value (0-360).

    Args:
        hex_color: The hex color string (e.g. "#FF0000" or "FF0000").

    Returns:
        The hue value corresponding to the color.
    """
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        raise ValueError("Invalid hex color format. Expected format: RRGGBB")

    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0

    max_c = max(r, g, b)
    min_c = min(r, g, b)
    delta = max_c - min_c

    if delta == 0:
        return 0  # Achromatic (grey)

    if max_c == r:
        hue = (60 * ((g - b) / delta) + 360) % 360
    elif max_c == g:
        hue = (60 * ((b - r) / delta) + 120) % 360
    else:  # max_c == b
        hue = (60 * ((r - g) / delta) + 240) % 360

    return int(hue)


def safe_json_dumps(data) -> str:
    """Safely dump data to JSON string with FastAPI encoding.

    Args:
        data: The data to serialize.

    Returns:
        The JSON string.
    """
    return json.dumps(jsonable_encoder(data), ensure_ascii=False)


def type_is_optional(typ: type):
    """Check if a type annotation is Optional.

    Args:
        typ: The type to check.

    Returns:
        True if the type is Optional (Union[T, None]), False otherwise.
    """
    origin_type = get_origin(typ)
    args = get_args(typ)
    return (origin_type is UnionType or origin_type is Union) and len(args) == 2 and NoneType in args


def _get_type(typ: type, includes: tuple[str, ...]) -> Any:
    """Recursively process a type annotation for API documentation.

    Args:
        typ: The type to process.
        includes: Tuple of field names to include for DatabaseModel types.

    Returns:
        The processed type annotation.
    """
    from app.database._base import DatabaseModel

    origin = get_origin(typ)
    if origin is list:
        item_type = typ.__args__[0]
        return list[_get_type(item_type, includes)]  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
    elif origin is dict:
        key_type, value_type = typ.__args__
        return dict[key_type, _get_type(value_type, includes)]  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
    elif type_is_optional(typ):
        inner_type = next(arg for arg in get_args(typ) if arg is not NoneType)
        return _get_type(inner_type, includes) | None  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
    elif origin is UnionType or origin is Union:
        new_types = []
        for arg in get_args(typ):
            new_types.append(_get_type(arg, includes))  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
        return Union[tuple(new_types)]  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]  # noqa: UP007
    elif issubclass(typ, DatabaseModel):
        return typ.generate_typeddict(includes)
    else:
        return typ


def api_doc(desc: str, model: Any, includes: list[str] = [], *, name: str = "APIDict"):
    """Generate API documentation metadata.

    Args:
        desc: The description text.
        model: The model type or dict of field types.
        includes: List of additional fields to include.
        name: The TypedDict name.

    Returns:
        A dict with 'description' and 'model' keys for OpenAPI docs.

    Example:

    ```python
    from app.utils import api_doc

    @router.get("/data/{data_id}",
        responses={
            200: api_doc(
                desc="Data response with optional secret info.",
                model=DataModel,
                includes=["secret_info"],
                name="DataResponse",
            )
        }
    )
    async def get_data(data_id: int, db: Database):
        data = await db.get(Data, data_id)
        return Data.transform(data, includes=["secret_info"])
    ```
    """
    if includes:
        includes_str = ", ".join(f"`{inc}`" for inc in includes)
        desc += f"\n\nIncludes: {includes_str}"
    if isinstance(model, dict):
        fields = {}
        for k, v in model.items():
            fields[k] = _get_type(v, tuple(includes))
        typed_dict = TypedDict(name, fields)  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
    else:
        typed_dict = _get_type(model, tuple(includes))
    return {"description": desc, "model": typed_dict}
