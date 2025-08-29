from __future__ import annotations

from app.database.beatmap import Beatmap
from app.database.beatmap_tags import BeatmapTagVote
from app.dependencies.database import get_db
from app.dependencies.user import get_current_user
from app.models.tags import BeatmapTags, get_all_tags, get_tag_by_id

from .router import router

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class APITagCollection(BaseModel):
    tags: list[BeatmapTags]


@router.get(
    "/tags",
    tags=["用户标签"],
    response_model=APITagCollection,
    name="获取所有标签",
    description="获取所有可用的谱面标签。",
)
async def router_get_all_tags():
    """
    获取所有可用标签。
    返回系统中所有可用的谱面标签列表。
    """
    return APITagCollection(tags=get_all_tags())


@router.put(
    "/beatmaps/{beatmap_id}/tags/{tag_id}",
    tags=["用户标签"],
    status_code=201,
    name="为谱面投票标签",
    description="为指定谱面添加标签投票。",
)
async def vote_beatmap_tags(
    beatmap_id: int, tag_id: int, session: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)
):
    """
    谱面添加标签投票。
    - **beatmap_id**: 谱面ID
    - **tag_id**: 标签ID
    """
    try:
        get_tag_by_id(tag_id)
        beatmap = await session.get(Beatmap, beatmap_id)
        if beatmap is None:
            raise HTTPException(404, "beatmap not found")
        previous_votes = (
            await session.exec(
                select(BeatmapTagVote)
                .where(BeatmapTagVote.beatmap_id == beatmap_id)
                .where(BeatmapTagVote.tag_id == tag_id)
                .where(BeatmapTagVote.user_id == current_user.id)
            )
        ).first()
        if previous_votes is None:
            new_vote = BeatmapTagVote(tag_id=tag_id, beatmap_id=beatmap_id, user_id=current_user.id)
            session.add(new_vote)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete(
    "/beatmaps/{beatmap_id}/tags/{tag_id}",
    tags=["用户标签"],
    status_code=201,
    name="取消谱面标签投票",
    description="取消对指定谱面标签的投票。",
)
async def devote_beatmap_tags(
    beatmap_id: int, tag_id: int, session: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)
):
    """
    取消对谱面指定标签的投票。

    - **beatmap_id**: 谱面ID
    - **tag_id**: 标签ID
    """
    try:
        tag = get_tag_by_id(tag_id)
        assert tag is not None
        beatmap = await session.get(Beatmap, beatmap_id)
        if beatmap is None:
            raise HTTPException(404, "beatmap not found")
        previous_votes = (
            await session.exec(
                select(BeatmapTagVote)
                .where(BeatmapTagVote.beatmap_id == beatmap_id)
                .where(BeatmapTagVote.tag_id == tag_id)
                .where(BeatmapTagVote.user_id == current_user.id)
            )
        ).first()
        if previous_votes is not None:
            await session.delete(previous_votes)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))
