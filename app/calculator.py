from __future__ import annotations

import asyncio
from copy import deepcopy
from enum import Enum
import math
from typing import TYPE_CHECKING

from app.config import settings
from app.log import logger
from app.models.beatmap import BeatmapAttributes
from app.models.beatmap.mods import APIMod, parse_enum_to_str
from app.models.score import GameMode

from osupyparser import HitObject, OsuFile
from osupyparser.osu.objects import Slider
from redis.asyncio import Redis
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession

try:
    import rosu_pp_py as rosu
except ImportError:
    raise ImportError(
        "rosu-pp-py is not installed. "
        "Please install it.\n"
        "   Official: uv add rosu-pp-py\n"
        "   ppy-sb: uv add git+https://github.com/ppy-sb/rosu-pp-py.git"
    )

if TYPE_CHECKING:
    from app.database.score import Score
    from app.fetcher import Fetcher


def clamp[T: int | float](n: T, min_value: T, max_value: T) -> T:
    if n < min_value:
        return min_value
    elif n > max_value:
        return max_value
    else:
        return n


def calculate_beatmap_attribute(
    beatmap: str,
    gamemode: GameMode | None = None,
    mods: int | list[APIMod] | list[str] = 0,
) -> BeatmapAttributes:
    map = rosu.Beatmap(content=beatmap)
    if gamemode is not None:
        map.convert(gamemode.to_rosu(), mods)  # pyright: ignore[reportArgumentType]
    diff = rosu.Difficulty(mods=mods).calculate(map)
    return BeatmapAttributes(
        star_rating=diff.stars,
        max_combo=diff.max_combo,
        aim_difficulty=diff.aim,
        aim_difficult_slider_count=diff.aim_difficult_slider_count,
        speed_difficulty=diff.speed,
        speed_note_count=diff.speed_note_count,
        slider_factor=diff.slider_factor,
        aim_difficult_strain_count=diff.aim_difficult_strain_count,
        speed_difficult_strain_count=diff.speed_difficult_strain_count,
        mono_stamina_factor=diff.stamina,
    )


async def calculate_pp(score: "Score", beatmap: str, session: AsyncSession) -> float:
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

    # 使用线程池执行计算密集型操作以避免阻塞事件循环

    loop = asyncio.get_event_loop()

    def _calculate_pp_sync():
        map = rosu.Beatmap(content=beatmap)
        mods = deepcopy(score.mods.copy())
        parse_enum_to_str(int(score.gamemode), mods)
        map.convert(score.gamemode.to_rosu(), mods)  # pyright: ignore[reportArgumentType]
        perf = rosu.Performance(
            mods=mods,
            lazer=True,
            accuracy=clamp(score.accuracy * 100, 0, 100),
            combo=score.max_combo,
            large_tick_hits=score.nlarge_tick_hit or 0,
            slider_end_hits=score.nslider_tail_hit or 0,
            small_tick_hits=score.nsmall_tick_hit or 0,
            n_geki=score.ngeki,
            n_katu=score.nkatu,
            n300=score.n300,
            n100=score.n100,
            n50=score.n50,
            misses=score.nmiss,
        )
        return perf.calculate(map)

    # 在线程池中执行计算
    attrs = await loop.run_in_executor(None, _calculate_pp_sync)
    pp = attrs.pp

    # mrekk bp1: 2048pp; ppy-sb top1 rxbp1: 2198pp
    if settings.suspicious_score_check and ((attrs.difficulty.stars > 25 and score.accuracy < 0.8) or pp > 3000):
        logger.warning(
            f"User {score.user_id} played {score.beatmap_id} "
            f"(star={attrs.difficulty.stars}) with {pp=} "
            f"acc={score.accuracy}. The score is suspicious and return 0pp"
            f"({score.id=})"
        )
        return 0
    return pp


async def pre_fetch_and_calculate_pp(
    score: "Score", session: AsyncSession, redis: Redis, fetcher: "Fetcher"
) -> tuple[float, bool]:
    """
    优化版PP计算：预先获取beatmap文件并使用缓存
    """
    from app.database.beatmap import BannedBeatmaps

    beatmap_id = score.beatmap_id

    # 快速检查是否被封禁
    if settings.suspicious_score_check:
        beatmap_banned = (
            await session.exec(select(exists()).where(col(BannedBeatmaps.beatmap_id) == beatmap_id))
        ).first()
        if beatmap_banned:
            return 0, False

    # 异步获取beatmap原始文件，利用已有的Redis缓存机制
    try:
        beatmap_raw = await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
    except Exception as e:
        logger.error(f"Failed to fetch beatmap {beatmap_id}: {e}")
        return 0, False

    # 在获取文件的同时，可以检查可疑beatmap
    if settings.suspicious_score_check:
        try:
            # 将可疑检查也移到线程池中执行
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

    # 调用已优化的PP计算函数
    return await calculate_pp(score, beatmap_raw, session), True


async def batch_calculate_pp(
    scores_data: list[tuple["Score", int]], session: AsyncSession, redis, fetcher
) -> list[float]:
    """
    批量计算PP：适用于重新计算或批量处理场景
    Args:
        scores_data: [(score, beatmap_id), ...] 的列表
    Returns:
        对应的PP值列表
    """
    import asyncio

    from app.database.beatmap import BannedBeatmaps

    if not scores_data:
        return []

    # 提取所有唯一的beatmap_id
    unique_beatmap_ids = list({beatmap_id for _, beatmap_id in scores_data})

    # 批量检查被封禁的beatmap
    banned_beatmaps = set()
    if settings.suspicious_score_check:
        banned_results = await session.exec(
            select(BannedBeatmaps.beatmap_id).where(col(BannedBeatmaps.beatmap_id).in_(unique_beatmap_ids))
        )
        banned_beatmaps = set(banned_results.all())

    # 并发获取所有需要的beatmap原始文件
    async def fetch_beatmap_safe(beatmap_id: int) -> tuple[int, str | None]:
        if beatmap_id in banned_beatmaps:
            return beatmap_id, None
        try:
            content = await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
            return beatmap_id, content
        except Exception as e:
            logger.error(f"Failed to fetch beatmap {beatmap_id}: {e}")
            return beatmap_id, None

    # 并发获取所有beatmap文件
    fetch_tasks = [fetch_beatmap_safe(bid) for bid in unique_beatmap_ids]
    fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    # 构建beatmap_id -> content的映射
    beatmap_contents = {}
    for result in fetch_results:
        if isinstance(result, tuple):
            beatmap_id, content = result
            beatmap_contents[beatmap_id] = content

    # 为每个score计算PP
    pp_results = []
    for score, beatmap_id in scores_data:
        beatmap_content = beatmap_contents.get(beatmap_id)
        if beatmap_content is None:
            pp_results.append(0.0)
            continue

        try:
            pp = await calculate_pp(score, beatmap_content, session)
            pp_results.append(pp)
        except Exception as e:
            logger.error(f"Failed to calculate PP for score {score.id}: {e}")
            pp_results.append(0.0)

    return pp_results


# https://osu.ppy.sh/wiki/Gameplay/Score/Total_score
def calculate_level_to_score(n: int) -> float:
    if n <= 100:
        return 5000 / 3 * (4 * n**3 - 3 * n**2 - n) + 1.25 * 1.8 ** (n - 60)
    else:
        return 26931190827 + 99999999999 * (n - 100)


# https://github.com/ppy/osu-queue-score-statistics/blob/4bdd479530408de73f3cdd95e097fe126772a65b/osu.Server.Queues.ScoreStatisticsProcessor/Processors/TotalScoreProcessor.cs#L70-L116
def calculate_score_to_level(total_score: int) -> float:
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


# https://osu.ppy.sh/wiki/Performance_points/Weighting_system
def calculate_pp_weight(index: int) -> float:
    return math.pow(0.95, index)


def calculate_weighted_pp(pp: float, index: int) -> float:
    return calculate_pp_weight(index) * pp if pp > 0 else 0.0


def calculate_weighted_acc(acc: float, index: int) -> float:
    return calculate_pp_weight(index) * acc if acc > 0 else 0.0


# 大致算法来自 https://github.com/MaxOhn/rosu-pp/blob/main/src/model/beatmap/suspicious.rs


class Threshold(int, Enum):
    # 谱面异常常量
    NOTES_THRESHOLD = 500000  # 除 taiko 以外任何模式的物件数量
    TAIKO_THRESHOLD = 30000  # taiko 模式下的物量限制

    NOTES_PER_1S_THRESHOLD = 200  # 3000 BPM
    NOTES_PER_10S_THRESHOLD = 500  # 600 BPM

    # 这个尺寸已经是常规游玩区域大小的 4 倍了 …… 如果不合适那另说吧
    NOTE_POSX_THRESHOLD = 512  # x: [-512,512]
    NOTE_POSY_THRESHOLD = 384  # y: [-384,384]

    POS_ERROR_THRESHOLD = 1280 * 50  # 超过这么多个物件（包括滑条控制点）的位置有问题就毙掉

    SLIDER_REPEAT_THRESHOLD = 5000


def too_dense(hit_objects: list[HitObject], per_1s: int, per_10s: int) -> bool:
    per_1s = max(1, per_1s)
    per_10s = max(1, per_10s)
    for i in range(0, len(hit_objects)):
        if len(hit_objects) > i + per_1s:
            if hit_objects[i + per_1s].start_time - hit_objects[i].start_time < 1000:
                return True
        elif len(hit_objects) > i + per_10s:
            if hit_objects[i + per_10s].start_time - hit_objects[i].start_time < 10000:
                return True
    return False


def slider_is_sus(hit_objects: list[HitObject]) -> bool:
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
    for i in range(0, len(hit_objects) - 1):
        if hit_objects[i] == hit_objects[i + 1].start_time:
            return True
    return False


def is_suspicious_beatmap(content: str) -> bool:
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
