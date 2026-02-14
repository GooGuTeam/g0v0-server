"""Performance and difficulty calculation module.

This module provides functionality for calculating performance points (PP),
difficulty ratings, score conversions, and beatmap validation. It integrates
with pluggable performance calculator backends and includes suspicious beatmap
detection.
"""

import asyncio
from enum import Enum
import importlib
import math
from typing import TYPE_CHECKING

from app.calculators.performance import PerformanceCalculator
from app.config import settings
from app.const import MAX_SCORE
from app.log import log
from app.models.score import GameMode, HitResult, ScoreStatistics
from app.models.scoring_mode import ScoringMode

from osupyparser import HitObject, OsuFile
from osupyparser.osu.objects import Slider
from redis.asyncio import Redis
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.database.score import Score
    from app.fetcher import Fetcher


logger = log("Calculator")

CALCULATOR: PerformanceCalculator | None = None


async def init_calculator() -> PerformanceCalculator | None:
    """Initialize the performance calculator.

    Dynamically imports and initializes the configured performance calculator
    backend from app.calculators.performance.

    Returns:
        The initialized PerformanceCalculator, or None if initialization fails.

    Raises:
        ImportError: If the calculator module cannot be imported.
    """
    global CALCULATOR
    try:
        module = importlib.import_module(f"app.calculators.performance.{settings.calculator}")
        CALCULATOR = module.PerformanceCalculator(**settings.calculator_config)
        if CALCULATOR is not None:
            await CALCULATOR.init()
    except (ImportError, AttributeError) as e:
        raise ImportError(f"Failed to import performance calculator for {settings.calculator}") from e
    return CALCULATOR


def get_calculator() -> PerformanceCalculator:
    """Get the initialized performance calculator.

    Returns:
        The PerformanceCalculator instance.

    Raises:
        RuntimeError: If the calculator has not been initialized.
    """
    if CALCULATOR is None:
        raise RuntimeError("Performance calculator is not initialized")
    return CALCULATOR


def clamp[T: int | float](n: T, min_value: T, max_value: T) -> T:
    """Clamp a value between minimum and maximum bounds.

    Args:
        n: The value to clamp.
        min_value: The minimum allowed value.
        max_value: The maximum allowed value.

    Returns:
        The clamped value.
    """
    if n < min_value:
        return min_value
    elif n > max_value:
        return max_value
    else:
        return n


def get_display_score(ruleset_id: int, total_score: int, mode: ScoringMode, maximum_statistics: ScoreStatistics) -> int:
    """
    Calculate the display score based on the scoring mode.

    Args:
        ruleset_id: The ruleset ID (0=osu!, 1=taiko, 2=catch, 3=mania)
        total_score: The standardised total score
        mode: The scoring mode (standardised or classic)
        maximum_statistics: Dictionary of maximum statistics for the score

    Returns:
        The display score in the requested scoring mode

    Reference:
        - https://github.com/ppy/osu/blob/master/osu.Game/Scoring/Legacy/ScoreInfoExtensions.cs
    """
    if mode == ScoringMode.STANDARDISED:
        return total_score

    # Calculate max basic judgements
    max_basic_judgements = sum(
        count for hit_result, count in maximum_statistics.items() if HitResult(hit_result).is_basic()
    )

    return _convert_standardised_to_classic(ruleset_id, total_score, max_basic_judgements)


def _convert_standardised_to_classic(ruleset_id: int, standardised_total_score: int, object_count: int) -> int:
    """
    Convert a standardised score to classic score.

    The coefficients were determined by a least-squares fit to minimise relative error
    of maximum possible base score across all beatmaps.

    Args:
        ruleset_id: The ruleset ID (0=osu!, 1=taiko, 2=catch, 3=mania)
        standardised_total_score: The standardised total score
        object_count: The number of basic hit objects

    Returns:
        The classic score
    """
    if ruleset_id == 0:  # osu!
        return round((object_count**2 * 32.57 + 100000) * standardised_total_score / MAX_SCORE)
    elif ruleset_id == 1:  # taiko
        return round((object_count * 1109 + 100000) * standardised_total_score / MAX_SCORE)
    elif ruleset_id == 2:  # catch
        return round((standardised_total_score / MAX_SCORE * object_count) ** 2 * 21.62 + standardised_total_score / 10)
    else:  # mania (ruleset_id == 3) or default
        return standardised_total_score


def calculate_pp_for_no_calculator(score: "Score", star_rating: float) -> float:
    """Calculate PP using fallback algorithm when no calculator is available.

    Uses a custom exponential reward formula based on score and star rating.
    See: https://www.desmos.com/calculator/i2aa7qm3o6

    Args:
        score: The score object.
        star_rating: The beatmap star rating.

    Returns:
        The calculated PP value.
    """
    # TODO: Improve this algorithm
    k = 4.0

    pmax = 1.4 * (star_rating**2.8)
    b = 0.95 - 0.33 * ((clamp(star_rating, 1, 8) - 1) / 7)

    x = score.total_score / 1000000

    if x < b:
        # Linear section
        return pmax * x
    else:
        # Exponential reward section
        x = (x - b) / (1 - b)
        exp_part = (math.exp(k * x) - 1) / (math.exp(k) - 1)
        return pmax * (b + (1 - b) * exp_part)


async def calculate_pp(score: "Score", beatmap: str, session: AsyncSession) -> float:
    """Calculate performance points for a score.

    Checks for banned/suspicious beatmaps and uses the configured
    performance calculator backend.

    Args:
        score: The score object.
        beatmap: The beatmap file content as a string.
        session: The database session.

    Returns:
        The calculated PP value, or 0 if the beatmap is banned/suspicious.
    """
    from app.database.beatmap import BannedBeatmaps

    if settings.suspicious_score_check:
        beatmap_banned = (
            await session.exec(select(exists()).where(col(BannedBeatmaps.beatmap_id) == score.beatmap_id))
        ).first()
        if beatmap_banned:
            return 0
        try:
            is_suspicious = is_suspicious_beatmap(beatmap)
            if is_suspicious:
                session.add(BannedBeatmaps(beatmap_id=score.beatmap_id))
                logger.warning(f"Beatmap {score.beatmap_id} is suspicious, banned")
                return 0
        except Exception:
            logger.exception(f"Error checking if beatmap {score.beatmap_id} is suspicious")

    if not (await get_calculator().can_calculate_performance(score.gamemode)):
        if not settings.fallback_no_calculator_pp:
            return 0
        star_rating = -1
        if await get_calculator().can_calculate_difficulty(score.gamemode):
            star_rating = (await get_calculator().calculate_difficulty(beatmap, score.mods, score.gamemode)).star_rating
        if star_rating < 0:
            star_rating = (await score.awaitable_attrs.beatmap).difficulty_rating
        pp = calculate_pp_for_no_calculator(score, star_rating)
    else:
        attrs = await get_calculator().calculate_performance(beatmap, score)
        pp = attrs.pp

    if settings.suspicious_score_check and (pp > 3000):
        logger.warning(
            f"User {score.user_id} played {score.beatmap_id} "
            f"with {pp=} "
            f"acc={score.accuracy}. The score is suspicious and return 0pp"
            f"({score.id=})"
        )
        return 0
    return pp


async def pre_fetch_and_calculate_pp(
    score: "Score", session: AsyncSession, redis: Redis, fetcher: "Fetcher"
) -> tuple[float, bool]:
    """Optimized PP calculation with pre-fetching and caching.

    Performs beatmap fetching and PP calculation with Redis caching support.

    Args:
        score: The score object.
        session: The database session.
        redis: The Redis client.
        fetcher: The fetcher instance.

    Returns:
        A tuple of (pp_value, success). Success is False only if fetching fails.
    """
    from app.database.beatmap import BannedBeatmaps

    beatmap_id = score.beatmap_id

    # Quick check if beatmap is banned
    if settings.suspicious_score_check:
        beatmap_banned = (
            await session.exec(select(exists()).where(col(BannedBeatmaps.beatmap_id) == beatmap_id))
        ).first()
        if beatmap_banned:
            return 0, False

    # Async fetch beatmap raw file, using existing Redis cache mechanism
    try:
        beatmap_raw = await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
    except Exception as e:
        logger.error(f"Failed to fetch beatmap {beatmap_id}: {e}")
        return 0, False

    # While fetching file, can also check suspicious beatmap
    if settings.suspicious_score_check:
        try:
            # Move suspicious check to thread pool
            def _check_suspicious():
                return is_suspicious_beatmap(beatmap_raw)

            loop = asyncio.get_event_loop()
            is_sus = await loop.run_in_executor(None, _check_suspicious)
            if is_sus:
                session.add(BannedBeatmaps(beatmap_id=beatmap_id))
                logger.warning(f"Beatmap {beatmap_id} is suspicious, banned")
                return 0, True
        except Exception:
            logger.exception(f"Error checking if beatmap {beatmap_id} is suspicious")

    # Call optimized PP calculation function
    return await calculate_pp(score, beatmap_raw, session), True


def calculate_level_to_score(n: int) -> float:
    """Calculate the total score required to reach a given level.

    Args:
        n: The target level.

    Returns:
        The total score required.

    Reference:
        - https://osu.ppy.sh/wiki/Gameplay/Score/Total_score
    """
    if n <= 100:
        return 5000 / 3 * (4 * n**3 - 3 * n**2 - n) + 1.25 * 1.8 ** (n - 60)
    else:
        return 26931190827 + 99999999999 * (n - 100)


def calculate_score_to_level(total_score: int) -> float:
    """Calculate the level for a given total score.

    Args:
        total_score: The total score.

    Returns:
        The calculated level (including decimal progress).

    Reference:
        - https://github.com/ppy/osu-queue-score-statistics/blob/4bdd479530408de73f3cdd95e097fe126772a65b/osu.Server.Queues.ScoreStatisticsProcessor/Processors/TotalScoreProcessor.cs#L70-L116
    """
    to_next_level = [
        30000,
        100000,
        210000,
        360000,
        550000,
        780000,
        1050000,
        1360000,
        1710000,
        2100000,
        2530000,
        3000000,
        3510000,
        4060000,
        4650000,
        5280000,
        5950000,
        6660000,
        7410000,
        8200000,
        9030000,
        9900000,
        10810000,
        11760000,
        12750000,
        13780000,
        14850000,
        15960000,
        17110000,
        18300000,
        19530000,
        20800000,
        22110000,
        23460000,
        24850000,
        26280000,
        27750000,
        29260000,
        30810000,
        32400000,
        34030000,
        35700000,
        37410000,
        39160000,
        40950000,
        42780000,
        44650000,
        46560000,
        48510000,
        50500000,
        52530000,
        54600000,
        56710000,
        58860000,
        61050000,
        63280000,
        65550000,
        67860000,
        70210001,
        72600001,
        75030002,
        77500003,
        80010006,
        82560010,
        85150019,
        87780034,
        90450061,
        93160110,
        95910198,
        98700357,
        101530643,
        104401157,
        107312082,
        110263748,
        113256747,
        116292144,
        119371859,
        122499346,
        125680824,
        128927482,
        132259468,
        135713043,
        139353477,
        143298259,
        147758866,
        153115959,
        160054726,
        169808506,
        184597311,
        208417160,
        248460887,
        317675597,
        439366075,
        655480935,
        1041527682,
        1733419828,
        2975801691,
        5209033044,
        9225761479,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
        99999999999,
    ]

    remaining_score = total_score
    level = 0.0

    while remaining_score > 0:
        next_level_requirement = to_next_level[min(len(to_next_level) - 1, round(level))]
        level += min(1, remaining_score / next_level_requirement)
        remaining_score -= next_level_requirement

    return level + 1


def calculate_pp_weight(index: int) -> float:
    """Calculate PP weighting factor for a score at given index.

    Based on: https://osu.ppy.sh/wiki/Performance_points/Weighting_system

    Args:
        index: The 0-based index in the sorted scores list.

    Returns:
        The weight factor (0.95^index).
    """
    return math.pow(0.95, index)


def calculate_weighted_pp(pp: float, index: int) -> float:
    """Calculate weighted PP value for a score.

    Args:
        pp: The raw PP value.
        index: The 0-based index in the sorted scores list.

    Returns:
        The weighted PP value.
    """
    return calculate_pp_weight(index) * pp if pp > 0 else 0.0


def calculate_weighted_acc(acc: float, index: int) -> float:
    """Calculate weighted accuracy for a score.

    Args:
        acc: The accuracy value.
        index: The 0-based index in the sorted scores list.

    Returns:
        The weighted accuracy value.
    """
    return calculate_pp_weight(index) * acc if acc > 0 else 0.0


# Suspicious beatmap detection algorithm based on:
# https://github.com/MaxOhn/rosu-pp/blob/main/src/model/beatmap/suspicious.rs


class Threshold(int, Enum):
    """Threshold constants for suspicious beatmap detection."""

    # Beatmap abnormality constants
    NOTES_THRESHOLD = 500000  # Object count limit for non-taiko modes
    TAIKO_THRESHOLD = 30000  # Object count limit for taiko mode

    NOTES_PER_1S_THRESHOLD = 200  # 3000 BPM equivalent
    NOTES_PER_10S_THRESHOLD = 500  # 600 BPM equivalent

    # This is already 4x the normal play area size
    NOTE_POSX_THRESHOLD = 512  # x: [-512,512]
    NOTE_POSY_THRESHOLD = 384  # y: [-384,384]

    POS_ERROR_THRESHOLD = 1280 * 50  # Ban if this many objects (including slider control points) have position issues

    SLIDER_REPEAT_THRESHOLD = 5000


def too_dense(hit_objects: list[HitObject], per_1s: int, per_10s: int) -> bool:
    """Check if hit objects are too densely packed.

    Args:
        hit_objects: List of hit objects.
        per_1s: Maximum objects allowed per 1 second window.
        per_10s: Maximum objects allowed per 10 second window.

    Returns:
        True if the beatmap is too dense, False otherwise.
    """
    per_1s = max(1, per_1s)
    per_10s = max(1, per_10s)
    for i in range(0, len(hit_objects)):
        if len(hit_objects) > i + per_1s:
            if hit_objects[i + per_1s].start_time - hit_objects[i].start_time < 1000:
                return True
        elif len(hit_objects) > i + per_10s and hit_objects[i + per_10s].start_time - hit_objects[i].start_time < 10000:
            return True
    return False


def slider_is_sus(hit_objects: list[HitObject]) -> bool:
    """Check if sliders have suspicious properties.

    Args:
        hit_objects: List of hit objects.

    Returns:
        True if any slider is suspicious, False otherwise.
    """
    for obj in hit_objects:
        if isinstance(obj, Slider):
            flag_repeat = obj.repeat_count > Threshold.SLIDER_REPEAT_THRESHOLD
            flag_pos = int(
                obj.pos.x > Threshold.NOTE_POSX_THRESHOLD
                or obj.pos.x < 0
                or obj.pos.y > Threshold.NOTE_POSY_THRESHOLD
                or obj.pos.y < 0
            )
            for point in obj.points:
                flag_pos += int(
                    point.x > Threshold.NOTE_POSX_THRESHOLD
                    or point.x < 0
                    or point.y > Threshold.NOTE_POSY_THRESHOLD
                    or point.y < 0
                )
            if flag_pos or flag_repeat:
                return True
    return False


def is_2b(hit_objects: list[HitObject]) -> bool:
    """Check if beatmap contains overlapping (2B) hit objects.

    Args:
        hit_objects: List of hit objects.

    Returns:
        True if overlapping objects are detected, False otherwise.
    """
    return any(hit_objects[i] == hit_objects[i + 1].start_time for i in range(0, len(hit_objects) - 1))


def is_suspicious_beatmap(content: str) -> bool:
    """Check if a beatmap is suspicious.

    Analyzes beatmap content for abnormal properties like excessive
    object count, density, position errors, or 2B patterns.

    Args:
        content: The .osu beatmap file content.

    Returns:
        True if the beatmap is suspicious, False otherwise.
    """
    osufile = OsuFile(content=content.encode("utf-8")).parse_file()

    if osufile.hit_objects[-1].start_time - osufile.hit_objects[0].start_time > 24 * 60 * 60 * 1000:
        return True
    if osufile.mode == int(GameMode.TAIKO):
        if len(osufile.hit_objects) > Threshold.TAIKO_THRESHOLD:
            return True
    elif len(osufile.hit_objects) > Threshold.NOTES_THRESHOLD:
        return True
    match osufile.mode:
        case int(GameMode.OSU):
            return (
                too_dense(
                    osufile.hit_objects,
                    Threshold.NOTES_PER_1S_THRESHOLD,
                    Threshold.NOTES_PER_10S_THRESHOLD,
                )
                or slider_is_sus(osufile.hit_objects)
                or is_2b(osufile.hit_objects)
            )
        case int(GameMode.TAIKO):
            return too_dense(
                osufile.hit_objects,
                Threshold.NOTES_PER_1S_THRESHOLD * 2,
                Threshold.NOTES_PER_10S_THRESHOLD * 2,
            ) or is_2b(osufile.hit_objects)
        case int(GameMode.FRUITS):
            return slider_is_sus(osufile.hit_objects) or is_2b(osufile.hit_objects)
        case int(GameMode.MANIA):
            keys_per_hand = max(1, int(osufile.cs / 2))
            per_1s = Threshold.NOTES_PER_1S_THRESHOLD * keys_per_hand
            per_10s = Threshold.NOTES_PER_10S_THRESHOLD * keys_per_hand
            return too_dense(osufile.hit_objects, per_1s, per_10s)
    return False
