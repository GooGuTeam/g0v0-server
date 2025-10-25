import asyncio
import hashlib
import json
from typing import Annotated

from app.calculators.performance import ConvertError
from app.database import Beatmap, BeatmapResp, User
from app.database.beatmap import calculate_beatmap_attributes
from app.dependencies.database import Database, Redis
from app.dependencies.fetcher import Fetcher
from app.dependencies.user import get_current_user
from app.helpers.asset_proxy_helper import asset_proxy_response
from app.models.mods import APIMod, int_to_mods
from app.models.performance import (
    DifficultyAttributes,
    DifficultyAttributesUnion,
)
from app.models.score import (
    GameMode,
)

from .router import router

from fastapi import HTTPException, Path, Query, Security
from httpx import HTTPError, HTTPStatusError
from pydantic import BaseModel
from sqlmodel import col, select


class BatchGetResp(BaseModel):
    """批量获取谱面返回模型。

    返回字段说明:
    - beatmaps: 谱面详细信息列表。"""

    beatmaps: list[BeatmapResp]


@router.get(
    "/beatmaps/lookup",
    tags=["谱面"],
    name="查询单个谱面",
    response_model=BeatmapResp,
    description=("根据谱面 ID / MD5 / 文件名 查询单个谱面。至少提供 id / checksum / filename 之一。"),
)
@asset_proxy_response
async def lookup_beatmap(
    db: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
    id: Annotated[int | None, Query(alias="id", description="谱面 ID")] = None,
    md5: Annotated[str | None, Query(alias="checksum", description="谱面文件 MD5")] = None,
    filename: Annotated[str | None, Query(alias="filename", description="谱面文件名")] = None,
):
    if id is None and md5 is None and filename is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'id', 'checksum', or 'filename' must be provided.",
        )
    try:
        beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=id, md5=md5)
    except HTTPError:
        raise HTTPException(status_code=404, detail="Beatmap not found")

    if beatmap is None:
        raise HTTPException(status_code=404, detail="Beatmap not found")
    await db.refresh(current_user)

    return await BeatmapResp.from_db(beatmap, session=db, user=current_user)


@router.get(
    "/beatmaps/{beatmap_id}",
    tags=["谱面"],
    name="获取谱面详情",
    response_model=BeatmapResp,
    description="获取单个谱面详情。",
)
@asset_proxy_response
async def get_beatmap(
    db: Database,
    beatmap_id: Annotated[int, Path(..., description="谱面 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
):
    try:
        beatmap = await Beatmap.get_or_fetch(db, fetcher, beatmap_id)
        return await BeatmapResp.from_db(beatmap, session=db, user=current_user)
    except HTTPError:
        raise HTTPException(status_code=404, detail="Beatmap not found")


@router.get(
    "/beatmaps/",
    tags=["谱面"],
    name="批量获取谱面",
    response_model=BatchGetResp,
    description=("批量获取谱面。若不提供 ids[]，按最近更新时间返回最多 50 条。为空时按最近更新时间返回。"),
)
@asset_proxy_response
async def batch_get_beatmaps(
    db: Database,
    beatmap_ids: Annotated[
        list[int],
        Query(alias="ids[]", default_factory=list, description="谱面 ID 列表 （最多 50 个）"),
    ],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
):
    if not beatmap_ids:
        beatmaps = (await db.exec(select(Beatmap).order_by(col(Beatmap.last_updated).desc()).limit(50))).all()
    else:
        beatmaps = list((await db.exec(select(Beatmap).where(col(Beatmap.id).in_(beatmap_ids)).limit(50))).all())
        not_found_beatmaps = [bid for bid in beatmap_ids if bid not in [bm.id for bm in beatmaps]]
        beatmaps.extend(
            beatmap
            for beatmap in await asyncio.gather(
                *[Beatmap.get_or_fetch(db, fetcher, bid=bid) for bid in not_found_beatmaps],
                return_exceptions=True,
            )
            if isinstance(beatmap, Beatmap)
        )
        for beatmap in beatmaps:
            await db.refresh(beatmap)
    await db.refresh(current_user)
    return BatchGetResp(beatmaps=[await BeatmapResp.from_db(bm, session=db, user=current_user) for bm in beatmaps])


@router.post(
    "/beatmaps/{beatmap_id}/attributes",
    tags=["谱面"],
    name="计算谱面属性",
    response_model=DifficultyAttributesUnion,
    description=("计算谱面指定 mods / ruleset 下谱面的难度属性 (难度/PP 相关属性)。"),
)
async def get_beatmap_attributes(
    db: Database,
    beatmap_id: Annotated[int, Path(..., description="谱面 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    mods: Annotated[
        list[str],
        Query(
            default_factory=list,
            description="Mods 列表；可为整型位掩码(单元素)或 JSON/简称",
        ),
    ],
    redis: Redis,
    fetcher: Fetcher,
    ruleset: Annotated[GameMode | None, Query(description="指定 ruleset；为空则使用谱面自身模式")] = None,
    ruleset_id: Annotated[int | None, Query(description="以数字指定 ruleset （与 ruleset 二选一）")] = None,
):
    mods_ = []
    if mods and mods[0].isdigit():
        mods_ = int_to_mods(int(mods[0]))
    else:
        for i in mods:
            try:
                mods_.append(json.loads(i))
            except json.JSONDecodeError:
                mods_.append(APIMod(acronym=i, settings={}))
    mods_.sort(key=lambda x: x["acronym"])
    if ruleset_id is not None and ruleset is None:
        ruleset = GameMode.from_int(ruleset_id)
    if ruleset is None:
        beatmap_db = await Beatmap.get_or_fetch(db, fetcher, beatmap_id)
        ruleset = beatmap_db.mode
    key = (
        f"beatmap:{beatmap_id}:{ruleset}:"
        f"{hashlib.md5(str(mods_).encode(), usedforsecurity=False).hexdigest()}:attributes"
    )
    if await redis.exists(key):
        return DifficultyAttributes.model_validate_json(await redis.get(key))  # pyright: ignore[reportArgumentType]
    try:
        return await calculate_beatmap_attributes(beatmap_id, ruleset, mods_, redis, fetcher)
    except HTTPStatusError:
        raise HTTPException(status_code=404, detail="Beatmap not found")
    except ConvertError as e:
        raise HTTPException(status_code=400, detail=str(e))
