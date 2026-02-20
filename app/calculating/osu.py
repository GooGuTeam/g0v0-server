import math
from typing import TYPE_CHECKING

from app.config import settings
from app.const import MAX_SCORE
from app.log import log
from app.models.events.calculating import AfterCalculatingPPEvent, BeforeCalculatingPPEvent
from app.models.score import HitResult, ScoreData, ScoreStatistics
from app.models.scoring_mode import ScoringMode
from app.plugins import hub

from .calculators import get_calculator
from .math import clamp

from redis.asyncio import Redis
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.database import Score
    from app.fetcher import Fetcher

logger = log("Calculator")


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


def calculate_pp_for_no_calculator(score: ScoreData, star_rating: float) -> float:
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


async def calculate_pp(score: "ScoreData | Score", beatmap: str, session: AsyncSession) -> float:
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
    from app.database import Beatmap

    if not isinstance(score, ScoreData):
        score = ScoreData.from_score(score)

    db_beatmap = await session.get(Beatmap, score.beatmap_id)
    if db_beatmap is None:
        logger.error(f"Beatmap {score.beatmap_id} not found in database for PP calculation")
        return 0

    hub.emit(BeforeCalculatingPPEvent(score=score, beatmap_raw=beatmap))

    if not (await get_calculator().can_calculate_performance(score.gamemode)):
        if not settings.fallback_no_calculator_pp:
            return 0
        star_rating = -1
        if await get_calculator().can_calculate_difficulty(score.gamemode):
            star_rating = (await get_calculator().calculate_difficulty(beatmap, score.mods, score.gamemode)).star_rating
        if star_rating < 0:
            star_rating = db_beatmap.difficulty_rating
        pp = calculate_pp_for_no_calculator(score, star_rating)
    else:
        attrs = await get_calculator().calculate_performance(beatmap, score)
        pp = attrs.pp
        hub.emit(AfterCalculatingPPEvent(score=score, beatmap_raw=beatmap, performance_attribute=attrs))

    return pp


async def pre_fetch_and_calculate_pp(
    score: "ScoreData | Score", session: AsyncSession, redis: Redis, fetcher: "Fetcher"
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
    if not isinstance(score, ScoreData):
        score = ScoreData.from_score(score)

    beatmap_id = score.beatmap_id
    try:
        beatmap_raw = await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
    except Exception as e:
        logger.error(f"Failed to fetch beatmap {beatmap_id}: {e}")
        return 0, False

    return await calculate_pp(score, beatmap_raw, session), True
