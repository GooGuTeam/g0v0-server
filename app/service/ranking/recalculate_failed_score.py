from __future__ import annotations

from app.calculator import pre_fetch_and_calculate_pp
from app.database.score import Score, calculate_user_pp
from app.database.user.statistics import UserStatistics
from app.dependencies.database import get_redis, with_db
from app.dependencies.fetcher import get_fetcher
from app.dependencies.scheduler import get_scheduler
from app.log import logger

from sqlmodel import select


@get_scheduler().scheduled_job("interval", id="recalculate_banned_beatmap", minutes=5)
async def recalculate_failed_score():
    redis = get_redis()
    fetcher = await get_fetcher()
    need_add = set()
    affected_user = set()
    while True:
        scores = await redis.lpop("score:need_recalculate", 100)  # pyright: ignore[reportGeneralTypeIssues]
        if not scores:
            break
        if isinstance(scores, bytes):
            scores = [scores]
        async with with_db() as session:
            for score_id in scores:
                score_id = int(score_id)
                score = await session.get(Score, score_id)
                if score is None:
                    continue
                pp, successed = await pre_fetch_and_calculate_pp(score, session, redis, fetcher)
                if not successed:
                    need_add.add(score_id)
                else:
                    score.pp = pp
                    logger.info(
                        f"Recalculated PP for score {score.id} (user: {score.user_id}) at {score.ended_at}: {pp}"
                    )
                    affected_user.add((score.user_id, score.gamemode))
            await session.commit()
            for user_id, gamemode in affected_user:
                stats = (
                    await session.exec(
                        select(UserStatistics).where(UserStatistics.user_id == user_id, UserStatistics.mode == gamemode)
                    )
                ).first()
                if not stats:
                    continue
                stats.pp, stats.hit_accuracy = await calculate_user_pp(session, user_id, gamemode)
            await session.commit()
    if need_add:
        await redis.rpush("score:need_recalculate", *need_add)  # pyright: ignore[reportGeneralTypeIssues]
