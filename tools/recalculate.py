from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import os
import sys
import warnings

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.calculator import calculate_pp, calculate_score_to_level, init_calculator
from app.config import settings
from app.const import BANCHOBOT_ID
from app.database import TotalScoreBestScore, UserStatistics
from app.database.beatmap import Beatmap
from app.database.best_scores import BestScore
from app.database.score import Score, calculate_playtime, calculate_user_pp
from app.dependencies.database import engine, get_redis
from app.dependencies.fetcher import get_fetcher
from app.fetcher import Fetcher
from app.log import log
from app.models.mods import init_mods, init_ranked_mods, mod_to_save, mods_can_get_pp
from app.models.score import GameMode, Rank

from httpx import HTTPError
from redis.asyncio import Redis
from sqlalchemy.orm import joinedload
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = log("Recalculate")

warnings.filterwarnings("ignore")


@dataclass(frozen=True)
class RecalculateConfig:
    user_ids: set[int]
    modes: set[GameMode]
    mods: set[str]
    beatmap_ids: set[int]
    beatmapset_ids: set[int]
    dry_run: bool
    concurrency: int
    recalculate_all: bool


def parse_cli_args(argv: list[str]) -> RecalculateConfig:
    parser = argparse.ArgumentParser(description="Recalculate stored performance data")
    parser.add_argument("--user-id", dest="user_ids", action="append", type=int, help="Filter by user id")
    parser.add_argument(
        "--mode",
        dest="modes",
        action="append",
        help="Filter by game mode (accepts names like osu, taiko or numeric ids)",
    )
    parser.add_argument(
        "--mod",
        dest="mods",
        action="append",
        help="Filter by mod acronym (can be passed multiple times or comma separated)",
    )
    parser.add_argument("--beatmap-id", dest="beatmap_ids", action="append", type=int, help="Filter by beatmap id")
    parser.add_argument(
        "--beatmapset-id",
        dest="beatmapset_ids",
        action="append",
        type=int,
        help="Filter by beatmapset id",
    )
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Execute without committing changes")
    parser.add_argument(
        "--concurrency",
        dest="concurrency",
        type=int,
        default=10,
        help="Maximum number of concurrent recalculation tasks",
    )
    parser.add_argument(
        "--all",
        dest="recalculate_all",
        action="store_true",
        help="Recalculate all users across all modes (ignores filter requirement)",
    )
    args = parser.parse_args(argv)

    if not args.recalculate_all and not any(
        (
            args.user_ids,
            args.modes,
            args.mods,
            args.beatmap_ids,
            args.beatmapset_ids,
        )
    ):
        parser.print_help(sys.stderr)
        parser.exit(1, "\nNo filters provided; please specify at least one target option.\n")

    user_ids = set(args.user_ids or [])

    modes: set[GameMode] = set()
    for raw in args.modes or []:
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            mode = GameMode.parse(piece)
            if mode is None:
                parser.error(f"Unknown game mode: {piece}")
            modes.add(mode)

    mods = {mod.strip().upper() for raw in args.mods or [] for mod in raw.split(",") if mod.strip()}
    beatmap_ids = set(args.beatmap_ids or [])
    beatmapset_ids = set(args.beatmapset_ids or [])
    concurrency = max(1, args.concurrency)

    return RecalculateConfig(
        user_ids=user_ids,
        modes=modes,
        mods=mods,
        beatmap_ids=beatmap_ids,
        beatmapset_ids=beatmapset_ids,
        dry_run=args.dry_run,
        concurrency=concurrency,
        recalculate_all=args.recalculate_all,
    )


async def run_in_batches(coros: Sequence[Awaitable[None]], batch_size: int) -> None:
    tasks = list(coros)
    for i in range(0, len(tasks), batch_size):
        await asyncio.gather(*tasks[i : i + batch_size])


def _score_has_required_mod(mods: list[dict] | None, required: set[str]) -> bool:
    if not required:
        return True
    if not mods:
        return False
    for mod in mods:
        acronym = mod.get("acronym") if isinstance(mod, dict) else str(mod)
        if acronym and acronym.upper() in required:
            return True
    return False


def _retry_wait_seconds(exc: HTTPError) -> float | None:
    response = getattr(exc, "response", None)
    if response is None or response.status_code != 429:
        return None
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return 5.0
    try:
        return max(float(retry_after), 1.0)
    except ValueError:
        try:
            target = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return 5.0
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        delay = (target - datetime.now(UTC)).total_seconds()
        return max(delay, 1.0)


async def determine_targets(config: RecalculateConfig) -> dict[tuple[int, GameMode], set[int] | None]:
    targets: dict[tuple[int, GameMode], set[int] | None] = {}
    if config.mods or config.beatmap_ids or config.beatmapset_ids:
        await _populate_targets_from_scores(config, targets)

    if config.user_ids and not (config.mods or config.beatmap_ids or config.beatmapset_ids):
        await _populate_targets_from_statistics(config, targets, config.user_ids)
    elif not targets:
        await _populate_targets_from_statistics(config, targets, None)

    if config.user_ids:
        targets = {key: value for key, value in targets.items() if key[0] in config.user_ids}
    if config.modes:
        targets = {key: value for key, value in targets.items() if key[1] in config.modes}

    targets = {key: value for key, value in targets.items() if key[0] != BANCHOBOT_ID}
    return targets


async def _populate_targets_from_scores(
    config: RecalculateConfig,
    targets: dict[tuple[int, GameMode], set[int] | None],
) -> None:
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        stmt = select(Score.id, Score.user_id, Score.gamemode, Score.mods).where(col(Score.passed).is_(True))
        if config.user_ids:
            stmt = stmt.where(col(Score.user_id).in_(list(config.user_ids)))
        if config.modes:
            stmt = stmt.where(col(Score.gamemode).in_(list(config.modes)))
        if config.beatmap_ids:
            stmt = stmt.where(col(Score.beatmap_id).in_(list(config.beatmap_ids)))
        if config.beatmapset_ids:
            stmt = stmt.join(Beatmap).where(col(Beatmap.beatmapset_id).in_(list(config.beatmapset_ids)))

        stream = await session.stream(stmt)
        async for score_id, user_id, gamemode, mods in stream:
            mode = gamemode if isinstance(gamemode, GameMode) else GameMode(gamemode)
            if user_id == BANCHOBOT_ID:
                continue
            if not _score_has_required_mod(mods, config.mods):
                continue
            key = (user_id, mode)
            bucket = targets.get(key)
            if bucket is None:
                targets[key] = {score_id}
            else:
                bucket.add(score_id)


async def _populate_targets_from_statistics(
    config: RecalculateConfig,
    targets: dict[tuple[int, GameMode], set[int] | None],
    user_filter: set[int] | None,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        stmt = select(UserStatistics.user_id, UserStatistics.mode).where(UserStatistics.user_id != BANCHOBOT_ID)
        if user_filter:
            stmt = stmt.where(col(UserStatistics.user_id).in_(list(user_filter)))
        if config.modes:
            stmt = stmt.where(col(UserStatistics.mode).in_(list(config.modes)))
        result = await session.exec(stmt)
        for user_id, mode in result:
            gamemode = mode if isinstance(mode, GameMode) else GameMode(mode)
            targets.setdefault((user_id, gamemode), None)


async def recalc_score_pp(
    session: AsyncSession,
    fetcher: Fetcher,
    redis: Redis,
    score: Score,
) -> float | None:
    attempts = 10
    while attempts > 0:
        try:
            beatmap = await Beatmap.get_or_fetch(session, fetcher, bid=score.beatmap_id)
        except HTTPError as exc:
            wait = _retry_wait_seconds(exc)
            if wait is not None:
                logger.warning(
                    f"Rate limited while fetching beatmap {score.beatmap_id}; waiting {wait:.1f}s before retry"
                )
                await asyncio.sleep(wait)
                continue
            attempts -= 1
            await asyncio.sleep(2)
            continue

        ranked = beatmap.beatmap_status.has_pp() | settings.enable_all_beatmap_pp
        if not ranked or not mods_can_get_pp(int(score.gamemode), score.mods):
            score.pp = 0
            return 0.0

        try:
            beatmap_raw = await fetcher.get_or_fetch_beatmap_raw(redis, score.beatmap_id)
            new_pp = await calculate_pp(score, beatmap_raw, session)
            score.pp = new_pp
            return new_pp
        except HTTPError as exc:
            wait = _retry_wait_seconds(exc)
            if wait is not None:
                logger.warning(
                    f"Rate limited while fetching beatmap raw {score.beatmap_id}; waiting {wait:.1f}s before retry"
                )
                await asyncio.sleep(wait)
                continue
            attempts -= 1
            await asyncio.sleep(2)
        except Exception:
            logger.exception(f"Failed to calculate pp for score {score.id} on beatmap {score.beatmap_id}")
            return None

    logger.warning(f"Failed to recalculate pp for score {score.id} after multiple attempts")
    return None


def build_best_scores(user_id: int, gamemode: GameMode, scores: list[Score]) -> list[BestScore]:
    best_per_map: dict[int, BestScore] = {}
    for score in scores:
        if not score.passed:
            continue
        beatmap = score.beatmap
        if beatmap is None:
            continue
        ranked = beatmap.beatmap_status.has_pp() | settings.enable_all_beatmap_pp
        if not ranked or not mods_can_get_pp(int(score.gamemode), score.mods):
            continue
        if not score.pp or score.pp <= 0:
            continue
        current_best = best_per_map.get(score.beatmap_id)
        if current_best is None or current_best.pp < score.pp:
            best_per_map[score.beatmap_id] = BestScore(
                user_id=user_id,
                beatmap_id=score.beatmap_id,
                acc=score.accuracy,
                score_id=score.id,
                pp=float(score.pp),
                gamemode=gamemode,
            )
    return list(best_per_map.values())


def build_total_score_best_scores(scores: list[Score]) -> list[TotalScoreBestScore]:
    beatmap_scores: dict[int, list[TotalScoreBestScore]] = {}
    for score in scores:
        if not score.passed:
            continue
        beatmap = score.beatmap
        if beatmap is None:
            continue
        if not (beatmap.beatmap_status.has_leaderboard() | settings.enable_all_beatmap_leaderboard):
            continue
        mods_saved = mod_to_save(score.mods)
        new_entry = TotalScoreBestScore(
            user_id=score.user_id,
            score_id=score.id,
            beatmap_id=score.beatmap_id,
            gamemode=score.gamemode,
            total_score=score.total_score,
            mods=mods_saved,
            rank=score.rank,
        )
        entries = beatmap_scores.setdefault(score.beatmap_id, [])
        existing = next((item for item in entries if item.mods == mods_saved), None)
        if existing is None:
            entries.append(new_entry)
        elif score.total_score > existing.total_score:
            entries.remove(existing)
            entries.append(new_entry)
    result: list[TotalScoreBestScore] = []
    for values in beatmap_scores.values():
        result.extend(values)
    return result


async def _recalculate_statistics(
    statistics: UserStatistics,
    session: AsyncSession,
    scores: list[Score],
) -> None:
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

    cached_best: dict[int, Score] = {}

    for score in scores:
        beatmap = score.beatmap
        if beatmap is None:
            continue

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

        ranked = beatmap.beatmap_status.has_pp() | settings.enable_all_beatmap_pp
        if ranked and score.passed:
            statistics.maximum_combo = max(statistics.maximum_combo, score.max_combo)
            previous = cached_best.get(score.beatmap_id)
            difference = score.total_score - (previous.total_score if previous else 0)
            if difference > 0:
                cached_best[score.beatmap_id] = score
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


async def recalculate_user_mode(
    user_id: int,
    gamemode: GameMode,
    score_filter: set[int] | None,
    config: RecalculateConfig,
    fetcher: Fetcher,
    redis: Redis,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore, AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        try:
            statistics = (
                await session.exec(
                    select(UserStatistics).where(
                        UserStatistics.user_id == user_id,
                        UserStatistics.mode == gamemode,
                    )
                )
            ).first()
            if statistics is None:
                logger.warning(f"No statistics found for user {user_id} mode {gamemode}")
                return

            old_pp = float(statistics.pp or 0)
            old_acc = float(statistics.hit_accuracy or 0)

            score_stmt = (
                select(Score)
                .where(Score.user_id == user_id, Score.gamemode == gamemode)
                .options(joinedload(Score.beatmap))
            )
            result = await session.exec(score_stmt)
            scores: list[Score] = list(result)

            passed_scores = [score for score in scores if score.passed]
            target_set = score_filter if score_filter is not None else {score.id for score in passed_scores}
            if score_filter is not None and not target_set:
                logger.info(f"User {user_id} mode {gamemode}: no scores matched filters")
                return

            recalculated = 0
            failed = 0
            for score in passed_scores:
                if target_set and score.id not in target_set:
                    continue
                result_pp = await recalc_score_pp(session, fetcher, redis, score)
                if result_pp is None:
                    failed += 1
                else:
                    recalculated += 1

            best_scores = build_best_scores(user_id, gamemode, passed_scores)
            total_best_scores = build_total_score_best_scores(passed_scores)

            await session.execute(
                delete(BestScore).where(
                    col(BestScore.user_id) == user_id,
                    col(BestScore.gamemode) == gamemode,
                )
            )
            await session.execute(
                delete(TotalScoreBestScore).where(
                    col(TotalScoreBestScore.user_id) == user_id,
                    col(TotalScoreBestScore.gamemode) == gamemode,
                )
            )
            session.add_all(best_scores)
            session.add_all(total_best_scores)
            await session.flush()

            await _recalculate_statistics(statistics, session, scores)
            await session.flush()

            new_pp = float(statistics.pp or 0)
            new_acc = float(statistics.hit_accuracy or 0)

            message = (
                "Dry-run | user {user_id} mode {mode} | recalculated {recalculated} scores (failed {failed}) | "
                "pp {old_pp:.2f} -> {new_pp:.2f} | acc {old_acc:.2f} -> {new_acc:.2f}"
            )
            success_message = (
                "Recalculated user {user_id} mode {mode} | updated {recalculated} scores (failed {failed}) | "
                "pp {old_pp:.2f} -> {new_pp:.2f} | acc {old_acc:.2f} -> {new_acc:.2f}"
            )

            if config.dry_run:
                await session.rollback()
                logger.info(
                    message.format(
                        user_id=user_id,
                        mode=gamemode,
                        recalculated=recalculated,
                        failed=failed,
                        old_pp=old_pp,
                        new_pp=new_pp,
                        old_acc=old_acc,
                        new_acc=new_acc,
                    )
                )
            else:
                await session.commit()
                logger.success(
                    success_message.format(
                        user_id=user_id,
                        mode=gamemode,
                        recalculated=recalculated,
                        failed=failed,
                        old_pp=old_pp,
                        new_pp=new_pp,
                        old_acc=old_acc,
                        new_acc=new_acc,
                    )
                )
        except Exception:
            if session.in_transaction():
                await session.rollback()
            logger.exception(f"Failed to process user {user_id} mode {gamemode}")


async def recalculate(config: RecalculateConfig) -> None:
    fetcher = await get_fetcher()
    redis = get_redis()

    init_mods()
    init_ranked_mods()
    await init_calculator()

    targets = await determine_targets(config)
    if not targets:
        logger.info("No targets matched the provided filters; nothing to recalculate")
        await engine.dispose()
        return

    scope = "full" if config.recalculate_all else "filtered"
    logger.info(
        "Recalculating {} user/mode pairs ({}) | dry-run={} | concurrency={}",
        len(targets),
        scope,
        config.dry_run,
        config.concurrency,
    )

    semaphore = asyncio.Semaphore(config.concurrency)
    coroutines = [
        recalculate_user_mode(user_id, mode, score_ids, config, fetcher, redis, semaphore)
        for (user_id, mode), score_ids in targets.items()
    ]
    await run_in_batches(coroutines, config.concurrency)
    await engine.dispose()


if __name__ == "__main__":
    config = parse_cli_args(sys.argv[1:])
    asyncio.run(recalculate(config))
