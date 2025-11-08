from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Sequence
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
import sys
import warnings

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.calculator import calculate_pp, calculate_score_to_level, init_calculator
from app.calculators.performance import CalculateError
from app.config import settings
from app.const import BANCHOBOT_ID
from app.database import TotalScoreBestScore, UserStatistics
from app.database.beatmap import Beatmap, calculate_beatmap_attributes
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
class GlobalConfig:
    dry_run: bool
    concurrency: int
    output_csv: str | None


@dataclass(frozen=True)
class PerformanceConfig:
    user_ids: set[int]
    modes: set[GameMode]
    mods: set[str]
    beatmap_ids: set[int]
    beatmapset_ids: set[int]
    recalculate_all: bool


@dataclass(frozen=True)
class LeaderboardConfig:
    user_ids: set[int]
    modes: set[GameMode]
    mods: set[str]
    beatmap_ids: set[int]
    beatmapset_ids: set[int]
    recalculate_all: bool


@dataclass(frozen=True)
class RatingConfig:
    modes: set[GameMode]
    beatmap_ids: set[int]
    beatmapset_ids: set[int]
    recalculate_all: bool


def parse_cli_args(
    argv: list[str],
) -> tuple[str, GlobalConfig, PerformanceConfig | LeaderboardConfig | RatingConfig | None]:
    parser = argparse.ArgumentParser(description="Recalculate stored performance data")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Execute without committing changes")
    parser.add_argument(
        "--concurrency",
        dest="concurrency",
        type=int,
        default=10,
        help="Maximum number of concurrent recalculation tasks",
    )
    parser.add_argument(
        "--output-csv",
        dest="output_csv",
        type=str,
        help="Output results to a CSV file at the specified path",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # performance subcommand
    perf_parser = subparsers.add_parser("performance", help="Recalculate performance points (pp) and best scores")
    perf_parser.add_argument("--user-id", dest="user_ids", action="append", type=int, help="Filter by user id")
    perf_parser.add_argument(
        "--mode",
        dest="modes",
        action="append",
        help="Filter by game mode (accepts names like osu, taiko or numeric ids)",
    )
    perf_parser.add_argument(
        "--mod",
        dest="mods",
        action="append",
        help="Filter by mod acronym (can be passed multiple times or comma separated)",
    )
    perf_parser.add_argument("--beatmap-id", dest="beatmap_ids", action="append", type=int, help="Filter by beatmap id")
    perf_parser.add_argument(
        "--beatmapset-id",
        dest="beatmapset_ids",
        action="append",
        type=int,
        help="Filter by beatmapset id",
    )
    perf_parser.add_argument(
        "--all",
        dest="recalculate_all",
        action="store_true",
        help="Recalculate all users across all modes (ignores filter requirement)",
    )

    # leaderboard subcommand
    lead_parser = subparsers.add_parser("leaderboard", help="Recalculate leaderboard (TotalScoreBestScore)")
    lead_parser.add_argument("--user-id", dest="user_ids", action="append", type=int, help="Filter by user id")
    lead_parser.add_argument(
        "--mode",
        dest="modes",
        action="append",
        help="Filter by game mode (accepts names like osu, taiko or numeric ids)",
    )
    lead_parser.add_argument(
        "--mod",
        dest="mods",
        action="append",
        help="Filter by mod acronym (can be passed multiple times or comma separated)",
    )
    lead_parser.add_argument("--beatmap-id", dest="beatmap_ids", action="append", type=int, help="Filter by beatmap id")
    lead_parser.add_argument(
        "--beatmapset-id",
        dest="beatmapset_ids",
        action="append",
        type=int,
        help="Filter by beatmapset id",
    )
    lead_parser.add_argument(
        "--all",
        dest="recalculate_all",
        action="store_true",
        help="Recalculate all users across all modes (ignores filter requirement)",
    )

    # rating subcommand
    rating_parser = subparsers.add_parser("rating", help="Recalculate beatmap difficulty ratings")
    rating_parser.add_argument(
        "--mode",
        dest="modes",
        action="append",
        help="Filter by game mode (accepts names like osu, taiko or numeric ids)",
    )
    rating_parser.add_argument(
        "--beatmap-id", dest="beatmap_ids", action="append", type=int, help="Filter by beatmap id"
    )
    rating_parser.add_argument(
        "--beatmapset-id",
        dest="beatmapset_ids",
        action="append",
        type=int,
        help="Filter by beatmapset id",
    )
    rating_parser.add_argument(
        "--all",
        dest="recalculate_all",
        action="store_true",
        help="Recalculate all beatmaps",
    )

    # all subcommand
    subparsers.add_parser("all", help="Execute performance, leaderboard, and rating with --all")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help(sys.stderr)
        parser.exit(1, "\nNo command specified.\n")

    global_config = GlobalConfig(
        dry_run=args.dry_run,
        concurrency=max(1, args.concurrency),
        output_csv=args.output_csv,
    )

    if args.command == "all":
        return args.command, global_config, None

    if args.command in ("performance", "leaderboard"):
        if not args.recalculate_all and not any(
            (
                args.user_ids,
                args.modes,
                args.mods,
                args.beatmap_ids,
                args.beatmapset_ids,
            )
        ):
            parser.error(
                f"\n{args.command}: No filters provided; please specify at least one target option or use --all.\n"
            )

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

        if args.command == "performance":
            return (
                args.command,
                global_config,
                PerformanceConfig(
                    user_ids=user_ids,
                    modes=modes,
                    mods=mods,
                    beatmap_ids=beatmap_ids,
                    beatmapset_ids=beatmapset_ids,
                    recalculate_all=args.recalculate_all,
                ),
            )
        else:  # leaderboard
            return (
                args.command,
                global_config,
                LeaderboardConfig(
                    user_ids=user_ids,
                    modes=modes,
                    mods=mods,
                    beatmap_ids=beatmap_ids,
                    beatmapset_ids=beatmapset_ids,
                    recalculate_all=args.recalculate_all,
                ),
            )

    elif args.command == "rating":
        if not args.recalculate_all and not any(
            (
                args.modes,
                args.beatmap_ids,
                args.beatmapset_ids,
            )
        ):
            parser.error("\nrating: No filters provided; please specify at least one target option or use --all.\n")

        rating_modes: set[GameMode] = set()
        for raw in args.modes or []:
            for piece in raw.split(","):
                piece = piece.strip()
                if not piece:
                    continue
                mode = GameMode.parse(piece)
                if mode is None:
                    parser.error(f"Unknown game mode: {piece}")
                rating_modes.add(mode)

        beatmap_ids = set(args.beatmap_ids or [])
        beatmapset_ids = set(args.beatmapset_ids or [])

        return (
            args.command,
            global_config,
            RatingConfig(
                modes=rating_modes,
                beatmap_ids=beatmap_ids,
                beatmapset_ids=beatmapset_ids,
                recalculate_all=args.recalculate_all,
            ),
        )

    return args.command, global_config, None


class CSVWriter:
    """Helper class to write recalculation results to CSV files."""

    def __init__(self, csv_path: str | None):
        self.csv_path = csv_path
        self.file = None
        self.writer = None
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        if self.csv_path:
            # Create directory if it doesn't exist
            Path(self.csv_path).parent.mkdir(parents=True, exist_ok=True)
            self.file = open(self.csv_path, "w", newline="", encoding="utf-8")  # noqa: ASYNC230, SIM115
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.file:
            self.file.close()
        self.writer = None

    async def write_performance(
        self,
        user_id: int,
        mode: str,
        recalculated: int,
        failed: int,
        old_pp: float,
        new_pp: float,
        old_acc: float,
        new_acc: float,
    ):
        """Write performance recalculation result."""
        if not self.file:
            return

        async with self.lock:
            if not self.writer:
                self.writer = csv.writer(self.file)
                self.writer.writerow(
                    [
                        "type",
                        "user_id",
                        "mode",
                        "recalculated",
                        "failed",
                        "old_pp",
                        "new_pp",
                        "pp_diff",
                        "old_acc",
                        "new_acc",
                        "acc_diff",
                    ]
                )
                self.initialized = True

            self.writer.writerow(
                [
                    "performance",
                    user_id,
                    mode,
                    recalculated,
                    failed,
                    f"{old_pp:.2f}",
                    f"{new_pp:.2f}",
                    f"{new_pp - old_pp:.2f}",
                    f"{old_acc:.2f}",
                    f"{new_acc:.2f}",
                    f"{new_acc - old_acc:.2f}",
                ]
            )
            self.file.flush()

    async def write_leaderboard(self, user_id: int, mode: str, count: int, changes: dict[str, int]):
        """Write leaderboard recalculation result."""
        if not self.file:
            return

        async with self.lock:
            if not self.writer:
                self.writer = csv.writer(self.file)
                self.writer.writerow(
                    [
                        "type",
                        "user_id",
                        "mode",
                        "entries",
                        "ranked_score_diff",
                        "max_combo_diff",
                        "ss_diff",
                        "ssh_diff",
                        "s_diff",
                        "sh_diff",
                        "a_diff",
                    ]
                )
                self.initialized = True

            self.writer.writerow(
                [
                    "leaderboard",
                    user_id,
                    mode,
                    count,
                    changes["ranked_score"],
                    changes["maximum_combo"],
                    changes["grade_ss"],
                    changes["grade_ssh"],
                    changes["grade_s"],
                    changes["grade_sh"],
                    changes["grade_a"],
                ]
            )
            self.file.flush()

    async def write_rating(self, beatmap_id: int, old_rating: float, new_rating: float):
        """Write beatmap rating recalculation result."""
        if not self.file:
            return

        async with self.lock:
            if not self.writer:
                self.writer = csv.writer(self.file)
                self.writer.writerow(["type", "beatmap_id", "old_rating", "new_rating", "rating_diff"])

            self.writer.writerow(
                ["rating", beatmap_id, f"{old_rating:.2f}", f"{new_rating:.2f}", f"{new_rating - old_rating:.2f}"]
            )
            self.file.flush()


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


async def determine_targets(
    config: PerformanceConfig | LeaderboardConfig,
) -> dict[tuple[int, GameMode], set[int] | None]:
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
    config: PerformanceConfig | LeaderboardConfig,
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
    config: PerformanceConfig | LeaderboardConfig,
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
        # Use display score based on configured scoring mode
        display_score = score.get_display_score()
        statistics.total_score += display_score

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
            # Calculate difference using display scores
            previous_display = previous.get_display_score() if previous else 0
            difference = display_score - previous_display
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


async def recalculate_user_mode_performance(
    user_id: int,
    gamemode: GameMode,
    score_filter: set[int] | None,
    global_config: GlobalConfig,
    fetcher: Fetcher,
    redis: Redis,
    semaphore: asyncio.Semaphore,
    csv_writer: CSVWriter | None = None,
) -> None:
    """Recalculate performance points and best scores (without TotalScoreBestScore)."""
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

            await session.execute(
                delete(BestScore).where(
                    col(BestScore.user_id) == user_id,
                    col(BestScore.gamemode) == gamemode,
                )
            )
            session.add_all(best_scores)
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

            if global_config.dry_run:
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

            # Write to CSV if enabled
            if csv_writer:
                await csv_writer.write_performance(
                    user_id, str(gamemode), recalculated, failed, old_pp, new_pp, old_acc, new_acc
                )
        except Exception:
            if session.in_transaction():
                await session.rollback()
            logger.exception(f"Failed to process user {user_id} mode {gamemode}")


async def recalculate_user_mode_leaderboard(
    user_id: int,
    gamemode: GameMode,
    score_filter: set[int] | None,
    global_config: GlobalConfig,
    semaphore: asyncio.Semaphore,
    csv_writer: CSVWriter | None = None,
) -> None:
    """Recalculate leaderboard (TotalScoreBestScore only)."""
    async with semaphore, AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        try:
            # Get statistics
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
            pervious_data = {
                "ranked_score": statistics.ranked_score,
                "maximum_combo": statistics.maximum_combo,
                "grade_ss": statistics.grade_ss,
                "grade_ssh": statistics.grade_ssh,
                "grade_s": statistics.grade_s,
                "grade_sh": statistics.grade_sh,
                "grade_a": statistics.grade_a,
            }

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

            total_best_scores = build_total_score_best_scores(passed_scores)

            await session.execute(
                delete(TotalScoreBestScore).where(
                    col(TotalScoreBestScore.user_id) == user_id,
                    col(TotalScoreBestScore.gamemode) == gamemode,
                )
            )
            session.add_all(total_best_scores)
            await session.flush()

            # Recalculate statistics using the helper function
            await _recalculate_statistics(statistics, session, scores)
            await session.flush()
            changes = {
                "ranked_score": statistics.ranked_score - pervious_data["ranked_score"],
                "maximum_combo": statistics.maximum_combo - pervious_data["maximum_combo"],
                "grade_ss": statistics.grade_ss - pervious_data["grade_ss"],
                "grade_ssh": statistics.grade_ssh - pervious_data["grade_ssh"],
                "grade_s": statistics.grade_s - pervious_data["grade_s"],
                "grade_sh": statistics.grade_sh - pervious_data["grade_sh"],
                "grade_a": statistics.grade_a - pervious_data["grade_a"],
            }

            message = (
                "Dry-run | user {user_id} mode {mode} | {count} leaderboard entries | "
                "ranked_score: {ranked_score:+d} | max_combo: {max_combo:+d} | "
                "SS: {ss:+d} | SSH: {ssh:+d} | S: {s:+d} | SH: {sh:+d} | A: {a:+d}"
            )
            success_message = (
                "Recalculated leaderboard | user {user_id} mode {mode} | {count} entries | "
                "ranked_score: {ranked_score:+d} | max_combo: {max_combo:+d} | "
                "SS: {ss:+d} | SSH: {ssh:+d} | S: {s:+d} | SH: {sh:+d} | A: {a:+d}"
            )

            if global_config.dry_run:
                await session.rollback()
                logger.info(
                    message.format(
                        user_id=user_id,
                        mode=gamemode,
                        count=len(total_best_scores),
                        ranked_score=changes["ranked_score"],
                        max_combo=changes["maximum_combo"],
                        ss=changes["grade_ss"],
                        ssh=changes["grade_ssh"],
                        s=changes["grade_s"],
                        sh=changes["grade_sh"],
                        a=changes["grade_a"],
                    )
                )
            else:
                await session.commit()
                logger.success(
                    success_message.format(
                        user_id=user_id,
                        mode=gamemode,
                        count=len(total_best_scores),
                        ranked_score=changes["ranked_score"],
                        max_combo=changes["maximum_combo"],
                        ss=changes["grade_ss"],
                        ssh=changes["grade_ssh"],
                        s=changes["grade_s"],
                        sh=changes["grade_sh"],
                        a=changes["grade_a"],
                    )
                )

            # Write to CSV if enabled
            if csv_writer:
                await csv_writer.write_leaderboard(user_id, str(gamemode), len(total_best_scores), changes)
        except Exception:
            if session.in_transaction():
                await session.rollback()
            logger.exception(f"Failed to process leaderboard for user {user_id} mode {gamemode}")


async def recalculate_beatmap_rating(
    beatmap_id: int,
    global_config: GlobalConfig,
    fetcher: Fetcher,
    redis: Redis,
    semaphore: asyncio.Semaphore,
    csv_writer: CSVWriter | None = None,
) -> None:
    """Recalculate difficulty rating for a beatmap."""
    async with semaphore, AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        try:
            beatmap = await session.get(Beatmap, beatmap_id)
            if beatmap is None:
                logger.warning(f"Beatmap {beatmap_id} not found")
                return
            if beatmap.deleted_at is not None:
                logger.warning(f"Beatmap {beatmap_id} is deleted; skipping")
                return

            old_rating = beatmap.difficulty_rating

            attempts = 10
            while attempts > 0:
                try:
                    ruleset = GameMode(beatmap.mode) if isinstance(beatmap.mode, int) else beatmap.mode
                    attributes = await calculate_beatmap_attributes(beatmap_id, ruleset, [], redis, fetcher)
                    beatmap.difficulty_rating = attributes.star_rating
                    break
                except CalculateError as exc:
                    attempts -= 1
                    if attempts > 0:
                        logger.warning(
                            f"CalculateError for beatmap {beatmap_id} (attempts remaining: {attempts}); retrying..."
                        )
                        await asyncio.sleep(1)
                    else:
                        logger.error(f"Failed to calculate rating for beatmap {beatmap_id} after 10 attempts: {exc}")
                        return
                except HTTPError as exc:
                    wait = _retry_wait_seconds(exc)
                    if wait is not None:
                        logger.warning(
                            f"Rate limited while calculating rating for beatmap {beatmap_id}; "
                            f"waiting {wait:.1f}s before retry"
                        )
                        await asyncio.sleep(wait)
                        continue
                    attempts -= 1
                    if attempts > 0:
                        await asyncio.sleep(2)
                    else:
                        logger.exception(f"Failed to calculate rating for beatmap {beatmap_id} after multiple attempts")
                        return
                except Exception:
                    logger.exception(f"Unexpected error calculating rating for beatmap {beatmap_id}")
                    return

            new_rating = beatmap.difficulty_rating

            message = "Dry-run | beatmap {beatmap_id} | rating {old_rating:.2f} -> {new_rating:.2f}"
            success_message = "Recalculated beatmap {beatmap_id} | rating {old_rating:.2f} -> {new_rating:.2f}"

            if global_config.dry_run:
                await session.rollback()
                logger.info(
                    message.format(
                        beatmap_id=beatmap_id,
                        old_rating=old_rating,
                        new_rating=new_rating,
                    )
                )
            else:
                await session.commit()
                logger.success(
                    success_message.format(
                        beatmap_id=beatmap_id,
                        old_rating=old_rating,
                        new_rating=new_rating,
                    )
                )

            # Write to CSV if enabled
            if csv_writer:
                await csv_writer.write_rating(beatmap_id, old_rating, new_rating)
        except Exception:
            if session.in_transaction():
                await session.rollback()
            logger.exception(f"Failed to process beatmap {beatmap_id}")


async def recalculate_performance(
    config: PerformanceConfig,
    global_config: GlobalConfig,
) -> None:
    """Execute performance recalculation."""
    fetcher = await get_fetcher()
    redis = get_redis()

    init_mods()
    init_ranked_mods()
    await init_calculator()

    targets = await determine_targets(config)
    if not targets:
        logger.info("No targets matched the provided filters; nothing to recalculate")
        return

    scope = "full" if config.recalculate_all else "filtered"
    logger.info(
        "Recalculating performance for {} user/mode pairs ({}) | dry-run={} | concurrency={}",
        len(targets),
        scope,
        global_config.dry_run,
        global_config.concurrency,
    )

    async with CSVWriter(global_config.output_csv) as csv_writer:
        semaphore = asyncio.Semaphore(global_config.concurrency)
        coroutines = [
            recalculate_user_mode_performance(
                user_id, mode, score_ids, global_config, fetcher, redis, semaphore, csv_writer
            )
            for (user_id, mode), score_ids in targets.items()
        ]
        await run_in_batches(coroutines, global_config.concurrency)


async def recalculate_leaderboard(
    config: LeaderboardConfig,
    global_config: GlobalConfig,
) -> None:
    """Execute leaderboard recalculation."""
    targets = await determine_targets(config)
    if not targets:
        logger.info("No targets matched the provided filters; nothing to recalculate")
        return

    scope = "full" if config.recalculate_all else "filtered"
    logger.info(
        "Recalculating leaderboard for {} user/mode pairs ({}) | dry-run={} | concurrency={}",
        len(targets),
        scope,
        global_config.dry_run,
        global_config.concurrency,
    )

    async with CSVWriter(global_config.output_csv) as csv_writer:
        semaphore = asyncio.Semaphore(global_config.concurrency)
        coroutines = [
            recalculate_user_mode_leaderboard(user_id, mode, score_ids, global_config, semaphore, csv_writer)
            for (user_id, mode), score_ids in targets.items()
        ]
        await run_in_batches(coroutines, global_config.concurrency)


async def recalculate_rating(
    config: RatingConfig,
    global_config: GlobalConfig,
) -> None:
    """Execute beatmap rating recalculation."""
    fetcher = await get_fetcher()
    redis = get_redis()

    await init_calculator()

    # Determine beatmaps to recalculate
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        stmt = select(Beatmap.id)
        if not config.recalculate_all:
            if config.beatmap_ids:
                stmt = stmt.where(col(Beatmap.id).in_(list(config.beatmap_ids)))
            if config.beatmapset_ids:
                stmt = stmt.where(col(Beatmap.beatmapset_id).in_(list(config.beatmapset_ids)))
            if config.modes:
                stmt = stmt.where(col(Beatmap.mode).in_(list(config.modes)))

        result = await session.exec(stmt)
        beatmap_ids = list(result)

    if not beatmap_ids:
        logger.info("No beatmaps matched the provided filters; nothing to recalculate")
        return

    scope = "full" if config.recalculate_all else "filtered"
    logger.info(
        "Recalculating rating for {} beatmaps ({}) | dry-run={} | concurrency={}",
        len(beatmap_ids),
        scope,
        global_config.dry_run,
        global_config.concurrency,
    )

    async with CSVWriter(global_config.output_csv) as csv_writer:
        semaphore = asyncio.Semaphore(global_config.concurrency)
        coroutines = [
            recalculate_beatmap_rating(beatmap_id, global_config, fetcher, redis, semaphore, csv_writer)
            for beatmap_id in beatmap_ids
        ]
        await run_in_batches(coroutines, global_config.concurrency)


def _get_csv_path_for_subcommand(base_path: str | None, subcommand: str) -> str | None:
    """Generate a CSV path with subcommand name inserted before extension."""
    if base_path is None:
        return None

    path = Path(base_path)
    # Insert subcommand name before the extension
    # e.g., "results.csv" -> "results.performance.csv"
    new_name = f"{path.stem}.{subcommand}{path.suffix}"
    return str(path.parent / new_name)


async def main() -> None:
    """Main entry point."""
    command, global_config, sub_config = parse_cli_args(sys.argv[1:])

    if command == "all":
        logger.info("Executing all recalculations (performance, leaderboard, rating) with --all")

        # Rating
        rating_config = RatingConfig(
            modes=set(),
            beatmap_ids=set(),
            beatmapset_ids=set(),
            recalculate_all=True,
        )
        rating_csv_path = _get_csv_path_for_subcommand(global_config.output_csv, "rating")
        rating_global_config = GlobalConfig(
            dry_run=global_config.dry_run,
            concurrency=global_config.concurrency,
            output_csv=rating_csv_path,
        )
        await recalculate_rating(rating_config, rating_global_config)

        # Performance
        perf_config = PerformanceConfig(
            user_ids=set(),
            modes=set(),
            mods=set(),
            beatmap_ids=set(),
            beatmapset_ids=set(),
            recalculate_all=True,
        )
        perf_csv_path = _get_csv_path_for_subcommand(global_config.output_csv, "performance")
        perf_global_config = GlobalConfig(
            dry_run=global_config.dry_run,
            concurrency=global_config.concurrency,
            output_csv=perf_csv_path,
        )
        await recalculate_performance(perf_config, perf_global_config)

        # Leaderboard
        lead_config = LeaderboardConfig(
            user_ids=set(),
            modes=set(),
            mods=set(),
            beatmap_ids=set(),
            beatmapset_ids=set(),
            recalculate_all=True,
        )
        lead_csv_path = _get_csv_path_for_subcommand(global_config.output_csv, "leaderboard")
        lead_global_config = GlobalConfig(
            dry_run=global_config.dry_run,
            concurrency=global_config.concurrency,
            output_csv=lead_csv_path,
        )
        await recalculate_leaderboard(lead_config, lead_global_config)

    elif command == "performance":
        assert isinstance(sub_config, PerformanceConfig)
        await recalculate_performance(sub_config, global_config)
    elif command == "leaderboard":
        assert isinstance(sub_config, LeaderboardConfig)
        await recalculate_leaderboard(sub_config, global_config)
    elif command == "rating":
        assert isinstance(sub_config, RatingConfig)
        await recalculate_rating(sub_config, global_config)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
