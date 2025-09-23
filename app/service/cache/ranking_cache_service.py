"""
用户排行榜缓存服务
用于缓存用户排行榜数据，减轻数据库压力
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from typing import TYPE_CHECKING, Literal

from app.config import settings
from app.database.user.statistics import UserStatistics, UserStatisticsResp
from app.log import logger
from app.models.score import GameMode
from app.service.assets.asset_proxy_service import get_asset_proxy_service
from app.utils import utcnow

from redis.asyncio import Redis
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    pass


class DateTimeEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，支持 datetime 序列化"""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def safe_json_dumps(data) -> str:
    """安全的 JSON 序列化，支持 datetime 对象"""
    return json.dumps(data, cls=DateTimeEncoder, ensure_ascii=False, separators=(",", ":"))


class RankingCacheService:
    """用户排行榜缓存服务"""

    def __init__(self, redis: Redis):
        self.redis = redis
        self._refreshing = False
        self._background_tasks: set = set()

    def _get_cache_key(
        self,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        country: str | None = None,
        page: int = 1,
    ) -> str:
        """生成缓存键"""
        country_part = f":{country.upper()}" if country else ""
        return f"ranking:{ruleset}:{type}{country_part}:page:{page}"

    def _get_stats_cache_key(
        self,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        country: str | None = None,
    ) -> str:
        """生成统计信息缓存键"""
        country_part = f":{country.upper()}" if country else ""
        return f"ranking:stats:{ruleset}:{type}{country_part}"

    def _get_country_cache_key(self, ruleset: GameMode, page: int = 1) -> str:
        """生成地区排行榜缓存键"""
        return f"country_ranking:{ruleset}:page:{page}"

    def _get_country_stats_cache_key(self, ruleset: GameMode) -> str:
        """生成地区排行榜统计信息缓存键"""
        return f"country_ranking:stats:{ruleset}"

    def _get_team_cache_key(self, ruleset: GameMode, page: int = 1) -> str:
        """生成战队排行榜缓存键"""
        return f"team_ranking:{ruleset}:page:{page}"

    def _get_team_stats_cache_key(self, ruleset: GameMode) -> str:
        """生成战队排行榜统计信息缓存键"""
        return f"team_ranking:stats:{ruleset}"

    async def get_cached_ranking(
        self,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        country: str | None = None,
        page: int = 1,
    ) -> list[dict] | None:
        """获取缓存的排行榜数据"""
        try:
            cache_key = self._get_cache_key(ruleset, type, country, page)
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached ranking: {e}")
            return None

    async def cache_ranking(
        self,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        ranking_data: list[dict],
        country: str | None = None,
        page: int = 1,
        ttl: int | None = None,  # 允许为None以使用配置文件的默认值
    ) -> None:
        """缓存排行榜数据"""
        try:
            cache_key = self._get_cache_key(ruleset, type, country, page)
            # 使用配置文件的TTL设置
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60
            await self.redis.set(cache_key, safe_json_dumps(ranking_data), ex=ttl)
            logger.debug(f"Cached ranking data for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching ranking: {e}")

    async def get_cached_stats(
        self,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        country: str | None = None,
    ) -> dict | None:
        """获取缓存的统计信息"""
        try:
            cache_key = self._get_stats_cache_key(ruleset, type, country)
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached stats: {e}")
            return None

    async def cache_stats(
        self,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        stats: dict,
        country: str | None = None,
        ttl: int | None = None,  # 允许为None以使用配置文件的默认值
    ) -> None:
        """缓存统计信息"""
        try:
            cache_key = self._get_stats_cache_key(ruleset, type, country)
            # 使用配置文件的TTL设置，统计信息缓存时间更长
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60 * 6  # 6倍时间
            await self.redis.set(cache_key, safe_json_dumps(stats), ex=ttl)
            logger.debug(f"Cached stats for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching stats: {e}")

    async def get_cached_country_ranking(
        self,
        ruleset: GameMode,
        page: int = 1,
    ) -> list[dict] | None:
        """获取缓存的地区排行榜数据"""
        try:
            cache_key = self._get_country_cache_key(ruleset, page)
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached country ranking: {e}")
            return None

    async def cache_country_ranking(
        self,
        ruleset: GameMode,
        ranking_data: list[dict],
        page: int = 1,
        ttl: int | None = None,
    ) -> None:
        """缓存地区排行榜数据"""
        try:
            cache_key = self._get_country_cache_key(ruleset, page)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60
            await self.redis.set(cache_key, safe_json_dumps(ranking_data), ex=ttl)
            logger.debug(f"Cached country ranking data for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching country ranking: {e}")

    async def get_cached_team_ranking(
        self,
        ruleset: GameMode,
        page: int = 1,
    ) -> list[dict] | None:
        """获取缓存的战队排行榜数据"""
        try:
            cache_key = self._get_team_cache_key(ruleset, page)
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached team ranking: {e}")
            return None

    async def cache_team_ranking(
        self,
        ruleset: GameMode,
        ranking_data: list[dict],
        page: int = 1,
        ttl: int | None = None,
    ) -> None:
        """缓存战队排行榜数据"""
        try:
            cache_key = self._get_team_cache_key(ruleset, page)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60
            await self.redis.set(cache_key, safe_json_dumps(ranking_data), ex=ttl)
            logger.debug(f"Cached team ranking data for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching team ranking: {e}")

    async def get_cached_team_stats(self, ruleset: GameMode) -> dict | None:
        """获取缓存的战队排行榜统计信息"""
        try:
            cache_key = self._get_team_stats_cache_key(ruleset)
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached team stats: {e}")
            return None

    async def cache_team_stats(
        self,
        ruleset: GameMode,
        stats: dict,
        ttl: int | None = None,
    ) -> None:
        """缓存战队排行榜统计信息"""
        try:
            cache_key = self._get_team_stats_cache_key(ruleset)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60 * 6
            await self.redis.set(cache_key, safe_json_dumps(stats), ex=ttl)
            logger.debug(f"Cached team stats for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching team stats: {e}")

    async def get_cached_country_stats(self, ruleset: GameMode) -> dict | None:
        """获取缓存的地区排行榜统计信息"""
        try:
            cache_key = self._get_country_stats_cache_key(ruleset)
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached country stats: {e}")
            return None

    async def cache_country_stats(
        self,
        ruleset: GameMode,
        stats: dict,
        ttl: int | None = None,
    ) -> None:
        """缓存地区排行榜统计信息"""
        try:
            cache_key = self._get_country_stats_cache_key(ruleset)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60 * 6  # 6倍时间
            await self.redis.set(cache_key, safe_json_dumps(stats), ex=ttl)
            logger.debug(f"Cached country stats for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching country stats: {e}")

    async def refresh_ranking_cache(
        self,
        session: AsyncSession,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        country: str | None = None,
        max_pages: int | None = None,  # 允许为None以使用配置文件的默认值
    ) -> None:
        """刷新排行榜缓存"""
        if self._refreshing:
            logger.debug(f"Ranking cache refresh already in progress for {ruleset}:{type}")
            return

        # 使用配置文件的设置
        if max_pages is None:
            max_pages = settings.ranking_cache_max_pages

        self._refreshing = True
        try:
            logger.info(f"Starting ranking cache refresh for {ruleset}:{type}")

            # 构建查询条件
            wheres = [
                col(UserStatistics.mode) == ruleset,
                col(UserStatistics.pp) > 0,
                col(UserStatistics.is_ranked).is_(True),
            ]
            include = ["user"]

            if type == "performance":
                order_by = col(UserStatistics.pp).desc()
                include.append("rank_change_since_30_days")
            else:
                order_by = col(UserStatistics.ranked_score).desc()

            if country:
                wheres.append(col(UserStatistics.user).has(country_code=country.upper()))

            # 获取总用户数用于统计
            total_users_query = select(UserStatistics).where(*wheres)
            total_users = len((await session.exec(total_users_query)).all())

            # 计算统计信息
            stats = {
                "total_users": total_users,
                "last_updated": utcnow().isoformat(),
                "type": type,
                "ruleset": ruleset,
                "country": country,
            }

            # 缓存统计信息
            await self.cache_stats(ruleset, type, stats, country)

            # 分页缓存数据
            for page in range(1, max_pages + 1):
                try:
                    statistics_list = await session.exec(
                        select(UserStatistics).where(*wheres).order_by(order_by).limit(50).offset(50 * (page - 1))
                    )

                    statistics_data = statistics_list.all()
                    if not statistics_data:
                        break  # 没有更多数据

                    # 转换为响应格式并确保正确序列化
                    ranking_data = []
                    for statistics in statistics_data:
                        user_stats_resp = await UserStatisticsResp.from_db(statistics, session, None, include)

                        # 应用资源代理处理
                        if settings.enable_asset_proxy:
                            try:
                                asset_proxy_service = get_asset_proxy_service()
                                user_stats_resp = await asset_proxy_service.replace_asset_urls(user_stats_resp)
                            except Exception as e:
                                logger.warning(f"Asset proxy processing failed for ranking cache: {e}")

                        # 将 UserStatisticsResp 转换为字典，处理所有序列化问题
                        user_dict = json.loads(user_stats_resp.model_dump_json())
                        ranking_data.append(user_dict)

                    # 缓存这一页的数据
                    await self.cache_ranking(ruleset, type, ranking_data, country, page)

                    # 添加延迟避免数据库过载
                    if page < max_pages:
                        await asyncio.sleep(0.1)

                except Exception as e:
                    logger.error(f"Error caching page {page} for {ruleset}:{type}: {e}")

            logger.debug(f"Completed ranking cache refresh for {ruleset}:{type}")

        except Exception as e:
            logger.error(f"Ranking cache refresh failed for {ruleset}:{type}: {e}")
        finally:
            self._refreshing = False

    async def refresh_country_ranking_cache(
        self,
        session: AsyncSession,
        ruleset: GameMode,
        max_pages: int | None = None,
    ) -> None:
        """刷新地区排行榜缓存"""
        if self._refreshing:
            logger.debug(f"Country ranking cache refresh already in progress for {ruleset}")
            return

        if max_pages is None:
            max_pages = settings.ranking_cache_max_pages

        self._refreshing = True
        try:
            logger.info(f"Starting country ranking cache refresh for {ruleset}")

            # 获取所有国家
            from app.database import User

            countries = (await session.exec(select(User.country_code).distinct())).all()

            # 计算每个国家的统计数据
            country_stats_list = []
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
                country_stats = {
                    "code": country,
                    "active_users": 0,
                    "play_count": 0,
                    "ranked_score": 0,
                    "performance": 0,
                }

                for stat in statistics:
                    country_stats["active_users"] += 1
                    country_stats["play_count"] += stat.play_count
                    country_stats["ranked_score"] += stat.ranked_score
                    pp += stat.pp

                country_stats["performance"] = round(pp)
                country_stats_list.append(country_stats)

            # 按表现分排序
            country_stats_list.sort(key=lambda x: x["performance"], reverse=True)

            # 计算统计信息
            stats = {
                "total_countries": len(country_stats_list),
                "last_updated": utcnow().isoformat(),
                "ruleset": ruleset,
            }

            # 缓存统计信息
            await self.cache_country_stats(ruleset, stats)

            # 分页缓存数据（每页50个国家）
            page_size = 50
            for page in range(1, max_pages + 1):
                start_idx = (page - 1) * page_size
                end_idx = start_idx + page_size

                page_data = country_stats_list[start_idx:end_idx]
                if not page_data:
                    break  # 没有更多数据

                # 缓存这一页的数据
                await self.cache_country_ranking(ruleset, page_data, page)

                # 添加延迟避免Redis过载
                if page < max_pages and page_data:
                    await asyncio.sleep(0.1)

            logger.info(f"Completed country ranking cache refresh for {ruleset}")

        except Exception as e:
            logger.error(f"Country ranking cache refresh failed for {ruleset}: {e}")
        finally:
            self._refreshing = False

    async def refresh_all_rankings(self, session: AsyncSession) -> None:
        """刷新所有排行榜缓存"""
        game_modes = [GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA]
        ranking_types: list[Literal["performance", "score"]] = ["performance", "score"]

        # 获取需要缓存的国家列表（活跃用户数量前20的国家）
        from app.database import User

        from sqlmodel import func

        countries_query = (
            await session.exec(
                select(User.country_code, func.count().label("user_count"))
                .where(col(User.is_active).is_(True))
                .group_by(User.country_code)
                .order_by(func.count().desc())
                .limit(settings.ranking_cache_top_countries)
            )
        ).all()

        top_countries = [country for country, _ in countries_query]

        refresh_tasks = []

        # 全球排行榜
        for mode in game_modes:
            for ranking_type in ranking_types:
                task = self.refresh_ranking_cache(session, mode, ranking_type)
                refresh_tasks.append(task)

        # 国家排行榜（仅前20个国家）
        for country in top_countries:
            for mode in game_modes:
                for ranking_type in ranking_types:
                    task = self.refresh_ranking_cache(session, mode, ranking_type, country)
                    refresh_tasks.append(task)

        # 地区排行榜
        for mode in game_modes:
            task = self.refresh_country_ranking_cache(session, mode)
            refresh_tasks.append(task)

        # 并发执行刷新任务，但限制并发数
        semaphore = asyncio.Semaphore(5)  # 最多同时5个任务

        async def bounded_refresh(task):
            async with semaphore:
                await task

        bounded_tasks = [bounded_refresh(task) for task in refresh_tasks]

        try:
            await asyncio.gather(*bounded_tasks, return_exceptions=True)
            logger.info("All ranking cache refresh completed")
        except Exception as e:
            logger.error(f"Error in batch ranking cache refresh: {e}")

    async def invalidate_cache(
        self,
        ruleset: GameMode | None = None,
        type: Literal["performance", "score"] | None = None,
        country: str | None = None,
        include_country_ranking: bool = True,
    ) -> None:
        """使缓存失效"""
        try:
            deleted_keys = 0

            if ruleset and type:
                # 删除特定的用户排行榜缓存
                country_part = f":{country.upper()}" if country else ""
                pattern = f"ranking:{ruleset}:{type}{country_part}:page:*"
                keys = await self.redis.keys(pattern)
                if keys:
                    await self.redis.delete(*keys)
                    deleted_keys += len(keys)
                    logger.info(f"Invalidated {len(keys)} cache keys for {ruleset}:{type}")
            elif ruleset:
                # 删除特定游戏模式的所有缓存
                patterns = [
                    f"ranking:{ruleset}:*",
                    f"country_ranking:{ruleset}:*" if include_country_ranking else None,
                ]
                for pattern in patterns:
                    if pattern:
                        keys = await self.redis.keys(pattern)
                        if keys:
                            await self.redis.delete(*keys)
                            deleted_keys += len(keys)
            else:
                # 删除所有排行榜缓存
                patterns = ["ranking:*"]
                if include_country_ranking:
                    patterns.append("country_ranking:*")

                for pattern in patterns:
                    keys = await self.redis.keys(pattern)
                    if keys:
                        await self.redis.delete(*keys)
                        deleted_keys += len(keys)

                logger.info(f"Invalidated all {deleted_keys} ranking cache keys")

        except Exception as e:
            logger.error(f"Error invalidating cache: {e}")

    async def invalidate_country_cache(self, ruleset: GameMode | None = None) -> None:
        """使地区排行榜缓存失效"""
        try:
            if ruleset:
                pattern = f"country_ranking:{ruleset}:*"
            else:
                pattern = "country_ranking:*"

            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} country ranking cache keys")
        except Exception as e:
            logger.error(f"Error invalidating country cache: {e}")

    async def invalidate_team_cache(self, ruleset: GameMode | None = None) -> None:
        """使战队排行榜缓存失效"""
        try:
            if ruleset:
                pattern = f"team_ranking:{ruleset}:*"
            else:
                pattern = "team_ranking:*"

            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} team ranking cache keys")
        except Exception as e:
            logger.error(f"Error invalidating team cache: {e}")

    async def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        try:
            # 获取用户排行榜缓存
            ranking_keys = await self.redis.keys("ranking:*")
            # 获取地区排行榜缓存
            country_keys = await self.redis.keys("country_ranking:*")

            total_keys = ranking_keys + country_keys
            total_size = 0

            for key in total_keys[:100]:  # 限制检查数量以避免性能问题
                try:
                    size = await self.redis.memory_usage(key)
                    if size:
                        total_size += size
                except Exception:
                    continue

            return {
                "cached_user_rankings": len(ranking_keys),
                "cached_country_rankings": len(country_keys),
                "total_cached_rankings": len(total_keys),
                "estimated_total_size_mb": (round(total_size / 1024 / 1024, 2) if total_size > 0 else 0),
                "refreshing": self._refreshing,
            }
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {"error": str(e)}


# 全局缓存服务实例
_ranking_cache_service: RankingCacheService | None = None


def get_ranking_cache_service(redis: Redis) -> RankingCacheService:
    """获取排行榜缓存服务实例"""
    global _ranking_cache_service
    if _ranking_cache_service is None:
        _ranking_cache_service = RankingCacheService(redis)
    return _ranking_cache_service


async def schedule_ranking_refresh_task(session: AsyncSession, redis: Redis):
    """定时排行榜刷新任务"""
    # 默认启用排行榜缓存，除非明确禁用
    enable_ranking_cache = getattr(settings, "enable_ranking_cache", True)
    if not enable_ranking_cache:
        return

    cache_service = get_ranking_cache_service(redis)
    try:
        await cache_service.refresh_all_rankings(session)
    except Exception as e:
        logger.error(f"Scheduled ranking refresh task failed: {e}")
