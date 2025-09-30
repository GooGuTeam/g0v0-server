from __future__ import annotations

import asyncio
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.calculator import (
    calculate_pp,
    calculate_score_to_level,
)
from app.config import settings
from app.const import BANCHOBOT_ID
from app.database import BestScore, UserStatistics
from app.database.beatmap import Beatmap
from app.database.pp_best_score import PPBestScore
from app.database.score import Score, calculate_playtime, calculate_user_pp
from app.dependencies.database import engine, get_redis
from app.dependencies.fetcher import get_fetcher
from app.fetcher import Fetcher
from app.log import logger
from app.models.mods import mod_to_save, mods_can_get_pp
from app.models.score import GameMode, Rank

from httpx import HTTPError
from redis.asyncio import Redis
from sqlalchemy.orm import joinedload
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

SEMAPHORE = asyncio.Semaphore(50)


async def run_in_batches(coros, batch_size=200):
    for i in range(0, len(coros), batch_size):
        await asyncio.gather(*coros[i : i + batch_size])


async def recalculate():
    async with AsyncSession(engine, autoflush=False) as session:
        fetcher = await get_fetcher()
        redis = get_redis()
        for mode in GameMode:
            await session.execute(delete(PPBestScore).where(col(PPBestScore.gamemode) == mode))
            await session.execute(delete(BestScore).where(col(BestScore.gamemode) == mode))
            await session.commit()
            logger.info(f"Recalculating for mode: {mode}")
            statistics_list = (
                await session.exec(
                    select(UserStatistics).where(
                        UserStatistics.mode == mode,
                        UserStatistics.user_id != BANCHOBOT_ID,
                    )
                )
            ).all()
            await run_in_batches(
                [
                    _recalculate_pp(statistics.user_id, statistics.mode, session, fetcher, redis)
                    for statistics in statistics_list
                ],
                batch_size=200,
            )
            await run_in_batches(
                [
                    _recalculate_best_score(statistics.user_id, statistics.mode, session)
                    for statistics in statistics_list
                ],
                batch_size=200,
            )
            await session.commit()
            for statistics in statistics_list:
                await session.refresh(statistics)
            await run_in_batches(
                [_recalculate_statistics(statistics, session) for statistics in statistics_list], batch_size=200
            )

            await session.commit()
            logger.success(f"Recalculated for mode: {mode}, total users: {len(statistics_list)}")
    await engine.dispose()


async def _recalculate_pp(
    user_id: int,
    gamemode: GameMode,
    session: AsyncSession,
    fetcher: Fetcher,
    redis: Redis,
):
    async with SEMAPHORE:
        scores = (
            await session.exec(
                select(Score).where(
                    Score.user_id == user_id,
                    Score.gamemode == gamemode,
                    col(Score.passed).is_(True),
                )
            )
        ).all()
        prev: dict[int, PPBestScore] = {}

        async def cal(score: Score):
            time = 10
            beatmap_id = score.beatmap_id
            while time > 0:
                try:
                    db_beatmap = await Beatmap.get_or_fetch(session, fetcher, bid=beatmap_id)
                except HTTPError:
                    time -= 1
                    await asyncio.sleep(2)
                    continue
                ranked = db_beatmap.beatmap_status.has_pp() | settings.enable_all_beatmap_pp
                if not ranked or not mods_can_get_pp(int(score.gamemode), score.mods):
                    score.pp = 0
                    return
                try:
                    beatmap_raw = await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
                    pp = await calculate_pp(score, beatmap_raw, session)
                    if pp == 0:
                        return
                    score.pp = pp
                    if score.beatmap_id not in prev or prev[score.beatmap_id].pp < pp:
                        best_score = PPBestScore(
                            user_id=user_id,
                            beatmap_id=beatmap_id,
                            acc=score.accuracy,
                            score_id=score.id,
                            pp=pp,
                            gamemode=score.gamemode,
                        )
                        prev[score.beatmap_id] = best_score
                    return
                except HTTPError:
                    time -= 1
                    await asyncio.sleep(2)
                    continue
                except Exception:
                    logger.exception(f"Error calculating pp for score {score.id} on beatmap {beatmap_id}")
                    return
            if time <= 0:
                logger.warning(f"Failed to fetch beatmap {beatmap_id} after 10 attempts, retrying later...")
                return score

        while len(scores) > 0:
            results = await asyncio.gather(*[cal(s) for s in scores])
            scores = [s for s in results if s is not None]
            if len(scores) == 0:
                break
            await asyncio.sleep(30)
            logger.info(f"Retry to calculate for {gamemode}, total: {len(scores)}")

        for best_score in prev.values():
            session.add(best_score)


async def _recalculate_best_score(
    user_id: int,
    gamemode: GameMode,
    session: AsyncSession,
):
    async with SEMAPHORE:
        beatmap_best_score: dict[int, list[BestScore]] = {}
        scores = (
            await session.exec(
                select(Score).where(
                    Score.gamemode == gamemode,
                    col(Score.passed).is_(True),
                    Score.user_id == user_id,
                )
            )
        ).all()
        for score in scores:
            if not (
                (await score.awaitable_attrs.beatmap).beatmap_status.has_leaderboard()
                | settings.enable_all_beatmap_leaderboard
            ):
                continue
            mod_for_save = mod_to_save(score.mods)
            bs = BestScore(
                user_id=score.user_id,
                score_id=score.id,
                beatmap_id=score.beatmap_id,
                gamemode=score.gamemode,
                total_score=score.total_score,
                mods=mod_for_save,
                rank=score.rank,
            )
            if score.beatmap_id not in beatmap_best_score:
                beatmap_best_score[score.beatmap_id] = [bs]
            else:
                b = next(
                    (
                        s
                        for s in beatmap_best_score[score.beatmap_id]
                        if s.mods == mod_for_save and s.beatmap_id == score.beatmap_id
                    ),
                    None,
                )
                if b is None:
                    beatmap_best_score[score.beatmap_id].append(bs)
                elif score.total_score > b.total_score:
                    beatmap_best_score[score.beatmap_id].remove(b)
                    beatmap_best_score[score.beatmap_id].append(bs)

        for best_score_in_beatmap in beatmap_best_score.values():
            for score in best_score_in_beatmap:
                session.add(score)


async def _recalculate_statistics(statistics: UserStatistics, session: AsyncSession):
    async with SEMAPHORE:
        statistics.pp, statistics.hit_accuracy = await calculate_user_pp(session, statistics.user_id, statistics.mode)

        statistics.play_count = 0
        statistics.total_score = 0
        statistics.maximum_combo = 0
        statistics.play_time = 0
        statistics.total_hits = 0
        statistics.count_100 = 0
        statistics.count_300 = 0
        statistics.count_50 = 0
        statistics.count_miss = 0
        statistics.ranked_score = 0
        statistics.grade_ss = 0
        statistics.grade_ssh = 0
        statistics.grade_s = 0
        statistics.grade_sh = 0
        statistics.grade_a = 0

        scores = (
            await session.exec(
                select(Score)
                .where(
                    Score.user_id == statistics.user_id,
                    Score.gamemode == statistics.mode,
                )
                .options(joinedload(Score.beatmap))
            )
        ).all()

        cached_beatmap_best: dict[int, Score] = {}

        for score in scores:
            beatmap: Beatmap = score.beatmap
            ranked = beatmap.beatmap_status.has_pp() | settings.enable_all_beatmap_pp

            statistics.play_count += 1
            statistics.total_score += score.total_score
            playtime, is_valid = calculate_playtime(score, beatmap.hit_length)
            if is_valid:
                statistics.play_time += playtime
            statistics.count_300 += score.n300 + score.ngeki
            statistics.count_100 += score.n100 + score.nkatu
            statistics.count_50 += score.n50
            statistics.count_miss += score.nmiss
            statistics.total_hits += score.n300 + score.ngeki + score.n100 + score.nkatu + score.n50

            if ranked and score.passed:
                statistics.maximum_combo = max(statistics.maximum_combo, score.max_combo)
                previous = cached_beatmap_best.get(score.beatmap_id)
                difference = score.total_score - (previous.total_score if previous else 0)
                if difference > 0:
                    cached_beatmap_best[score.beatmap_id] = score
                    statistics.ranked_score += difference
                    match score.rank:
                        case Rank.X:
                            statistics.grade_ss += 1
                        case Rank.XH:
                            statistics.grade_ssh += 1
                        case Rank.S:
                            statistics.grade_s += 1
                        case Rank.SH:
                            statistics.grade_sh += 1
                        case Rank.A:
                            statistics.grade_a += 1
                    if previous is not None:
                        match previous.rank:
                            case Rank.X:
                                statistics.grade_ss -= 1
                            case Rank.XH:
                                statistics.grade_ssh -= 1
                            case Rank.S:
                                statistics.grade_s -= 1
                            case Rank.SH:
                                statistics.grade_sh -= 1
                            case Rank.A:
                                statistics.grade_a -= 1
        statistics.level_current = calculate_score_to_level(statistics.total_score)


if __name__ == "__main__":
    asyncio.run(recalculate())
