import re
from typing import Annotated, Literal
from urllib.parse import parse_qs

from app.database import (
    Beatmap,
    Beatmapset,
    BeatmapsetModel,
    FavouriteBeatmapset,
    SearchBeatmapsetsResp,
    User,
)
from app.dependencies.beatmap_download import DownloadService
from app.dependencies.cache import BeatmapsetCacheService, UserCacheService
from app.dependencies.database import Database, Redis
from app.dependencies.fetcher import Fetcher
from app.dependencies.geoip import IPAddress, get_geoip_helper
from app.dependencies.user import ClientUser, get_current_user
from app.helpers.asset_proxy_helper import asset_proxy_response
from app.models.beatmap import SearchQueryModel
from app.models.error import ErrorType, RequestError
from app.service.beatmapset_cache_service import generate_hash
from app.utils import api_doc

from .router import router

from fastapi import (
    BackgroundTasks,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    Security,
)
from fastapi.responses import RedirectResponse
from httpx import HTTPError
from sqlmodel import select


@router.get(
    "/beatmapsets/search",
    name="搜索谱面集",
    tags=["谱面集"],
    response_model=SearchBeatmapsetsResp,
)
@asset_proxy_response
async def search_beatmapset(
    query: Annotated[SearchQueryModel, Query()],
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
    redis: Redis,
    cache_service: BeatmapsetCacheService,
):
    params = parse_qs(qs=request.url.query, keep_blank_values=True)
    cursor = {}

    # 解析 cursor[field] 格式的参数
    for k, v in params.items():
        match = re.match(r"cursor\[(\w+)\]", k)
        if match:
            field_name = match.group(1)
            field_value = v[0] if v else None
            if field_value is not None:
                # 转换为适当的类型
                try:
                    if field_name in ["approved_date", "id"]:
                        cursor[field_name] = int(field_value)
                    else:
                        # 尝试转换为数字类型
                        try:
                            # 首先尝试转换为整数
                            cursor[field_name] = int(field_value)
                        except ValueError:
                            try:
                                # 然后尝试转换为浮点数
                                cursor[field_name] = float(field_value)
                            except ValueError:
                                # 最后保持字符串
                                cursor[field_name] = field_value
                except ValueError:
                    cursor[field_name] = field_value

    if (
        "recommended" in query.c
        or len(query.r) > 0
        or query.played
        or "follows" in query.c
        or "mine" in query.s
        or "favourites" in query.s
    ):
        # TODO: search locally
        return SearchBeatmapsetsResp(total=0, beatmapsets=[])

    # 生成查询和游标的哈希用于缓存
    query_hash = generate_hash(query.model_dump())
    cursor_hash = generate_hash(cursor)

    # 尝试从缓存获取搜索结果
    cached_result = await cache_service.get_search_from_cache(query_hash, cursor_hash)
    if cached_result:
        sets = SearchBeatmapsetsResp(**cached_result)
        return sets

    try:
        sets = await fetcher.search_beatmapset(query, cursor, redis)

        # 缓存搜索结果
        await cache_service.cache_search_result(query_hash, cursor_hash, sets.model_dump())
        return sets
    except HTTPError as e:
        raise RequestError(ErrorType.INTERNAL, {"message": str(e)}) from e


@router.get(
    "/beatmapsets/lookup",
    tags=["谱面集"],
    responses={200: api_doc("谱面集详细信息", BeatmapsetModel, BeatmapsetModel.BEATMAPSET_TRANSFORMER_INCLUDES)},
    name="查询谱面集 (通过谱面 ID)",
    description=("通过谱面 ID 查询所属谱面集。"),
)
@asset_proxy_response
async def lookup_beatmapset(
    db: Database,
    request: Request,
    beatmap_id: Annotated[int, Query(description="谱面 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
    cache_service: BeatmapsetCacheService,
):
    # 先尝试从缓存获取
    cached_resp = await cache_service.get_beatmap_lookup_from_cache(beatmap_id)
    if cached_resp:
        return cached_resp

    try:
        beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=beatmap_id)

        resp = await BeatmapsetModel.transform(
            beatmap.beatmapset, user=current_user, includes=BeatmapsetModel.API_INCLUDES
        )

        # 缓存结果
        await cache_service.cache_beatmap_lookup(beatmap_id, resp)
        return resp
    except HTTPError as exc:
        raise RequestError(ErrorType.BEATMAP_NOT_FOUND) from exc


@router.get(
    "/beatmapsets/{beatmapset_id}",
    tags=["谱面集"],
    responses={200: api_doc("谱面集详细信息", BeatmapsetModel, BeatmapsetModel.BEATMAPSET_TRANSFORMER_INCLUDES)},
    name="获取谱面集详情",
    description="获取单个谱面集详情。",
)
@asset_proxy_response
async def get_beatmapset(
    db: Database,
    request: Request,
    beatmapset_id: Annotated[int, Path(..., description="谱面集 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
    cache_service: BeatmapsetCacheService,
):
    # 先尝试从缓存获取
    cached_resp = await cache_service.get_beatmapset_from_cache(beatmapset_id)
    if cached_resp:
        return cached_resp

    try:
        beatmapset = await Beatmapset.get_or_fetch(db, fetcher, beatmapset_id)
        await db.refresh(current_user)
        resp = await BeatmapsetModel.transform(beatmapset, includes=BeatmapsetModel.API_INCLUDES, user=current_user)

        # 缓存结果
        await cache_service.cache_beatmapset(resp)
        return resp
    except HTTPError as exc:
        raise RequestError(ErrorType.BEATMAPSET_NOT_FOUND) from exc


@router.get(
    "/beatmapsets/{beatmapset_id}/download",
    tags=["谱面集"],
    name="下载谱面集",
    description="\n下载谱面集文件。基于请求IP地理位置智能分流，支持负载均衡和自动故障转移。中国IP使用Sayobot镜像，其他地区使用Nerinyan和OsuDirect镜像。",
)
async def download_beatmapset(
    client_ip: IPAddress,
    beatmapset_id: Annotated[int, Path(..., description="谱面集 ID")],
    current_user: ClientUser,
    download_service: DownloadService,
    no_video: Annotated[bool, Query(alias="noVideo", description="是否下载无视频版本")] = True,
):
    geoip_helper = get_geoip_helper()
    geo_info = geoip_helper.lookup(client_ip)
    country_code = geo_info.get("country_iso", "")

    # 优先使用IP地理位置判断，如果获取失败则回退到用户账户的国家代码
    is_china = country_code == "CN" or (not country_code and current_user.country_code == "CN")

    try:
        # 使用负载均衡服务获取下载URL
        download_url = download_service.get_download_url(
            beatmapset_id=beatmapset_id, no_video=no_video, is_china=is_china
        )
        return RedirectResponse(download_url)
    except HTTPException:
        # 如果负载均衡服务失败，回退到原有逻辑
        if is_china:
            return RedirectResponse(
                f"https://dl.sayobot.cn/beatmaps/download/{'novideo' if no_video else 'full'}/{beatmapset_id}"
            )
        else:
            return RedirectResponse(f"https://catboy.best/d/{beatmapset_id}{'n' if no_video else ''}")


@router.post(
    "/beatmapsets/{beatmapset_id}/favourites",
    tags=["谱面集"],
    name="收藏或取消收藏谱面集",
    description="\n收藏或取消收藏指定谱面集。",
)
async def favourite_beatmapset(
    db: Database,
    cache_service: UserCacheService,
    beatmapset_id: Annotated[int, Path(..., description="谱面集 ID")],
    action: Annotated[
        Literal["favourite", "unfavourite"],
        Form(description="操作类型：favourite 收藏 / unfavourite 取消收藏"),
    ],
    current_user: ClientUser,
):
    existing_favourite = (
        await db.exec(
            select(FavouriteBeatmapset).where(
                FavouriteBeatmapset.user_id == current_user.id,
                FavouriteBeatmapset.beatmapset_id == beatmapset_id,
            )
        )
    ).first()

    if (action == "favourite" and existing_favourite) or (action == "unfavourite" and not existing_favourite):
        return

    if action == "favourite":
        favourite = FavouriteBeatmapset(user_id=current_user.id, beatmapset_id=beatmapset_id)
        db.add(favourite)
    else:
        await db.delete(existing_favourite)
    await cache_service.invalidate_user_beatmapsets_cache(current_user.id)
    await db.commit()
