from __future__ import annotations

from app.database.beatmap import Beatmap
from app.database.beatmapset import Beatmapset
from app.database.beatmapset_ratings import BeatmapRating
from app.database.lazer_user import User
from app.database.score import Score
from app.dependencies.database import Database
from app.dependencies.user import get_client_user

from .router import router

from fastapi import Body, HTTPException, Security
from sqlmodel import col, exists, select


@router.get("/beatmapsets/{beatmapset_id}/can_rate", name="判断用户能否为谱面集打分", response_model=bool)
async def can_rate_beatmapset(
    beatmapset_id: int,
    session: Database,
    current_user: User = Security(get_client_user),
):
    """检查用户是否可以评价谱面集

    检查当前用户是否可以对指定的谱面集进行评价
    参数:
    - beatmapset_id: 谱面集ID

    错误情况:
    - 404: 找不到指定谱面集

    返回:
    - bool: 用户是否可以评价谱面集
    """
    user_id = current_user.id
    prev_ratings = (await session.exec(select(BeatmapRating).where(BeatmapRating.user_id == user_id))).first()
    if prev_ratings is not None:
        return False
    query = select(exists()).where(
        Score.user_id == user_id,
        col(Score.beatmap).has(col(Beatmap.beatmapset_id) == beatmapset_id),
        col(Score.passed).is_(True),
    )
    return (await session.exec(query)).first() or False


@router.post("/beatmapsets/{beatmapset_id}/ratings", name="上传对谱面集的打分", status_code=201)
async def rate_beatmaps(
    beatmapset_id: int,
    session: Database,
    rating: int = Body(..., ge=0, le=10),
    current_user: User = Security(get_client_user),
):
    """为谱面集评分

    为指定的谱面集添加用户评分，并更新谱面集的评分统计信息

    参数:
    - beatmapset_id: 谱面集ID
    - rating: 评分

    错误情况:
    - 404: 找不到指定谱面集

    返回:
    - 成功: None
    """
    user_id = current_user.id
    current_beatmapset = (await session.exec(select(exists()).where(Beatmapset.id == beatmapset_id))).first()
    if not current_beatmapset:
        raise HTTPException(404, "Beatmapset Not Found")
    can_rating = await can_rate_beatmapset(beatmapset_id, session, current_user)
    if not can_rating:
        raise HTTPException(403, "User Cannot Rate This Beatmapset")
    new_rating: BeatmapRating = BeatmapRating(beatmapset_id=beatmapset_id, user_id=user_id, rating=rating)
    session.add(new_rating)
    await session.commit()
