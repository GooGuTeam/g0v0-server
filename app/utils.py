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

from app.models.error import ErrorType, RequestError

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
    """Convert a snake_case string to camelCase."""
    if not name:
        return name

    parts = name.split("_")
    if not parts:
        return name

    # 常见缩写词列表
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
    """Convert a snake_case string to PascalCase."""
    if not name:
        return name

    parts = name.split("_")
    if not parts:
        return name

    # 常见缩写词列表
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
    y1, w1, _ = dt1.isocalendar()
    y2, w2, _ = dt2.isocalendar()

    # 按 (年, 周) 排序，保证 dt1 <= dt2
    if (y1, w1) > (y2, w2):
        y1, w1, y2, w2 = y2, w2, y1, w1

    # 同一年，周数相邻
    if y1 == y2 and w2 - w1 == 1:
        return True

    # 跨年，判断 y2 是否是下一年，且 w2 == 1，并且 w1 是 y1 的最后一周
    if y2 == y1 + 1 and w2 == 1:
        # 判断 y1 的最后一周是多少
        last_week_y1 = datetime(y1, 12, 28).isocalendar()[1]  # 12-28 保证在最后一周
        if w1 == last_week_y1:
            return True

    return False


def are_same_weeks(dt1: datetime, dt2: datetime) -> bool:
    return dt1.isocalendar()[:2] == dt2.isocalendar()[:2]


def truncate(text: str, limit: int = 100, ellipsis: str = "...") -> str:
    if len(text) > limit:
        return text[:limit] + ellipsis
    return text


def check_image(content: bytes, size: int, width: int, height: int) -> str:
    if len(content) > size:  # 10MB limit
        raise RequestError(ErrorType.FILE_SIZE_EXCEEDS_LIMIT)
    elif len(content) == 0:
        raise RequestError(ErrorType.FILE_EMPTY)
    try:
        with Image.open(BytesIO(content)) as img:
            if img.format not in ["PNG", "JPEG", "GIF"]:
                raise RequestError(ErrorType.INVALID_IMAGE_FORMAT)
            if img.size[0] > width or img.size[1] > height:
                raise RequestError(
                    ErrorType.IMAGE_DIMENSIONS_EXCEED_LIMIT,
                    {"limit_width": width, "limit_height": height},
                )
            return img.format.lower()
    except Exception as e:
        raise RequestError(ErrorType.ERROR_PROCESSING_IMAGE, {"reason": str(e)})


def extract_user_agent(user_agent: str | None) -> "UserAgentInfo":
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


# https://github.com/encode/starlette/blob/master/starlette/_utils.py
T = TypeVar("T")
AwaitableCallable = Callable[..., Awaitable[T]]


def is_async_callable(obj: Any) -> bool:
    while isinstance(obj, functools.partial):
        obj = obj.func

    return inspect.iscoroutinefunction(obj)


P = ParamSpec("P")


async def run_in_threadpool(func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    func = functools.partial(func, *args, **kwargs)
    return await asyncio.get_event_loop().run_in_executor(None, func)


class BackgroundTasks:
    def __init__(self, tasks: Sequence[asyncio.Task] | None = None):
        self.tasks = set(tasks) if tasks else set()

    def add_task(self, func: Callable[P, Any], *args: P.args, **kwargs: P.kwargs) -> None:
        coro = func(*args, **kwargs) if is_async_callable(func) else run_in_threadpool(func, *args, **kwargs)
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()


bg_tasks = BackgroundTasks()


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def hex_to_hue(hex_color: str) -> int:
    """Convert a hex color string to a hue value (0-360)."""
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
    return json.dumps(jsonable_encoder(data), ensure_ascii=False)


def type_is_optional(typ: type):
    origin_type = get_origin(typ)
    args = get_args(typ)
    return (origin_type is UnionType or origin_type is Union) and len(args) == 2 and NoneType in args


def _get_type(typ: type, includes: tuple[str, ...]) -> Any:
    from app.database._base import DatabaseModel

    origin = get_origin(typ)
    if issubclass(typ, DatabaseModel):
        return typ.generate_typeddict(includes)
    elif origin is list:
        item_type = typ.__args__[0]
        return list[_get_type(item_type, includes)]  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
    elif origin is dict:
        key_type, value_type = typ.__args__
        return dict[key_type, _get_type(value_type, includes)]  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
    elif type_is_optional(typ):
        inner_type = next(arg for arg in get_args(typ) if arg is not NoneType)
        return Union[_get_type(inner_type, includes), None]  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]  # noqa: UP007
    elif origin is UnionType or origin is Union:
        new_types = []
        for arg in get_args(typ):
            new_types.append(_get_type(arg, includes))  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
        return Union[tuple(new_types)]  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]  # noqa: UP007
    else:
        return typ


def api_doc(desc: str, model: Any, includes: list[str] = [], *, name: str = "APIDict"):
    if includes:
        includes_str = ", ".join(f"`{inc}`" for inc in includes)
        desc += f"\n\n包含：{includes_str}"
    if isinstance(model, dict):
        fields = {}
        for k, v in model.items():
            fields[k] = _get_type(v, tuple(includes))
        typed_dict = TypedDict(name, fields)  # pyright: ignore[reportArgumentType, reportGeneralTypeIssues]
    else:
        typed_dict = _get_type(model, tuple(includes))
    return {"description": desc, "model": typed_dict}
