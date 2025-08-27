from __future__ import annotations

from app.database.beatmapset import Beatmapset
from app.database.beatmapset_ratings import BeatmapRating
from app.database.lazer_user import User
from app.database.score import Score
from app.dependencies.database import Database
from app.dependencies.fetcher import get_fetcher
from app.dependencies.user import get_client_user
from app.fetcher import Fetcher

from .router import router

from fastapi import Body, HTTPException, Security
from sqlmodel import col, select


@router.get("/beatmapsets/{beatmapset_id}/can_rate", response_model=bool)
async def can_rate_beatmapset(
    beatmapset_id: int,
    session: Database,
    current_user: User = Security(get_client_user),
    fetcher: Fetcher = Security(get_fetcher),
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
    beatmapset = await session.get(Beatmapset, beatmapset_id)
    prev_ratings = (await session.exec(select(BeatmapRating).where(BeatmapRating.user_id == user_id))).first()
    if prev_ratings is not None:  # 打过分的不能再打
        return False
    if beatmapset is None:
        raise HTTPException(404, "Beatmapset not found")
    for beatmap in beatmapset.beatmaps:
        all_beatmap_scores = (
            await session.exec(
                select(Score)
                .where(Score.beatmap_id == beatmap.id)
                .where(Score.user_id == user_id)
                .where(col(Score.passed).is_(True))
            )
        ).all()
        if len(all_beatmap_scores) <= 0:  # 没有passed成绩
            return False
    return True


@router.post("/beatmapsets/{beatmapset_id}/ratings", status_code=201)
async def rate_beatmaps(
    beatmapset_id: int,
    session: Database,
    rating: int = Body(...),
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
    new_rating: BeatmapRating = BeatmapRating(beatmapset_id=beatmapset_id, user_id=user_id, rating=rating)
    session.add(new_rating)
    current_beatmapset = (await session.exec(select(Beatmapset).where(Beatmapset.id == beatmapset_id))).first()
    if current_beatmapset is None:
        raise HTTPException(404, "Beatmapset Not Found")
    if current_beatmapset.ratings is None:
        current_beatmapset.ratings = [0] * 11
    current_beatmapset.ratings[int(rating)] += 1
    await session.commit()
    await session.refresh(current_beatmapset)
    return None
