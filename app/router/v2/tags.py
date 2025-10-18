from typing import Annotated

from app.database.beatmap import Beatmap
from app.database.beatmap_tags import BeatmapTagVote
from app.database.score import Score
from app.database.user import User
from app.dependencies.database import Database
from app.dependencies.user import get_client_user
from app.models.score import Rank
from app.models.tags import BeatmapTags, get_all_tags, get_tag_by_id

from .router import router

from fastapi import Depends, HTTPException, Path
from pydantic import BaseModel
from sqlmodel import col, exists, select
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
    return APITagCollection(tags=get_all_tags())


async def check_user_can_vote(user: User, beatmap_id: int, session: AsyncSession):
    user_beatmap_score = (
        await session.exec(
            select(exists())
            .where(Score.beatmap_id == beatmap_id)
            .where(Score.user_id == user.id)
            .where(col(Score.rank).not_in([Rank.F, Rank.D]))
            .where(col(Score.beatmap).has(col(Beatmap.mode) == Score.gamemode))
        )
    ).first()
    return user_beatmap_score is not None


@router.put(
    "/beatmaps/{beatmap_id}/tags/{tag_id}",
    tags=["用户标签"],
    status_code=204,
    name="为谱面投票标签",
    description="为指定谱面添加标签投票。",
)
async def vote_beatmap_tags(
    beatmap_id: Annotated[int, Path(..., description="谱面 ID")],
    tag_id: Annotated[int, Path(..., description="标签 ID")],
    session: Database,
    current_user: Annotated[User, Depends(get_client_user)],
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    try:
        get_tag_by_id(tag_id)
        beatmap = (await session.exec(select(exists()).where(Beatmap.id == beatmap_id))).first()
        if beatmap is None or (not beatmap):
            raise HTTPException(404, "beatmap not found")
        previous_votes = (
            await session.exec(
                select(BeatmapTagVote)
                .where(BeatmapTagVote.beatmap_id == beatmap_id)
                .where(BeatmapTagVote.tag_id == tag_id)
                .where(BeatmapTagVote.user_id == current_user.id)
            )
        ).first()
        if previous_votes is None and check_user_can_vote(current_user, beatmap_id, session):
            new_vote = BeatmapTagVote(tag_id=tag_id, beatmap_id=beatmap_id, user_id=current_user.id)
            session.add(new_vote)
        await session.commit()
    except ValueError:
        raise HTTPException(400, "Tag is not found")


@router.delete(
    "/beatmaps/{beatmap_id}/tags/{tag_id}",
    tags=["用户标签", "谱面"],
    status_code=204,
    name="取消谱面标签投票",
    description="取消对指定谱面标签的投票。",
)
async def devote_beatmap_tags(
    beatmap_id: Annotated[int, Path(..., description="谱面 ID")],
    tag_id: Annotated[int, Path(..., description="标签 ID")],
    session: Database,
    current_user: Annotated[User, Depends(get_client_user)],
):
    """
    取消对谱面指定标签的投票。

    - **beatmap_id**: 谱面ID
    - **tag_id**: 标签ID
    """
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

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
    except ValueError:
        raise HTTPException(400, "Tag is not found")
