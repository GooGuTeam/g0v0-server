from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Sequence
import contextlib
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
import sys
import warnings

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.calculating import CalculateError, calculate_pp, calculate_score_to_level, init_calculator
from app.config import settings
from app.const import BANCHOBOT_ID
from app.database import TotalScoreBestScore, UserStatistics
from app.database.beatmap import Beatmap, calculate_beatmap_attributes, clear_cached_beatmap_raws
from app.database.best_scores import BestScore
from app.database.score import Score, calculate_playtime, calculate_user_pp
from app.dependencies.database import engine, get_redis
from app.dependencies.fetcher import get_fetcher
from app.fetcher import Fetcher
from app.fetcher.beatmap_raw import NoBeatmapError
from app.log import log
from app.models.mods import init_mods, init_ranked_mods, mod_to_save, mods_can_get_pp
from app.models.score import GameMode, Rank
from app.plugins import manager

from httpx import HTTPError
from redis.asyncio import Redis
from sqlalchemy.orm import joinedload
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = log("Recalculate")

warnings.filterwarnings("ignore")


class BeatmapCacheManager:
    """管理beatmap缓存，确保不超过指定数量

    优化：
    1. 将清理操作移到锁外，减少持锁时间
    2. 使用 LRU 策略，已存在的 beatmap 移到最后
    """

    def __init__(self, max_count: int, additional_count: int, redis: Redis):
        self.max_count = max_count
        self.additional_count = additional_count
        self.redis = redis
        self.beatmap_ids: list[int] = []  # 记录处理的beatmap id（按顺序）
        self.beatmap_id_set: set[int] = set()  # 用于快速查找（唯一性）
        self.lock = asyncio.Lock()

    async def add_beatmap(self, beatmap_id: int) -> None:
        """添加beatmap到缓存跟踪列表（LRU策略）"""
        if self.max_count <= 0:  # 不限制
            return

        to_remove: list[int] = []

        async with self.lock:
            # 如果已经存在，更新其位置（移到最后，表示最近使用）
            if beatmap_id in self.beatmap_id_set:
                with contextlib.suppress(ValueError):
                    self.beatmap_ids.remove(beatmap_id)
                self.beatmap_ids.append(beatmap_id)
                return

            self.beatmap_ids.append(beatmap_id)
            self.beatmap_id_set.add(beatmap_id)

            # 检查是否需要清理
            threshold = self.max_count + max(0, self.additional_count)
            if len(self.beatmap_ids) > threshold:
                # 计算需要删除的数量
                to_remove_count = max(1, self.additional_count)
                # 获取要删除的 beatmap ids（最旧的）
                to_remove = self.beatmap_ids[:to_remove_count]
                self.beatmap_ids = self.beatmap_ids[to_remove_count:]
                # 从 set 中移除
                for bid in to_remove:
                    self.beatmap_id_set.discard(bid)

        # 在锁外执行清理（避免阻塞其他协程）
        if to_remove:
            await self._cleanup_async(to_remove)

    async def _cleanup_async(self, to_remove: list[int]) -> None:
        """异步清理 beatmap 缓存（在锁外执行）"""
        if not to_remove:
            return

        try:
            # 从 Redis 中删除缓存
            await clear_cached_beatmap_raws(self.redis, to_remove)
            logger.info(f"Cleaned up {len(to_remove)} beatmap caches (remaining: {len(self.beatmap_ids)})")
        except Exception as e:
            logger.warning(f"Failed to cleanup {len(to_remove)} beatmap caches: {e}")

    def get_stats(self) -> dict:
        """获取统计信息"""
        threshold = self.max_count + max(0, self.additional_count) if self.max_count > 0 else "unlimited"
        return {
            "total_beatmaps": len(self.beatmap_ids),
            "max_count": self.max_count,
            "additional_count": self.additional_count,
            "threshold": threshold,
        }


@dataclass(frozen=True)
class GlobalConfig:
    dry_run: bool
    concurrency: int
    output_csv: str | None
    max_cached_beatmaps_count: int
    additional_count: int


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
    parser.add_argument(
        "--max-cached-beatmaps-count",
        dest="max_cached_beatmaps_count",
        type=int,
        default=1500,
        help="Maximum number of beatmaps to cache (<=0 means no limit)",
    )
    parser.add_argument(
        "--additional-count",
        dest="additional_count",
        type=int,
        default=100,
        help="Number of additional beatmaps before cleanup (<=0 means cleanup immediately)",
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
        max_cached_beatmaps_count=args.max_cached_beatmaps_count,
        additional_count=args.additional_count,
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
    cache_manager: BeatmapCacheManager | None = None,
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
            # 记录使用的beatmap
            if cache_manager:
                await cache_manager.add_beatmap(score.beatmap_id)
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
        except NoBeatmapError:
            logger.warning(f"Beatmap raw not found for beatmap {score.beatmap_id}; cannot calculate pp")
            return None
        except CalculateError as exc:
            attempts -= 1
            logger.warning(
                f"Calculation error for score {score.id} on "
                f"beatmap {score.beatmap_id}: {exc}; attempts left: {attempts}"
            )
            await asyncio.sleep(2)
            continue
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
    cache_manager: BeatmapCacheManager | None = None,
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
                result_pp = await recalc_score_pp(session, fetcher, redis, score, cache_manager)
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
            previous_data = {
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
                "ranked_score": statistics.ranked_score - previous_data["ranked_score"],
                "maximum_combo": statistics.maximum_combo - previous_data["maximum_combo"],
                "grade_ss": statistics.grade_ss - previous_data["grade_ss"],
                "grade_ssh": statistics.grade_ssh - previous_data["grade_ssh"],
                "grade_s": statistics.grade_s - previous_data["grade_s"],
                "grade_sh": statistics.grade_sh - previous_data["grade_sh"],
                "grade_a": statistics.grade_a - previous_data["grade_a"],
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
    cache_manager: BeatmapCacheManager | None = None,
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
                    # 添加整体超时保护（30秒），防止单个请求卡死
                    try:
                        attributes = await asyncio.wait_for(
                            calculate_beatmap_attributes(beatmap_id, ruleset, [], redis, fetcher), timeout=30.0
                        )
                    except TimeoutError:
                        logger.error(f"Timeout calculating attributes for beatmap {beatmap_id} after 30s")
                        return

                    # 记录使用的beatmap
                    if cache_manager:
                        await cache_manager.add_beatmap(beatmap_id)
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
                except NoBeatmapError:
                    logger.error(f"Beatmap data for {beatmap_id} not found; cannot calculate rating")
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
    manager.load_all_plugins()
    await init_calculator()

    targets = await determine_targets(config)
    if not targets:
        logger.info("No targets matched the provided filters; nothing to recalculate")
        return

    # 创建缓存管理器
    cache_manager = BeatmapCacheManager(
        max_count=global_config.max_cached_beatmaps_count,
        additional_count=global_config.additional_count,
        redis=redis,
    )
    logger.info(f"Beatmap cache manager initialized: {cache_manager.get_stats()}")

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
                user_id, mode, score_ids, global_config, fetcher, redis, semaphore, cache_manager, csv_writer
            )
            for (user_id, mode), score_ids in targets.items()
        ]
        await run_in_batches(coroutines, global_config.concurrency)

    # 显示最终统计
    logger.info(f"Beatmap cache final stats: {cache_manager.get_stats()}")


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

    manager.load_all_plugins()
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

    # 创建缓存管理器
    cache_manager = BeatmapCacheManager(
        max_count=global_config.max_cached_beatmaps_count,
        additional_count=global_config.additional_count,
        redis=redis,
    )
    logger.info(f"Beatmap cache manager initialized: {cache_manager.get_stats()}")

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
            recalculate_beatmap_rating(beatmap_id, global_config, fetcher, redis, semaphore, cache_manager, csv_writer)
            for beatmap_id in beatmap_ids
        ]
        await run_in_batches(coroutines, global_config.concurrency)

    # 显示最终统计
    logger.info(f"Beatmap cache final stats: {cache_manager.get_stats()}")


def _get_csv_path_for_subcommand(base_path: str | None, subcommand: str) -> str | None:
    """Generate a CSV path with subcommand name inserted before extension."""
    if base_path is None:
        return None

    path = Path(base_path)
    # Insert subcommand name before the extension
    # e.g., "results.csv" -> "results.performance.csv"
    new_name = f"{path.stem}.{subcommand}{path.suffix}"
    if path.parent == Path("."):
        return new_name
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
            max_cached_beatmaps_count=global_config.max_cached_beatmaps_count,
            additional_count=global_config.additional_count,
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
            max_cached_beatmaps_count=global_config.max_cached_beatmaps_count,
            additional_count=global_config.additional_count,
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
            max_cached_beatmaps_count=global_config.max_cached_beatmaps_count,
            additional_count=global_config.additional_count,
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
