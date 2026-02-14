"""Banned beatmap recalculation scheduled task.

Handles PP recalculation when beatmaps are banned or unbanned.
Zeroes PP for banned beatmaps and recalculates PP when beatmaps
are unbanned, updating affected user statistics.
"""

import asyncio
import json

from app.calculator import calculate_pp
from app.config import settings
from app.database.beatmap import BannedBeatmaps, Beatmap
from app.database.best_scores import BestScore
from app.database.score import Score, calculate_user_pp
from app.database.statistics import UserStatistics
from app.dependencies.database import get_redis, with_db
from app.dependencies.fetcher import get_fetcher
from app.dependencies.scheduler import get_scheduler
from app.log import logger
from app.models.mods import mods_can_get_pp

from sqlmodel import col, delete, select


@get_scheduler().scheduled_job("interval", id="recalculate_banned_beatmap", hours=1)
async def recalculate_banned_beatmap() -> None:
    """Recalculate PP for newly banned or unbanned beatmaps.

    Runs hourly to detect beatmap ban status changes and update
    scores and user statistics accordingly.
    """
    redis = get_redis()
    last_banned_beatmaps = set()
    last_banned = await redis.get("last_banned_beatmap")
    if last_banned:
        last_banned_beatmaps = set(json.loads(last_banned))
    affected_users = set()

    async with with_db() as session:
        query = select(BannedBeatmaps.beatmap_id).distinct()
        if last_banned_beatmaps:
            query = query.where(col(BannedBeatmaps.beatmap_id).not_in(last_banned_beatmaps))
        new_banned_beatmaps = (await session.exec(query)).all()

        current_banned = (await session.exec(select(BannedBeatmaps.beatmap_id).distinct())).all()
        unbanned_beatmaps = [b for b in last_banned_beatmaps if b not in current_banned]
        for i in new_banned_beatmaps:
            last_banned_beatmaps.add(i)
            await session.execute(delete(BestScore).where(col(BestScore.beatmap_id) == i))
            scores = (await session.exec(select(Score).where(Score.beatmap_id == i, Score.pp > 0))).all()
            for score in scores:
                score.pp = 0
                affected_users.add((score.user_id, score.gamemode))

        if unbanned_beatmaps:
            fetcher = await get_fetcher()
            for beatmap_id in unbanned_beatmaps:
                last_banned_beatmaps.discard(beatmap_id)
                try:
                    scores = (
                        await session.exec(
                            select(Score).where(
                                Score.beatmap_id == beatmap_id,
                                col(Score.passed).is_(True),
                            )
                        )
                    ).all()
                except Exception:
                    logger.exception(f"Failed to query scores for unbanned beatmap {beatmap_id}")
                    continue

                prev: dict[tuple[int, int], BestScore] = {}
                for score in scores:
                    attempts = 3
                    while attempts > 0:
                        try:
                            db_beatmap = await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
                            break
                        except Exception:
                            attempts -= 1
                            await asyncio.sleep(1)
                    else:
                        logger.warning(f"Could not fetch beatmap raw for {beatmap_id}, skipping pp calc")
                        continue

                    try:
                        beatmap_obj = await Beatmap.get_or_fetch(session, fetcher, bid=beatmap_id)
                    except Exception:
                        beatmap_obj = None

                    ranked = (
                        beatmap_obj.beatmap_status.has_pp() if beatmap_obj else False
                    ) | settings.enable_all_beatmap_pp

                    if not ranked or not mods_can_get_pp(int(score.gamemode), score.mods):
                        continue

                    try:
                        pp = await calculate_pp(score, db_beatmap, session)
                        if not pp or pp == 0:
                            continue
                        key = (score.beatmap_id, score.user_id)
                        if key not in prev or prev[key].pp < pp:
                            best_score = BestScore(
                                user_id=score.user_id,
                                beatmap_id=beatmap_id,
                                acc=score.accuracy,
                                score_id=score.id,
                                pp=pp,
                                gamemode=score.gamemode,
                            )
                            prev[key] = best_score
                            affected_users.add((score.user_id, score.gamemode))
                            score.pp = pp
                    except Exception:
                        logger.exception(f"Error calculating pp for score {score.id} on unbanned beatmap {beatmap_id}")
                        continue

                for best in prev.values():
                    session.add(best)

        for user_id, gamemode in affected_users:
            statistics = (
                await session.exec(
                    select(UserStatistics)
                    .where(UserStatistics.user_id == user_id)
                    .where(col(UserStatistics.mode) == gamemode)
                )
            ).first()
            if not statistics:
                continue
            statistics.pp, statistics.hit_accuracy = await calculate_user_pp(session, statistics.user_id, gamemode)

        await session.commit()
    logger.info(
        f"Recalculated banned beatmaps, banned {len(new_banned_beatmaps)} beatmaps, "
        f"unbanned {len(unbanned_beatmaps)} beatmaps, affected {len(affected_users)} users"
    )
    await redis.set("last_banned_beatmap", json.dumps(list(last_banned_beatmaps)))
