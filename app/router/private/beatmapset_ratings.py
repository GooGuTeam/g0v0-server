from __future__ import annotations

from app.database.beatmapset import Beatmapset
from app.database.beatmapset_ratings import BeatmapRating
from app.database.lazer_user import User
from app.database.score import Score
from app.dependencies.database import Database
from app.dependencies.fetcher import get_fetcher
from app.dependencies.user import get_current_user
from app.fetcher import Fetcher

from .router import router

from fastapi import Security
from sqlmodel import select


@router.get("/beatmapsets/{beatmapset_id}/can_rate", tags=["beatmapset", "rate"], response_model=bool)
async def can_rate_beatmapset(
    beatmapset_id: int,
    session: Database,
    current_user: User = Security(get_current_user),
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
    beatmapset = await Beatmapset.get_or_fetch(session, fetcher, beatmapset_id)
    prev_ratings = (await session.exec(select(BeatmapRating).where(BeatmapRating.user_id == user_id))).first()
    if prev_ratings is not None:  # 打过分的不能再打
        return False
    assert beatmapset is not None
    for beatmap in beatmapset.beatmaps:
        all_beatmap_scores = (
            await session.exec(
                select(Score).where(Score.beatmap_id == beatmap.id).where(Score.user_id == user_id).where(Score.passed)
            )
        ).all()
        if all_beatmap_scores is not None or len(all_beatmap_scores) <= 0:  # 没有passed成绩
            return False
    return True
