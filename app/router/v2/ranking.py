from __future__ import annotations

from typing import Literal

from app.config import settings
from app.database import Team, TeamMember, User, UserStatistics, UserStatisticsResp
from app.dependencies import get_current_user
from app.dependencies.database import Database, get_redis
from app.models.score import GameMode
from app.service.ranking_cache_service import get_ranking_cache_service

from .router import router

from fastapi import BackgroundTasks, Path, Query, Security
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import col, select


class TeamStatistics(BaseModel):
    team_id: int
    ruleset_id: int
    play_count: int
    ranked_score: int
    performance: int


class TeamResponse(BaseModel):
    ranking: list[TeamStatistics]


SortType = Literal["performance", "score"]


@router.get(
    "/rankings/{ruleset}/team",
    name="获取战队排行榜",
    description="获取在指定模式下按照 pp 排序的战队排行榜",
    tags=["排行榜"],
    status_code=301,
)
async def get_team_ranking_pp(
    ruleset: GameMode = Path(..., description="指定 ruleset"),
    page: int = Query(1, ge=1, description="页码"),
):
    return RedirectResponse(url=f"/api/v2/rankings/{ruleset}/team/performance?page={page}", status_code=301)


@router.get(
    "/rankings/{ruleset}/team/{sort}",
    response_model=TeamResponse,
    name="获取战队排行榜",
    description="获取在指定模式下的战队排行榜",
    tags=["排行榜"],
)
async def get_team_ranking(
    session: Database,
    background_tasks: BackgroundTasks,
    sort: SortType = Path(
        ...,
        description="排名类型：performance 表现分 / score 计分成绩总分 "
        "**这个参数是本服务器额外添加的，不属于 v2 API 的一部分**",
    ),
    ruleset: GameMode = Path(..., description="指定 ruleset"),
    page: int = Query(1, ge=1, description="页码"),
    current_user: User = Security(get_current_user, scopes=["public"]),
):
    # 获取 Redis 连接和缓存服务
    redis = get_redis()
    cache_service = get_ranking_cache_service(redis)

    # 尝试从缓存获取数据（战队排行榜）
    cached_data = await cache_service.get_cached_team_ranking(ruleset, page)

    if cached_data:
        # 从缓存返回数据
        return TeamResponse(ranking=[TeamStatistics.model_validate(item) for item in cached_data])

    # 缓存未命中，从数据库查询
    response = TeamResponse(ranking=[])
    teams = (await session.exec(select(Team))).all()

    for team in teams:
        statistics = (
            await session.exec(
                select(UserStatistics).where(
                    UserStatistics.mode == ruleset,
                    UserStatistics.pp > 0,
                    col(UserStatistics.user).has(col(User.team_membership).has(col(TeamMember.team_id) == team.id)),
                )
            )
        ).all()

        if not statistics:
            continue

        pp = 0
        stats = TeamStatistics(
            team_id=team.id,
            ruleset_id=int(ruleset),
            play_count=0,
            ranked_score=0,
            performance=0,
        )
        for stat in statistics:
            stats.ranked_score += stat.ranked_score
            pp += stat.pp
        stats.performance = round(pp)
        response.ranking.append(stats)

    if sort == "performance":
        response.ranking.sort(key=lambda x: x.performance, reverse=True)
    else:
        response.ranking.sort(key=lambda x: x.ranked_score, reverse=True)

    # 分页处理
    page_size = 50
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size

    # 获取当前页的数据
    current_page_data = response.ranking[start_idx:end_idx]

    # 异步缓存数据（不等待完成）
    cache_data = [item.model_dump() for item in current_page_data]

    # 创建后台任务来缓存数据
    background_tasks.add_task(
        cache_service.cache_team_ranking,
        ruleset,
        cache_data,
        page,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    # 返回当前页的结果
    response.ranking = current_page_data
    return response


class CountryStatistics(BaseModel):
    code: str
    active_users: int
    play_count: int
    ranked_score: int
    performance: int


class CountryResponse(BaseModel):
    ranking: list[CountryStatistics]


@router.get(
    "/rankings/{ruleset}/country",
    name="获取地区排行榜",
    description="获取在指定模式下按照 pp 排序的地区排行榜",
    tags=["排行榜"],
    status_code=301,
)
async def get_country_ranking_pp(
    ruleset: GameMode = Path(..., description="指定 ruleset"),
    page: int = Query(1, ge=1, description="页码"),
):
    return RedirectResponse(url=f"/api/v2/rankings/{ruleset}/country/performance?page={page}", status_code=301)


@router.get(
    "/rankings/{ruleset}/country/{sort}",
    response_model=CountryResponse,
    name="获取地区排行榜",
    description="获取在指定模式下的地区排行榜",
    tags=["排行榜"],
)
async def get_country_ranking(
    session: Database,
    background_tasks: BackgroundTasks,
    ruleset: GameMode = Path(..., description="指定 ruleset"),
    page: int = Query(1, ge=1, description="页码"),
    sort: SortType = Path(
        ...,
        description="排名类型：performance 表现分 / score 计分成绩总分 "
        "**这个参数是本服务器额外添加的，不属于 v2 API 的一部分**",
    ),
    current_user: User = Security(get_current_user, scopes=["public"]),
):
    # 获取 Redis 连接和缓存服务
    redis = get_redis()
    cache_service = get_ranking_cache_service(redis)

    # 尝试从缓存获取数据
    cached_data = await cache_service.get_cached_country_ranking(ruleset, page)

    if cached_data:
        # 从缓存返回数据
        return CountryResponse(ranking=[CountryStatistics.model_validate(item) for item in cached_data])

    # 缓存未命中，从数据库查询
    response = CountryResponse(ranking=[])
    countries = (await session.exec(select(User.country_code).distinct())).all()

    for country in countries:
        if not country:  # 跳过空的国家代码
            continue

        statistics = (
            await session.exec(
                select(UserStatistics).where(
                    UserStatistics.mode == ruleset,
                    UserStatistics.pp > 0,
                    col(UserStatistics.user).has(country_code=country),
                    col(UserStatistics.user).has(is_active=True),
                )
            )
        ).all()

        if not statistics:  # 跳过没有数据的国家
            continue

        pp = 0
        country_stats = CountryStatistics(
            code=country,
            active_users=0,
            play_count=0,
            ranked_score=0,
            performance=0,
        )
        for stat in statistics:
            country_stats.active_users += 1
            country_stats.play_count += stat.play_count
            country_stats.ranked_score += stat.ranked_score
            pp += stat.pp
        country_stats.performance = round(pp)
        response.ranking.append(country_stats)

    if sort == "performance":
        response.ranking.sort(key=lambda x: x.performance, reverse=True)
    else:
        response.ranking.sort(key=lambda x: x.ranked_score, reverse=True)

    # 分页处理
    page_size = 50
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size

    # 获取当前页的数据
    current_page_data = response.ranking[start_idx:end_idx]

    # 异步缓存数据（不等待完成）
    cache_data = [item.model_dump() for item in current_page_data]

    # 创建后台任务来缓存数据
    background_tasks.add_task(
        cache_service.cache_country_ranking,
        ruleset,
        cache_data,
        page,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    # 返回当前页的结果
    response.ranking = current_page_data
    return response


class TopUsersResponse(BaseModel):
    ranking: list[UserStatisticsResp]


@router.get(
    "/rankings/{ruleset}/{sort}",
    response_model=TopUsersResponse,
    name="获取用户排行榜",
    description="获取在指定模式下的用户排行榜",
    tags=["排行榜"],
)
async def get_user_ranking(
    session: Database,
    background_tasks: BackgroundTasks,
    ruleset: GameMode = Path(..., description="指定 ruleset"),
    sort: SortType = Path(..., description="排名类型：performance 表现分 / score 计分成绩总分"),
    country: str | None = Query(None, description="国家代码"),
    page: int = Query(1, ge=1, description="页码"),
    current_user: User = Security(get_current_user, scopes=["public"]),
):
    # 获取 Redis 连接和缓存服务
    redis = get_redis()
    cache_service = get_ranking_cache_service(redis)

    # 尝试从缓存获取数据
    cached_data = await cache_service.get_cached_ranking(ruleset, sort, country, page)

    if cached_data:
        # 从缓存返回数据
        return TopUsersResponse(ranking=[UserStatisticsResp.model_validate(item) for item in cached_data])

    # 缓存未命中，从数据库查询
    wheres = [
        col(UserStatistics.mode) == ruleset,
        col(UserStatistics.pp) > 0,
        col(UserStatistics.is_ranked).is_(True),
    ]
    include = ["user"]
    if sort == "performance":
        order_by = col(UserStatistics.pp).desc()
        include.append("rank_change_since_30_days")
    else:
        order_by = col(UserStatistics.ranked_score).desc()
    if country:
        wheres.append(col(UserStatistics.user).has(country_code=country.upper()))

    statistics_list = await session.exec(
        select(UserStatistics).where(*wheres).order_by(order_by).limit(50).offset(50 * (page - 1))
    )

    # 转换为响应格式
    ranking_data = []
    for statistics in statistics_list:
        user_stats_resp = await UserStatisticsResp.from_db(statistics, session, None, include)
        ranking_data.append(user_stats_resp)

    # 异步缓存数据（不等待完成）
    # 使用配置文件中的TTL设置
    cache_data = [item.model_dump() for item in ranking_data]
    # 创建后台任务来缓存数据

    background_tasks.add_task(
        cache_service.cache_ranking,
        ruleset,
        sort,
        cache_data,
        country,
        page,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    resp = TopUsersResponse(ranking=ranking_data)
    return resp
