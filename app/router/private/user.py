from typing import Annotated, Any

from app.auth import validate_username
from app.config import settings
from app.database import User
from app.database.events import Event, EventType
from app.database.user_preference import (
    DEFAULT_ORDER,
    BeatmapCardSize,
    BeatmapDownload,
    ScoringMode,
    UserListFilter,
    UserListSort,
    UserListView,
    UserPreference,
)
from app.dependencies.cache import UserCacheService
from app.dependencies.database import Database
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError
from app.models.score import GameMode
from app.models.user import Page
from app.models.userpage import (
    UpdateUserpageRequest,
    UpdateUserpageResponse,
    UserpageError,
    ValidateBBCodeRequest,
    ValidateBBCodeResponse,
)
from app.service.bbcode_service import bbcode_service
from app.utils import hex_to_hue, utcnow

from .router import router

from fastapi import Body
from pydantic import BaseModel, Field
from sqlmodel import exists, select


@router.post("/rename", name="修改用户名", tags=["用户", "g0v0 API"])
async def user_rename(
    session: Database,
    new_name: Annotated[str, Body(..., description="新的用户名")],
    current_user: ClientUser,
    cache_service: UserCacheService,
):
    """修改用户名

    为指定用户修改用户名，并将原用户名添加到历史用户名列表中

    错误情况:
    - 404: 找不到指定用户
    - 409: 新用户名已被占用

    返回:
    - 成功: None
    """
    if await current_user.is_restricted(session):
        # https://github.com/ppy/osu-web/blob/cae2fdf03cfb8c30c8e332cfb142e03188ceffef/app/Libraries/ChangeUsername.php#L48-L49
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    samename_user = (await session.exec(select(exists()).where(User.username == new_name))).first()
    if samename_user:
        raise RequestError(ErrorType.USERNAME_EXISTS)
    errors = validate_username(new_name)
    if errors:
        raise RequestError(ErrorType.INVALID_REQUEST, {"errors": errors}, status_code=403)
    previous_username = []
    previous_username.extend(current_user.previous_usernames)
    previous_username.append(current_user.username)
    current_user.username = new_name
    current_user.previous_usernames = previous_username
    rename_event = Event(
        created_at=utcnow(),
        type=EventType.USERNAME_CHANGE,
        user_id=current_user.id,
        user=current_user,
    )
    rename_event.event_payload["user"] = {
        "username": new_name,
        "url": settings.web_url + "users/" + str(current_user.id),
        "previous_username": current_user.previous_usernames[-1],
    }
    session.add(rename_event)
    await cache_service.invalidate_user_cache(current_user.id)
    await session.commit()

    return None


@router.put(
    "/user/page",
    response_model=UpdateUserpageResponse,
    name="更新用户页面",
    description="更新指定用户的个人页面内容（支持BBCode）。匹配官方osu-web API格式。",
    tags=["用户", "g0v0 API"],
)
async def update_userpage(
    request: UpdateUserpageRequest,
    session: Database,
    current_user: ClientUser,
    cache_service: UserCacheService,
):
    """更新用户页面内容"""
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    try:
        errors = bbcode_service.validate_bbcode(request.body)
        if errors:
            msg = "Invalid BBCode content: " + "; ".join(errors)
            raise UserpageError(msg)

        # 处理BBCode内容
        processed_page = bbcode_service.process_userpage_content(request.body)

        # 更新数据库 - 直接更新用户对象
        current_user.page = Page(html=processed_page["html"], raw=processed_page["raw"])
        session.add(current_user)
        await session.commit()
        await session.refresh(current_user)

        await cache_service.invalidate_user_cache(current_user.id)

        # 返回官方格式的响应：只包含html
        return UpdateUserpageResponse(html=processed_page["html"])

    except UserpageError as e:
        # 使用官方格式的错误响应：{'error': message}
        raise RequestError(ErrorType.INVALID_REQUEST, {"error": e.message})
    except Exception:
        raise RequestError(ErrorType.INTERNAL, {"error": "Failed to update user page"})


@router.post(
    "/user/validate-bbcode",
    response_model=ValidateBBCodeResponse,
    name="验证BBCode",
    description="验证BBCode语法并返回预览。",
    tags=["用户", "g0v0 API"],
)
async def validate_bbcode(
    request: ValidateBBCodeRequest,
):
    """验证BBCode语法"""
    try:
        # 验证BBCode语法
        errors = bbcode_service.validate_bbcode(request.content)

        # 生成预览（如果没有严重错误）
        if len(errors) == 0:
            preview = bbcode_service.process_userpage_content(request.content)
        else:
            preview = {"raw": request.content, "html": ""}

        return ValidateBBCodeResponse(valid=len(errors) == 0, errors=errors, preview=preview)

    except UserpageError as e:
        return ValidateBBCodeResponse(valid=False, errors=[e.message], preview={"raw": request.content, "html": ""})
    except Exception:
        raise RequestError(ErrorType.INTERNAL, {"error": "Failed to validate BBCode"})


class Preferences(BaseModel):
    theme: str | None = None
    language: str | None = None

    audio_autoplay: bool | None = None
    audio_muted: bool | None = None
    audio_volume: float | None = Field(None, ge=0.0, le=1.0)
    beatmapset_card_size: BeatmapCardSize | None = None
    beatmap_download: BeatmapDownload | None = None
    beatmapset_show_nsfw: bool | None = None
    profile_order: list[str] | None = None
    legacy_score_only: bool | None = None
    profile_cover_expanded: bool | None = None
    scoring_mode: ScoringMode | None = None
    user_list_filter: UserListFilter | None = None
    user_list_sort: UserListSort | None = None
    user_list_view: UserListView | None = None
    extra: dict[str, Any] | None = None

    # in User
    playmode: GameMode | None = None
    interests: str | None = None
    location: str | None = None
    occupation: str | None = None
    twitter: str | None = None
    website: str | None = None
    discord: str | None = None
    profile_colour: str | None = None

    @staticmethod
    async def clear(current_user: User, fields: list[str]):
        await current_user.awaitable_attrs.user_preference
        user_pref: UserPreference | None = current_user.user_preference
        if user_pref is None:
            return
        if len(fields) == 0:
            fields = [
                *PREFERENCE_FIELDS,
                *USER_PROFILE_FIELDS_WITH_WEBSITE,
                "profile_order",
                "extra",
                "playmode",
                "profile_colour",
            ]

        for field in fields:
            if field in PREFERENCE_FIELDS:
                setattr(user_pref, field, UserPreference.model_fields[field].default)
            elif field == "profile_order":
                user_pref.extras_order = DEFAULT_ORDER
            elif field == "extra":
                user_pref.extra = {}

        for field in fields:
            if field in USER_PROFILE_FIELDS_WITH_WEBSITE:
                setattr(current_user, field, None)
            elif field == "playmode":
                current_user.playmode = GameMode.OSU
                current_user.g0v0_playmode = GameMode.OSU
            elif field == "profile_colour":
                current_user.profile_colour = None
                current_user.profile_hue = None


PREFERENCE_FIELDS = {
    "theme",
    "language",
    "audio_autoplay",
    "audio_muted",
    "audio_volume",
    "beatmapset_card_size",
    "beatmap_download",
    "beatmapset_show_nsfw",
    "legacy_score_only",
    "profile_cover_expanded",
    "scoring_mode",
    "user_list_filter",
    "user_list_sort",
    "user_list_view",
}

USER_PROFILE_FIELDS = {
    "interests",
    "location",
    "occupation",
    "twitter",
    "discord",
}

USER_PROFILE_FIELDS_WITH_WEBSITE = USER_PROFILE_FIELDS | {"website"}


@router.get(
    "/user/preferences",
    name="获取用户偏好设置",
    description="获取当前登录用户的偏好设置",
    tags=["用户", "g0v0 API"],
    response_model=Preferences,
)
async def get_user_preference(
    current_user: ClientUser,
):
    await current_user.awaitable_attrs.user_preference
    user_pref: UserPreference | None = current_user.user_preference
    if user_pref is None:
        user_pref = UserPreference(user_id=current_user.id)

    return Preferences(
        theme=user_pref.theme,
        language=user_pref.language,
        audio_autoplay=user_pref.audio_autoplay,
        audio_muted=user_pref.audio_muted,
        audio_volume=user_pref.audio_volume,
        beatmapset_card_size=user_pref.beatmapset_card_size,
        beatmap_download=user_pref.beatmap_download,
        beatmapset_show_nsfw=user_pref.beatmapset_show_nsfw,
        profile_order=user_pref.extras_order or DEFAULT_ORDER,
        legacy_score_only=user_pref.legacy_score_only,
        profile_cover_expanded=user_pref.profile_cover_expanded,
        scoring_mode=user_pref.scoring_mode,
        user_list_filter=user_pref.user_list_filter,
        user_list_sort=user_pref.user_list_sort,
        user_list_view=user_pref.user_list_view,
        extra=user_pref.extra or {},
        playmode=current_user.g0v0_playmode,
        interests=current_user.interests,
        location=current_user.location,
        occupation=current_user.occupation,
        twitter=current_user.twitter,
        website=current_user.website,
        discord=current_user.discord,
        profile_colour="#" + current_user.profile_colour if current_user.profile_colour else None,
    )


@router.patch(
    "/user/preferences",
    name="修改用户偏好设置",
    description="修改当前登录用户的偏好设置",
    tags=["用户", "g0v0 API"],
    status_code=204,
)
async def change_user_preference(
    request: Preferences,
    session: Database,
    current_user: ClientUser,
    cache_service: UserCacheService,
):
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    await current_user.awaitable_attrs.user_preference
    user_pref: UserPreference | None = current_user.user_preference
    if user_pref is None:
        user_pref = UserPreference(user_id=current_user.id)
        session.add(user_pref)

    for field, value in request.model_dump(include=PREFERENCE_FIELDS, exclude_none=True).items():
        setattr(user_pref, field, value)

    if request.profile_order is not None:
        if set(request.profile_order) != set(DEFAULT_ORDER):
            raise RequestError(ErrorType.INVALID_PROFILE_ORDER)
        user_pref.extras_order = request.profile_order

    if request.extra is not None:
        user_pref.extra = (user_pref.extra or {}) | request.extra

    if request.playmode is not None:
        if request.playmode.is_official():
            current_user.playmode = request.playmode.to_base_ruleset()
        current_user.g0v0_playmode = request.playmode

    for field, value in request.model_dump(include=USER_PROFILE_FIELDS, exclude_none=True).items():
        setattr(current_user, field, value or None)

    if request.website is not None:
        if request.website == "":
            current_user.website = None
        elif not (request.website.startswith("http://") or request.website.startswith("https://")):
            current_user.website = "https://" + request.website

    if request.profile_colour is not None:
        current_user.profile_colour = request.profile_colour.removeprefix("#")
        try:
            current_user.profile_hue = hex_to_hue(request.profile_colour)
        except ValueError:
            raise RequestError(ErrorType.INVALID_PROFILE_COLOUR_HEX)

    await cache_service.invalidate_user_cache(current_user.id)
    await session.commit()


@router.put(
    "/user/preferences",
    name="覆盖用户偏好设置",
    description="使用提供的数据完整覆盖当前登录用户的偏好设置，未提供的字段将被重置为默认值。",
    tags=["用户", "g0v0 API"],
    status_code=204,
)
async def overwrite_user_preference(
    request: Preferences,
    session: Database,
    current_user: ClientUser,
    cache_service: UserCacheService,
):
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    await Preferences.clear(current_user, [])
    await change_user_preference(request, session, current_user, cache_service)

    await cache_service.invalidate_user_cache(current_user.id)
    await session.commit()


@router.delete(
    "/user/preferences",
    name="删除用户偏好设置",
    description="删除当前登录用户的偏好设置，恢复为默认值\n\n如果未指定字段，则删除所有可删除的偏好设置",
    tags=["用户", "g0v0 API"],
    status_code=204,
)
async def delete_user_preference(
    session: Database,
    current_user: ClientUser,
    fields: list[str],
    cache_service: UserCacheService,
):
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    await Preferences.clear(current_user, fields)

    await cache_service.invalidate_user_cache(current_user.id)
    await session.commit()
