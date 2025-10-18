from typing import Annotated, Literal

from app.database.statistics import UserStatistics
from app.database.user import User
from app.dependencies.database import Database, get_redis
from app.log import logger
from app.models.score import GameMode
from app.models.v1_user import (
    GetPlayerCountResponse,
    GetPlayerInfoResponse,
    PlayerAllResponse,
    PlayerCountData,
    PlayerEventsResponse,
    PlayerInfoResponse,
    PlayerStatsHistory,
    PlayerStatsResponse,
)
from app.router.v1.public_router import public_router

from fastapi import HTTPException, Query
from sqlmodel import select


async def _create_player_mode_stats(
    session: Database, user: User, mode: GameMode, user_statistics: list[UserStatistics]
):
    """创建指定模式的玩家统计数据"""
    from app.models.v1_user import PlayerModeStats

    # 查找对应模式的统计数据
    statistics = None
    for stats in user_statistics:
        if stats.mode == mode:
            statistics = stats
            break

    if not statistics:
        # 如果没有统计数据，返回默认值
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
            history=PlayerStatsHistory(),
        )

    return PlayerModeStats(
        id=user.id,
        mode=int(mode),
        tscore=statistics.total_score if statistics.total_score else 0,
        rscore=statistics.ranked_score if statistics.ranked_score else 0,
        pp=float(statistics.pp) if statistics.pp else 0.0,
        plays=statistics.play_count if statistics.play_count else 0,
        playtime=statistics.play_time if statistics.play_time else 0,
        acc=float(statistics.hit_accuracy) if statistics.hit_accuracy else 0.0,
        max_combo=statistics.maximum_combo if statistics.maximum_combo else 0,
        total_hits=statistics.total_hits if statistics.total_hits else 0,
        replay_views=statistics.replays_watched_by_others if statistics.replays_watched_by_others else 0,
        xh_count=statistics.grade_ssh if statistics.grade_ssh else 0,
        x_count=statistics.grade_ss if statistics.grade_ss else 0,
        sh_count=statistics.grade_sh if statistics.grade_sh else 0,
        s_count=statistics.grade_s if statistics.grade_s else 0,
        a_count=statistics.grade_a if statistics.grade_a else 0,
        level=int(statistics.level_current) if statistics.level_current else 1,
        level_progress=0,  # TODO: 计算等级进度
        rank=0,  # global_rank需要从RankHistory获取
        country_rank=0,  # country_rank需要从其他地方获取
        history=PlayerStatsHistory(),  # TODO: 获取PP历史数据
    )


async def _create_player_info(user: User):
    """创建玩家基本信息"""
    from app.models.v1_user import PlayerInfo

    return PlayerInfo(
        id=user.id,
        name=user.username,
        safe_name=user.username,  # 使用 username 作为 safe_name
        priv=user.priv if user.priv else 1,
        country=user.country_code if user.country_code else "",
        silence_end=int(user.silence_end_at.timestamp()) if user.silence_end_at else 0,
        donor_end=int(user.donor_end_at.timestamp()) if user.donor_end_at else 0,
        creation_time=int(user.join_date.timestamp()) if user.join_date else 0,
        latest_activity=int(user.last_visit.timestamp()) if user.last_visit else 0,
        clan_id=0,  # TODO: 从 user 获取战队信息
        clan_priv=0,
        preferred_mode=int(user.playmode) if user.playmode else 0,
        preferred_type=0,
        play_style=0,  # TODO: 从 user.playstyle 获取游戏风格
        custom_badge_enabled=0,
        custom_badge_name="",
        custom_badge_icon="",
        custom_badge_color="",
        userpage_content=user.page["html"] if user.page and "html" in user.page else "",
        recentFailed=0,  # TODO: 获取最近失败次数
        social_discord=user.discord,
        social_youtube=None,
        social_twitter=user.twitter,
        social_twitch=None,
        social_github=None,
        social_osu=None,
        username_history=user.previous_usernames if user.previous_usernames else [],
    )


async def _get_player_events(session: Database, user_id: int):
    """获取玩家事件列表"""
    # TODO: 实现事件查询逻辑
    # 这里应该查询 app.database.events 表
    return []


async def _count_online_users_optimized(redis):
    """
    优化的在线用户计数函数
    首先尝试使用HyperLogLog近似计数，失败则回退到SCAN
    """
    try:
        online_set_key = "metadata:online_users_set"
        if await redis.exists(online_set_key):
            count = await redis.scard(online_set_key)
            logger.debug(f"Using online users set, count: {count}")
            return count

    except Exception as e:
        logger.debug(f"Online users set not available: {e}")

    # 方案2: 回退到优化的SCAN操作
    online_count = 0
    cursor = 0
    scan_iterations = 0
    max_iterations = 50  # 进一步减少最大迭代次数
    batch_size = 10000  # 增加批次大小

    try:
        while cursor != 0 or scan_iterations == 0:
            if scan_iterations >= max_iterations:
                logger.warning(f"Redis SCAN reached max iterations ({max_iterations}), breaking")
                break

            cursor, keys = await redis.scan(cursor, match="metadata:online:*", count=batch_size)
            online_count += len(keys)
            scan_iterations += 1

            # 如果连续几次没有找到键，可能已经扫描完成
            if len(keys) == 0 and scan_iterations > 2:
                break

        logger.debug(f"Found {online_count} online users after {scan_iterations} scan iterations")
        return online_count

    except Exception as e:
        logger.error(f"Error counting online users: {e}")
        # 如果SCAN失败，返回0而不是让整个API失败
        return 0


@public_router.get(
    "/get_player_info",
    name="获取玩家信息",
    description="返回指定玩家的信息。",
)
async def api_get_player_info(
    session: Database,
    scope: Annotated[Literal["stats", "events", "info", "all"], Query(..., description="信息范围")],
    id: Annotated[int | None, Query(ge=3, le=2147483647, description="用户 ID")] = None,
    name: Annotated[str | None, Query(regex=r"^[\w \[\]-]{2,32}$", description="用户名")] = None,
):
    """
    获取指定玩家的信息

    Args:
        scope: 信息范围 - stats(统计), events(事件), info(基本信息), all(全部)
        id: 用户 ID (可选)
        name: 用户名 (可选)
    """
    # 验证参数
    if not id and not name:
        raise HTTPException(400, "Must provide either id or name")

    # 查询用户
    if id:
        user = await session.get(User, id)
    else:
        user = (await session.exec(select(User).where(User.username == name))).first()

    if not user:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=200, content={"status": "Player not found."})

    try:
        if scope == "stats":
            # 获取所有模式的统计数据
            user_statistics = list(
                (await session.exec(select(UserStatistics).where(UserStatistics.user_id == user.id))).all()
            )

            stats_dict = {}
            # 获取所有游戏模式的统计数据
            all_modes = [GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA, GameMode.OSURX, GameMode.OSUAP]

            for mode in all_modes:
                mode_stats = await _create_player_mode_stats(session, user, mode, user_statistics)
                stats_dict[str(int(mode))] = mode_stats

            return GetPlayerInfoResponse(player=PlayerStatsResponse(stats=stats_dict))

        elif scope == "events":
            # 获取事件数据
            events = await _get_player_events(session, user.id)
            return GetPlayerInfoResponse(player=PlayerEventsResponse(events=events))

        elif scope == "info":
            # 获取基本信息
            info = await _create_player_info(user)
            return GetPlayerInfoResponse(player=PlayerInfoResponse(info=info))

        elif scope == "all":
            # 获取所有信息
            # 统计数据
            user_statistics = list(
                (await session.exec(select(UserStatistics).where(UserStatistics.user_id == user.id))).all()
            )

            stats_dict = {}
            all_modes = [GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA, GameMode.OSURX, GameMode.OSUAP]

            for mode in all_modes:
                mode_stats = await _create_player_mode_stats(session, user, mode, user_statistics)
                stats_dict[str(int(mode))] = mode_stats

            # 基本信息
            info = await _create_player_info(user)

            # 事件
            events = await _get_player_events(session, user.id)

            return GetPlayerInfoResponse(player=PlayerAllResponse(info=info, stats=stats_dict, events=events))

    except Exception as e:
        logger.error(f"Error processing get_player_info for user {user.id}: {e}")
        raise HTTPException(500, "Internal server error")


@public_router.get(
    "/get_player_count",
    response_model=GetPlayerCountResponse,
    name="获取玩家数量",
    description="返回在线和总用户数量。",
)
async def api_get_player_count(
    session: Database,
):
    """
    获取玩家数量统计

    Returns:
        包含在线用户数和总用户数的响应
    """
    try:
        redis = get_redis()

        online_cache_key = "stats:online_users_count"
        cached_online = await redis.get(online_cache_key)

        if cached_online is not None:
            online_count = int(cached_online)
            logger.debug(f"Using cached online user count: {online_count}")
        else:
            logger.debug("Cache miss, scanning Redis for online users")
            online_count = await _count_online_users_optimized(redis)

            await redis.setex(online_cache_key, 30, str(online_count))
            logger.debug(f"Cached online user count: {online_count} for 30 seconds")

        cache_key = "stats:total_users"
        cached_total = await redis.get(cache_key)

        if cached_total is not None:
            total_count = int(cached_total)
            logger.debug(f"Using cached total user count: {total_count}")
        else:
            logger.debug("Cache miss, querying database for total user count")
            from sqlmodel import func, select

            total_count_result = await session.exec(select(func.count()).select_from(User))
            total_count = total_count_result.one()

            await redis.setex(cache_key, 3600, str(total_count))
            logger.debug(f"Cached total user count: {total_count} for 1 hour")

        return GetPlayerCountResponse(
            counts=PlayerCountData(
                online=online_count,
                total=max(0, total_count - 1),  # 减去1个机器人账户，确保不为负数
            )
        )

    except Exception as e:
        logger.error(f"Error getting player count: {e}")
        raise HTTPException(500, "Internal server error")
