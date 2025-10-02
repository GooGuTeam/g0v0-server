from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import parse_qs

from app.database import Beatmap, Beatmapset, BeatmapsetResp, FavouriteBeatmapset, User
from app.database.beatmapset import SearchBeatmapsetsResp
from app.dependencies.beatmap_download import get_beatmap_download_service
from app.dependencies.beatmapset_cache import get_beatmapset_cache_dependency
from app.dependencies.database import Database, get_redis, with_db
from app.dependencies.fetcher import get_fetcher
from app.dependencies.geoip import get_client_ip, get_geoip_helper
from app.dependencies.user import get_client_user, get_current_user
from app.fetcher import Fetcher
from app.models.beatmap import SearchQueryModel
from app.service.asset_proxy_helper import process_response_assets
from app.service.beatmap_download_service import BeatmapDownloadService
from app.service.beatmapset_cache_service import BeatmapsetCacheService, generate_hash

from .router import router

from fastapi import (
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    Security,
)
from fastapi.responses import RedirectResponse
from httpx import HTTPError
from sqlmodel import exists, select


async def _save_to_db(sets: SearchBeatmapsetsResp):
    async with with_db() as session:
        for s in sets.beatmapsets:
            if not (await session.exec(select(exists()).where(Beatmapset.id == s.id))).first():
                await Beatmapset.from_resp(session, s)


@router.get(
    "/beatmapsets/search",
    name="搜索谱面集",
    tags=["谱面集"],
    response_model=SearchBeatmapsetsResp,
)
async def search_beatmapset(
    db: Database,
    query: Annotated[SearchQueryModel, Query(...)],
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Security(get_current_user, scopes=["public"]),
    fetcher: Fetcher = Depends(get_fetcher),
    redis=Depends(get_redis),
    cache_service: BeatmapsetCacheService = Depends(get_beatmapset_cache_dependency),
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
        # 处理资源代理
        processed_sets = await process_response_assets(sets, request)
        return processed_sets

    try:
        sets = await fetcher.search_beatmapset(query, cursor, redis)
        background_tasks.add_task(_save_to_db, sets)

        # 缓存搜索结果
        await cache_service.cache_search_result(query_hash, cursor_hash, sets.model_dump())

        # 处理资源代理
        processed_sets = await process_response_assets(sets, request)
        return processed_sets
    except HTTPError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/beatmapsets/lookup",
    tags=["谱面集"],
    name="查询谱面集 (通过谱面 ID)",
    response_model=BeatmapsetResp,
    description=("通过谱面 ID 查询所属谱面集。"),
)
async def lookup_beatmapset(
    db: Database,
    request: Request,
    beatmap_id: int = Query(description="谱面 ID"),
    current_user: User = Security(get_current_user, scopes=["public"]),
    fetcher: Fetcher = Depends(get_fetcher),
    cache_service: BeatmapsetCacheService = Depends(get_beatmapset_cache_dependency),
):
    # 先尝试从缓存获取
    cached_resp = await cache_service.get_beatmap_lookup_from_cache(beatmap_id)
    if cached_resp:
        # 处理资源代理
        processed_resp = await process_response_assets(cached_resp, request)
        return processed_resp

    try:
        beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=beatmap_id)
        resp = await BeatmapsetResp.from_db(beatmap.beatmapset, session=db, user=current_user)

        # 缓存结果
        await cache_service.cache_beatmap_lookup(beatmap_id, resp)

        # 处理资源代理
        processed_resp = await process_response_assets(resp, request)
        return processed_resp
    except HTTPError as exc:
        raise HTTPException(status_code=404, detail="Beatmap not found") from exc


@router.get(
    "/beatmapsets/{beatmapset_id}",
    tags=["谱面集"],
    name="获取谱面集详情",
    response_model=BeatmapsetResp,
    description="获取单个谱面集详情。",
)
async def get_beatmapset(
    db: Database,
    request: Request,
    beatmapset_id: int = Path(..., description="谱面集 ID"),
    current_user: User = Security(get_current_user, scopes=["public"]),
    fetcher: Fetcher = Depends(get_fetcher),
    cache_service: BeatmapsetCacheService = Depends(get_beatmapset_cache_dependency),
):
    # 先尝试从缓存获取
    cached_resp = await cache_service.get_beatmapset_from_cache(beatmapset_id)
    if cached_resp:
        # 处理资源代理
        processed_resp = await process_response_assets(cached_resp, request)
        return processed_resp

    try:
        beatmapset = await Beatmapset.get_or_fetch(db, fetcher, beatmapset_id)
        resp = await BeatmapsetResp.from_db(beatmapset, session=db, include=["recent_favourites"], user=current_user)

        # 缓存结果
        await cache_service.cache_beatmapset(resp)

        # 处理资源代理
        processed_resp = await process_response_assets(resp, request)
        return processed_resp
    except HTTPError as exc:
        raise HTTPException(status_code=404, detail="Beatmapset not found") from exc


@router.get(
    "/beatmapsets/{beatmapset_id}/download",
    tags=["谱面集"],
    name="下载谱面集",
    description="\n下载谱面集文件。基于请求IP地理位置智能分流，支持负载均衡和自动故障转移。中国IP使用Sayobot镜像，其他地区使用Nerinyan和OsuDirect镜像。",
)
async def download_beatmapset(
    request: Request,
    beatmapset_id: int = Path(..., description="谱面集 ID"),
    no_video: bool = Query(True, alias="noVideo", description="是否下载无视频版本"),
    current_user: User = Security(get_client_user),
    download_service: BeatmapDownloadService = Depends(get_beatmap_download_service),
):
    client_ip = get_client_ip(request)

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
    beatmapset_id: int = Path(..., description="谱面集 ID"),
    action: Literal["favourite", "unfavourite"] = Form(description="操作类型：favourite 收藏 / unfavourite 取消收藏"),
    current_user: User = Security(get_client_user),
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
    await db.commit()
