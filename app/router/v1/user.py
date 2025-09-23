from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.database import User, UserStatistics, UserStatisticsResp
from app.dependencies.database import Database, get_redis
from app.log import logger
from app.models.score import GameMode
from app.models.user.v1_user import (
    PlayerEventItem,
    PlayerInfo,
    PlayerModeStats,
    PlayerStatsHistory,
)
from app.service.cache.user_cache_service import get_user_cache_service

from .router import AllStrModel, router

from fastapi import BackgroundTasks, HTTPException, Query
from sqlmodel import select


class V1User(AllStrModel):
    user_id: int
    username: str
    join_date: datetime
    count300: int
    count100: int
    count50: int
    playcount: int
    ranked_score: int
    total_score: int
    pp_rank: int
    level: float
    pp_raw: float
    accuracy: float
    count_rank_ss: int
    count_rank_ssh: int
    count_rank_s: int
    count_rank_sh: int
    count_rank_a: int
    country: str
    total_seconds_played: int
    pp_country_rank: int
    events: list[dict]

    @classmethod
    def _get_cache_key(cls, user_id: int, ruleset: GameMode | None = None) -> str:
        """生成 V1 用户缓存键"""
        if ruleset:
            return f"v1_user:{user_id}:ruleset:{ruleset}"
        return f"v1_user:{user_id}"

    @classmethod
    async def from_db(cls, session: Database, db_user: User, ruleset: GameMode | None = None) -> "V1User":
        # 确保 user_id 不为 None
        if db_user.id is None:
            raise ValueError("User ID cannot be None")

        ruleset = ruleset or db_user.playmode
        current_statistics: UserStatistics | None = None
        for i in await db_user.awaitable_attrs.statistics:
            if i.mode == ruleset:
                current_statistics = i
                break
        if current_statistics:
            statistics = await UserStatisticsResp.from_db(current_statistics, session, db_user.country_code)
        else:
            statistics = None
        return cls(
            user_id=db_user.id,
            username=db_user.username,
            join_date=db_user.join_date,
            count300=statistics.count_300 if statistics else 0,
            count100=statistics.count_100 if statistics else 0,
            count50=statistics.count_50 if statistics else 0,
            playcount=statistics.play_count if statistics else 0,
            ranked_score=statistics.ranked_score if statistics else 0,
            total_score=statistics.total_score if statistics else 0,
            pp_rank=statistics.global_rank if statistics and statistics.global_rank else 0,
            level=current_statistics.level_current if current_statistics else 0,
            pp_raw=statistics.pp if statistics else 0.0,
            accuracy=statistics.hit_accuracy if statistics else 0,
            count_rank_ss=current_statistics.grade_ss if current_statistics else 0,
            count_rank_ssh=current_statistics.grade_ssh if current_statistics else 0,
            count_rank_s=current_statistics.grade_s if current_statistics else 0,
            count_rank_sh=current_statistics.grade_sh if current_statistics else 0,
            count_rank_a=current_statistics.grade_a if current_statistics else 0,
            country=db_user.country_code,
            total_seconds_played=statistics.play_time if statistics else 0,
            pp_country_rank=statistics.country_rank if statistics and statistics.country_rank else 0,
            events=[],  # TODO
        )


@router.get(
    "/get_user",
    response_model=list[V1User],
    name="获取用户信息",
    description="获取指定用户的信息。",
)
async def get_user(
    session: Database,
    background_tasks: BackgroundTasks,
    user: str = Query(..., alias="u", description="用户"),
    ruleset_id: int | None = Query(None, alias="m", description="Ruleset ID", ge=0),
    type: Literal["string", "id"] | None = Query(None, description="用户类型：string 用户名称 / id 用户 ID"),
    event_days: int = Query(default=1, ge=1, le=31, description="从现在起所有事件的最大天数"),
):
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # 确定查询方式和用户ID
    is_id_query = type == "id" or user.isdigit()

    # 解析 ruleset
    ruleset = GameMode.from_int_extra(ruleset_id) if ruleset_id else None

    # 如果是 ID 查询，先尝试从缓存获取
    cached_v1_user = None
    user_id_for_cache = None

    if is_id_query:
        try:
            user_id_for_cache = int(user)
            cached_v1_user = await cache_service.get_v1_user_from_cache(user_id_for_cache, ruleset)
            if cached_v1_user:
                return [V1User(**cached_v1_user)]
        except (ValueError, TypeError):
            pass  # 不是有效的用户ID，继续数据库查询

    # 从数据库查询用户
    db_user = (
        await session.exec(
            select(User).where(
                User.id == user if is_id_query else User.username == user,
            )
        )
    ).first()

    if not db_user:
        return []

    try:
        # 生成用户数据
        v1_user = await V1User.from_db(session, db_user, ruleset)

        # 异步缓存结果（如果有用户ID）
        if db_user.id is not None:
            user_data = v1_user.model_dump()
            background_tasks.add_task(cache_service.cache_v1_user, user_data, db_user.id, ruleset)

        return [v1_user]

    except KeyError:
        raise HTTPException(400, "Invalid request")
    except ValueError as e:
        logger.error(f"Error processing V1 user data: {e}")
        raise HTTPException(500, "Internal server error")


# 以下为 get_player_info 接口相关的实现函数


async def _get_pp_history_for_mode(session: Database, user_id: int, mode: GameMode, days: int = 30) -> list[float]:
    """获取指定模式的 PP 历史数据"""
    try:
        # 获取最近 30 天的排名历史（由于没有 PP 历史，我们使用当前的 PP 填充）
        stats = (
            await session.exec(
                select(UserStatistics).where(UserStatistics.user_id == user_id, UserStatistics.mode == mode)
            )
        ).first()

        current_pp = stats.pp if stats else 0.0
        # 创建 30 天的 PP 历史（使用当前 PP 值填充）
        return [current_pp] * days
    except Exception as e:
        logger.error(f"Error getting PP history for user {user_id}, mode {mode}: {e}")
        return [0.0] * days


async def _create_player_mode_stats(
    session: Database, user: User, mode: GameMode, user_statistics: list[UserStatistics]
) -> PlayerModeStats:
    """创建单个模式的玩家统计数据"""
    # 查找对应模式的统计数据
    stats = None
    for stat in user_statistics:
        if stat.mode == mode:
            stats = stat
            break

    if not stats:
        # 如果没有统计数据，创建默认数据
        pp_history = [0.0] * 30
        return PlayerModeStats(
            id=user.id,
            mode=int(mode),
            tscore=0,
            rscore=0,
            pp=0.0,
            plays=0,
            playtime=0,
            acc=0.0,
            max_combo=0,
            total_hits=0,
            replay_views=0,
            xh_count=0,
            x_count=0,
            sh_count=0,
            s_count=0,
            a_count=0,
            level=1,
            level_progress=0,
            rank=0,
            country_rank=0,
            history=PlayerStatsHistory(pp=pp_history),
        )

    # 获取排名信息
    try:
        from app.database.user.statistics import get_rank

        global_rank = await get_rank(session, stats) or 0
        country_rank = await get_rank(session, stats, user.country_code) or 0
    except Exception as e:
        logger.error(f"Error getting rank for user {user.id}: {e}")
        global_rank = 0
        country_rank = 0

    # 获取 PP 历史
    pp_history = await _get_pp_history_for_mode(session, user.id, mode)

    # 计算等级进度
    level_current = int(stats.level_current)
    level_progress = int((stats.level_current - level_current) * 100)

    return PlayerModeStats(
        id=user.id,
        mode=int(mode),
        tscore=stats.total_score,
        rscore=stats.ranked_score,
        pp=stats.pp,
        plays=stats.play_count,
        playtime=stats.play_time,
        acc=stats.hit_accuracy,
        max_combo=stats.maximum_combo,
        total_hits=stats.total_hits,
        replay_views=stats.replays_watched_by_others,
        xh_count=stats.grade_ssh,
        x_count=stats.grade_ss,
        sh_count=stats.grade_sh,
        s_count=stats.grade_s,
        a_count=stats.grade_a,
        level=level_current,
        level_progress=level_progress,
        rank=global_rank,
        country_rank=country_rank,
        history=PlayerStatsHistory(pp=pp_history),
    )


async def _get_player_events(session: Database, user_id: int, event_days: int = 1) -> list[PlayerEventItem]:
    """获取玩家事件"""
    try:
        # 这里暂时返回空列表，因为事件系统需要更多的实现
        # TODO: 实现真正的事件查询
        return []
    except Exception as e:
        logger.error(f"Error getting events for user {user_id}: {e}")
        return []


async def _create_player_info(user: User) -> PlayerInfo:
    """创建玩家基本信息"""
    return PlayerInfo(
        id=user.id,
        name=user.username,
        safe_name=user.username.lower(),  # 使用 username 转小写作为 safe_name
        priv=user.priv,
        country=user.country_code,
        silence_end=int(user.silence_end_at.timestamp()) if user.silence_end_at else 0,
        donor_end=int(user.donor_end_at.timestamp()) if user.donor_end_at else 0,
        creation_time=int(user.join_date.timestamp()),
        latest_activity=int(user.last_visit.timestamp()) if user.last_visit else 0,
        clan_id=0,  # TODO: 实现战队系统
        clan_priv=0,
        preferred_mode=int(user.playmode),
        preferred_type=0,
        play_style=0,
        custom_badge_enabled=0,
        custom_badge_name="",
        custom_badge_icon="",
        custom_badge_color="white",
        userpage_content=user.page.get("html", "") if user.page else "",
        recentFailed=0,
        social_discord=user.discord,
        social_youtube=None,
        social_twitter=user.twitter,
        social_twitch=None,
        social_github=None,
        social_osu=None,
        username_history=user.previous_usernames or [],
    )
