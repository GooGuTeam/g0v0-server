"""HTTP and API documentation utilities.

This module provides functions for user agent parsing, image validation,
and API documentation generation.
"""

from io import BytesIO
import re
from types import NoneType, UnionType
from typing import TYPE_CHECKING, Any, TypedDict, Union, get_args, get_origin

from .type import type_is_optional

from PIL import Image

if TYPE_CHECKING:
    from app.models.model import UserAgentInfo


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

    if len(content) > size:
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
    from app.helpers import api_doc

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
