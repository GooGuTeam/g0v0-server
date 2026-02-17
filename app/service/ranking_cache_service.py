"""User ranking cache service.

Caches user ranking data to reduce database load.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Literal

from app.config import settings
from app.database.statistics import UserStatistics, UserStatisticsModel
from app.helpers import replace_asset_urls, safe_json_dumps, utcnow
from app.log import logger
from app.models.score import GameMode

from redis.asyncio import Redis
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    pass


class RankingCacheService:
    """User ranking cache service.

    Caches performance and score rankings, country rankings,
    and team rankings using Redis.
    """

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
        """Generate cache key."""
        country_part = f":{country.upper()}" if country else ""
        return f"ranking:{ruleset}:{type}{country_part}:page:{page}"

    def _get_stats_cache_key(
        self,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        country: str | None = None,
    ) -> str:
        """Generate statistics cache key."""
        country_part = f":{country.upper()}" if country else ""
        return f"ranking:stats:{ruleset}:{type}{country_part}"

    def _get_country_cache_key(self, ruleset: GameMode, page: int = 1) -> str:
        """Generate country ranking cache key."""
        return f"country_ranking:{ruleset}:page:{page}"

    def _get_country_stats_cache_key(self, ruleset: GameMode) -> str:
        """Generate country ranking statistics cache key."""
        return f"country_ranking:stats:{ruleset}"

    def _get_team_cache_key(self, ruleset: GameMode, page: int = 1) -> str:
        """Generate team ranking cache key."""
        return f"team_ranking:{ruleset}:page:{page}"

    def _get_team_stats_cache_key(self, ruleset: GameMode) -> str:
        """Generate team ranking statistics cache key."""
        return f"team_ranking:stats:{ruleset}"

    async def get_cached_ranking(
        self,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        country: str | None = None,
        page: int = 1,
    ) -> list[dict] | None:
        """Get cached ranking data."""
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
        ttl: int | None = None,  # Allow None to use config default
    ) -> None:
        """Cache ranking data."""
        try:
            cache_key = self._get_cache_key(ruleset, type, country, page)
            # Use config file TTL setting
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
        """Get cached statistics."""
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
        ttl: int | None = None,  # Allow None to use config default
    ) -> None:
        """Cache statistics."""
        try:
            cache_key = self._get_stats_cache_key(ruleset, type, country)
            # Use config TTL setting, statistics cache time is longer
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60 * 6  # 6x time
            await self.redis.set(cache_key, safe_json_dumps(stats), ex=ttl)
            logger.debug(f"Cached stats for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching stats: {e}")

    async def get_cached_country_ranking(
        self,
        ruleset: GameMode,
        page: int = 1,
    ) -> list[dict] | None:
        """Get cached country ranking data."""
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
        """Cache country ranking data."""
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
        """Get cached team ranking data."""
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
        """Cache team ranking data."""
        try:
            cache_key = self._get_team_cache_key(ruleset, page)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60
            await self.redis.set(cache_key, safe_json_dumps(ranking_data), ex=ttl)
            logger.debug(f"Cached team ranking data for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching team ranking: {e}")

    async def get_cached_team_stats(self, ruleset: GameMode) -> dict | None:
        """Get cached team ranking statistics."""
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
        """Cache team ranking statistics."""
        try:
            cache_key = self._get_team_stats_cache_key(ruleset)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60 * 6
            await self.redis.set(cache_key, safe_json_dumps(stats), ex=ttl)
            logger.debug(f"Cached team stats for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching team stats: {e}")

    async def get_cached_country_stats(self, ruleset: GameMode) -> dict | None:
        """Get cached country ranking statistics."""
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
        """Cache country ranking statistics."""
        try:
            cache_key = self._get_country_stats_cache_key(ruleset)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60 * 6  # 6x time
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
        max_pages: int | None = None,  # Allow None to use config default
    ) -> None:
        """Refresh ranking cache."""
        if self._refreshing:
            logger.debug(f"Ranking cache refresh already in progress for {ruleset}:{type}")
            return

        # Use config file settings
        if max_pages is None:
            max_pages = settings.ranking_cache_max_pages

        self._refreshing = True
        try:
            logger.info(f"Starting ranking cache refresh for {ruleset}:{type}")

            # Build query conditions
            wheres = [
                col(UserStatistics.mode) == ruleset,
                col(UserStatistics.pp) > 0,
                col(UserStatistics.is_ranked).is_(True),
            ]
            include = UserStatistics.RANKING_INCLUDES.copy()

            if type == "performance":
                order_by = col(UserStatistics.pp).desc()
                include.append("rank_change_since_30_days")
            else:
                order_by = col(UserStatistics.ranked_score).desc()

            if country:
                wheres.append(col(UserStatistics.user).has(country_code=country.upper()))
                include.append("country_rank")

            # Get total user count for statistics
            total_users_query = select(UserStatistics).where(*wheres)
            total_users = len((await session.exec(total_users_query)).all())

            # Calculate statistics
            stats = {
                "total": total_users,
                "total_users": total_users,
                "last_updated": utcnow().isoformat(),
                "type": type,
                "ruleset": ruleset,
                "country": country,
            }

            # Cache statistics
            await self.cache_stats(ruleset, type, stats, country)

            # Paginated data caching
            for page in range(1, max_pages + 1):
                try:
                    statistics_list = await session.exec(
                        select(UserStatistics).where(*wheres).order_by(order_by).limit(50).offset(50 * (page - 1))
                    )

                    statistics_data = statistics_list.all()
                    if not statistics_data:
                        break  # No more data

                    # Convert to response format and ensure proper serialization
                    ranking_data = []
                    for statistics in statistics_data:
                        user_stats_resp = await UserStatisticsModel.transform(statistics, includes=include)

                        user_dict = user_stats_resp

                        # Apply resource proxy processing
                        if settings.enable_asset_proxy:
                            try:
                                user_dict = await replace_asset_urls(user_dict)
                            except Exception as e:
                                logger.warning(f"Asset proxy processing failed for ranking cache: {e}")

                        ranking_data.append(user_dict)

                    # Cache this page's data
                    await self.cache_ranking(ruleset, type, ranking_data, country, page)

                    # Add delay to avoid database overload
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
        """Refresh country ranking cache."""
        if self._refreshing:
            logger.debug(f"Country ranking cache refresh already in progress for {ruleset}")
            return

        if max_pages is None:
            max_pages = settings.ranking_cache_max_pages

        self._refreshing = True
        try:
            logger.info(f"Starting country ranking cache refresh for {ruleset}")

            # Get all countries
            from app.database import User

            countries = (await session.exec(select(User.country_code).distinct())).all()

            # Calculate statistics for each country
            country_stats_list = []
            for country in countries:
                if not country:  # Skip empty country codes
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

                if not statistics:  # Skip countries with no data
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

            # Sort by performance
            country_stats_list.sort(key=lambda x: x["performance"], reverse=True)

            # Calculate statistics
            stats = {
                "total_countries": len(country_stats_list),
                "last_updated": utcnow().isoformat(),
                "ruleset": ruleset,
            }

            # Cache statistics
            await self.cache_country_stats(ruleset, stats)

            # Paginated data caching (50 countries per page)
            page_size = 50
            for page in range(1, max_pages + 1):
                start_idx = (page - 1) * page_size
                end_idx = start_idx + page_size

                page_data = country_stats_list[start_idx:end_idx]
                if not page_data:
                    break  # No more data

                # Cache this page's data
                await self.cache_country_ranking(ruleset, page_data, page)

                # Add delay to avoid Redis overload
                if page < max_pages and page_data:
                    await asyncio.sleep(0.1)

            logger.info(f"Completed country ranking cache refresh for {ruleset}")

        except Exception as e:
            logger.error(f"Country ranking cache refresh failed for {ruleset}: {e}")
        finally:
            self._refreshing = False

    async def refresh_all_rankings(self, session: AsyncSession) -> None:
        """Refresh all ranking caches."""
        game_modes = [GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA]
        ranking_types: list[Literal["performance", "score"]] = ["performance", "score"]

        # Get list of countries to cache (top 20 countries by active user count)
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

        # Global rankings
        for mode in game_modes:
            for ranking_type in ranking_types:
                task = self.refresh_ranking_cache(session, mode, ranking_type)
                refresh_tasks.append(task)

        # Country rankings (top 20 countries only)
        for country in top_countries:
            for mode in game_modes:
                for ranking_type in ranking_types:
                    task = self.refresh_ranking_cache(session, mode, ranking_type, country)
                    refresh_tasks.append(task)

        # Regional rankings
        for mode in game_modes:
            task = self.refresh_country_ranking_cache(session, mode)
            refresh_tasks.append(task)

        # Concurrent refresh with limited parallelism
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent tasks

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
        """Invalidate caches."""
        try:
            deleted_keys = 0

            if ruleset and type:
                # Delete specific user ranking cache
                country_part = f":{country.upper()}" if country else ""
                pattern = f"ranking:{ruleset}:{type}{country_part}:page:*"
                keys = await self.redis.keys(pattern)
                if keys:
                    await self.redis.delete(*keys)
                    deleted_keys += len(keys)
                    logger.info(f"Invalidated {len(keys)} cache keys for {ruleset}:{type}")
            elif ruleset:
                # Delete all caches for specific game mode
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
                # Delete all ranking caches
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
        """Invalidate country ranking cache."""
        try:
            pattern = f"country_ranking:{ruleset}:*" if ruleset else "country_ranking:*"

            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} country ranking cache keys")
        except Exception as e:
            logger.error(f"Error invalidating country cache: {e}")

    async def invalidate_team_cache(self, ruleset: GameMode | None = None) -> None:
        """Invalidate team ranking cache."""
        try:
            pattern = f"team_ranking:{ruleset}:*" if ruleset else "team_ranking:*"

            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} team ranking cache keys")
        except Exception as e:
            logger.error(f"Error invalidating team cache: {e}")

    async def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        try:
            # Get user ranking cache keys
            ranking_keys = await self.redis.keys("ranking:*")
            # Get country ranking cache keys
            country_keys = await self.redis.keys("country_ranking:*")

            total_keys = ranking_keys + country_keys
            total_size = 0

            for key in total_keys[:100]:  # Limit check count to avoid performance issues
                try:
                    size = await self.redis.memory_usage(key)
                    if size:
                        total_size += size
                except Exception:
                    logger.warning(f"Failed to get memory usage for key {key}")
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


# Global cache service instance
_ranking_cache_service: RankingCacheService | None = None


def get_ranking_cache_service(redis: Redis) -> RankingCacheService:
    """Get ranking cache service instance."""
    global _ranking_cache_service
    if _ranking_cache_service is None:
        _ranking_cache_service = RankingCacheService(redis)
    return _ranking_cache_service


async def schedule_ranking_refresh_task(session: AsyncSession, redis: Redis):
    """Scheduled ranking refresh task."""
    # Ranking cache enabled by default unless explicitly disabled
    if not settings.enable_ranking_cache:
        return

    cache_service = get_ranking_cache_service(redis)
    try:
        await cache_service.refresh_all_rankings(session)
    except Exception as e:
        logger.error(f"Scheduled ranking refresh task failed: {e}")
