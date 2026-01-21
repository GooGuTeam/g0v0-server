from datetime import datetime
from typing import Annotated, Literal

from app.database.statistics import UserStatistics, UserStatisticsModel
from app.database.user import User
from app.dependencies.database import Database, get_redis
from app.log import logger
from app.models.score import GameMode
from app.service.user_cache_service import get_user_cache_service

from .router import AllStrModel, router

from app.models.error import ErrorType, RequestError
from fastapi import BackgroundTasks, Query
from sqlmodel import col, select


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
    async def from_db(cls, db_user: User, ruleset: GameMode | None = None) -> "V1User":
        ruleset = ruleset or db_user.playmode
        current_statistics: UserStatistics | None = None
        for i in await db_user.awaitable_attrs.statistics:
            if i.mode == ruleset:
                current_statistics = i
                break
        if current_statistics:
            statistics = await UserStatisticsModel.transform(
                current_statistics, country_code=db_user.country_code, includes=["country_rank"]
            )
        else:
            statistics = None
        return cls(
            user_id=db_user.id,
            username=db_user.username,
            join_date=db_user.join_date,
            count300=current_statistics.count_300 if current_statistics else 0,
            count100=current_statistics.count_100 if current_statistics else 0,
            count50=current_statistics.count_50 if current_statistics else 0,
            playcount=current_statistics.play_count if current_statistics else 0,
            ranked_score=current_statistics.ranked_score if current_statistics else 0,
            total_score=current_statistics.total_score if current_statistics else 0,
            pp_rank=statistics.get("global_rank") or 0 if statistics else 0,
            level=current_statistics.level_current if current_statistics else 0,
            pp_raw=current_statistics.pp if current_statistics else 0.0,
            accuracy=current_statistics.hit_accuracy if current_statistics else 0,
            count_rank_ss=current_statistics.grade_ss if current_statistics else 0,
            count_rank_ssh=current_statistics.grade_ssh if current_statistics else 0,
            count_rank_s=current_statistics.grade_s if current_statistics else 0,
            count_rank_sh=current_statistics.grade_sh if current_statistics else 0,
            count_rank_a=current_statistics.grade_a if current_statistics else 0,
            country=db_user.country_code,
            total_seconds_played=current_statistics.play_time if current_statistics else 0,
            pp_country_rank=statistics.get("country_rank") or 0 if statistics else 0,
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
    user: Annotated[str, Query(..., alias="u", description="用户")],
    ruleset_id: Annotated[int | None, Query(alias="m", description="Ruleset ID", ge=0)] = None,
    type: Annotated[Literal["string", "id"] | None, Query(description="用户类型：string 用户名称 / id 用户 ID")] = None,
    event_days: Annotated[int, Query(ge=1, le=31, description="从现在起所有事件的最大天数")] = 1,
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
                ~User.is_restricted_query(col(User.id)),
            )
        )
    ).first()

    if not db_user:
        return []

    try:
        # 生成用户数据
        v1_user = await V1User.from_db(db_user, ruleset)

        # 异步缓存结果（如果有用户ID）
        if db_user.id is not None:
            user_data = v1_user.model_dump()
            background_tasks.add_task(cache_service.cache_v1_user, user_data, db_user.id, ruleset)

        return [v1_user]

    except KeyError:
        raise RequestError(ErrorType.INVALID_REQUEST)
    except ValueError as e:
        logger.error(f"Error processing V1 user data: {e}")
        raise RequestError(ErrorType.INTERNAL)


# 以下为 get_player_info 接口相关的实现函数


async def _get_pp_history_for_mode(session: Database, user_id: int, mode: GameMode, days: int = 30) -> list[float]:
    """获取指定模式的 PP 历史数据"""
    try:
        # 获取最近 30 天的排名历史（由于没有 PP 历史，我们使用当前的 PP 填充）
        stats = (
            await session.exec(
                select(UserStatistics).where(
                    UserStatistics.user_id == user_id,
                    UserStatistics.mode == mode,
                    ~User.is_restricted_query(col(UserStatistics.user_id)),
                )
            )
        ).first()

        current_pp = stats.pp if stats else 0.0
        # 创建 30 天的 PP 历史（使用当前 PP 值填充）
        return [current_pp] * days
    except Exception as e:
        logger.error(f"Error getting PP history for user {user_id}, mode {mode}: {e}")
        return [0.0] * days
