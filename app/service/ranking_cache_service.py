"""User ranking cache service.

Caches user ranking data to reduce database load.
"""

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Literal

from app.config import settings
from app.const import MANIA_COMMON_KEY_COUNTS, NEW_SCORE_FORMAT_VER
from app.database import ManiaKeyStatistics, User
from app.database.mania_key_statistics import ManiaKeyStatisticsModel
from app.database.statistics import UserStatistics, UserStatisticsModel
from app.helpers import replace_asset_urls, safe_json_dumps, utcnow
from app.log import logger
from app.models.score import GameMode

from redis.asyncio import Redis
from sqlmodel import col, func, select
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
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._background_tasks: set = set()

    def _get_refresh_lock(self, lock_key: str) -> asyncio.Lock:
        """Get or create an async lock for a specific refresh type.

        This allows different ranking types to refresh concurrently while
        preventing duplicate refreshes of the same type.
        """
        if lock_key not in self._refresh_locks:
            self._refresh_locks[lock_key] = asyncio.Lock()
        return self._refresh_locks[lock_key]

    @property
    def is_any_refreshing(self) -> bool:
        """Check if any refresh operation is currently in progress."""
        return any(lock.locked() for lock in self._refresh_locks.values())

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

    def _get_mania_key_cache_key(
        self,
        key_count: int,
        type: Literal["performance", "score"],
        country: str | None = None,
        page: int = 1,
    ) -> str:
        """Generate mania key ranking cache key."""
        country_part = f":{country.upper()}" if country else ""
        return f"mania_key_ranking:{key_count}:{type}{country_part}:page:{page}"

    def _get_mania_key_stats_cache_key(
        self,
        key_count: int,
        type: Literal["performance", "score"],
        country: str | None = None,
    ) -> str:
        """Generate mania key ranking statistics cache key."""
        country_part = f":{country.upper()}" if country else ""
        return f"mania_key_ranking:stats:{key_count}:{type}{country_part}"

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

    async def get_cached_mania_key_ranking(
        self,
        key_count: int,
        type: Literal["performance", "score"],
        country: str | None = None,
        page: int = 1,
    ) -> list[dict] | None:
        """Get cached mania key ranking data."""
        try:
            cache_key = self._get_mania_key_cache_key(key_count, type, country, page)
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached mania key ranking: {e}")
            return None

    async def cache_mania_key_ranking(
        self,
        key_count: int,
        type: Literal["performance", "score"],
        ranking_data: list[dict],
        country: str | None = None,
        page: int = 1,
        ttl: int | None = None,
    ) -> None:
        """Cache mania key ranking data."""
        try:
            cache_key = self._get_mania_key_cache_key(key_count, type, country, page)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60
            await self.redis.set(cache_key, safe_json_dumps(ranking_data), ex=ttl)
            logger.debug(f"Cached mania key ranking data for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching mania key ranking: {e}")

    async def get_cached_mania_key_stats(
        self,
        key_count: int,
        type: Literal["performance", "score"],
        country: str | None = None,
    ) -> dict | None:
        """Get cached mania key ranking statistics."""
        try:
            cache_key = self._get_mania_key_stats_cache_key(key_count, type, country)
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached mania key stats: {e}")
            return None

    async def cache_mania_key_stats(
        self,
        key_count: int,
        type: Literal["performance", "score"],
        stats: dict,
        country: str | None = None,
        ttl: int | None = None,
    ) -> None:
        """Cache mania key ranking statistics."""
        try:
            cache_key = self._get_mania_key_stats_cache_key(key_count, type, country)
            if ttl is None:
                ttl = settings.ranking_cache_expire_minutes * 60 * 6
            await self.redis.set(cache_key, safe_json_dumps(stats), ex=ttl)
            logger.debug(f"Cached mania key stats for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching mania key stats: {e}")

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

    async def cache_top_scores(self, data: list, ruleset: GameMode, page: int = 1) -> None:
        """Cache top scores for a specific game mode."""
        try:
            cache_key = f"top_scores:{ruleset}:page:{page}"
            ttl = settings.ranking_cache_expire_minutes * 60
            await self.redis.set(cache_key, safe_json_dumps(data), ex=ttl)
            logger.debug(f"Cached top scores for {cache_key}")
        except Exception as e:
            logger.error(f"Error caching top scores: {e}")

    async def get_cached_top_scores(self, ruleset: GameMode, page: int = 1) -> list[dict] | None:
        """Get cached top scores for a specific game mode."""
        try:
            cache_key = f"top_scores:{ruleset}:page:{page}"
            cached_data = await self.redis.get(cache_key)

            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting cached top scores: {e}")
            return None

    async def refresh_ranking_cache(
        self,
        session: AsyncSession,
        ruleset: GameMode,
        type: Literal["performance", "score"],
        country: str | None = None,
        max_pages: int | None = None,  # Allow None to use config default
    ) -> None:
        """Refresh ranking cache."""
        country_part = f":{country}" if country else ""
        lock_key = f"ranking:{ruleset}:{type}{country_part}"
        lock = self._get_refresh_lock(lock_key)

        # Skip if already refreshing this specific ranking
        if lock.locked():
            logger.debug(f"Ranking cache refresh already in progress for {ruleset}:{type}{country_part}")
            return

        async with lock:
            # Use config file settings
            if max_pages is None:
                max_pages = settings.ranking_cache_max_pages

            try:
                logger.info(f"Starting ranking cache refresh for {ruleset}:{type}{country_part}")

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

                logger.debug(f"Completed ranking cache refresh for {ruleset}:{type}{country_part}")

            except Exception as e:
                logger.error(f"Ranking cache refresh failed for {ruleset}:{type}{country_part}: {e}")

    async def refresh_country_ranking_cache(
        self,
        session: AsyncSession,
        ruleset: GameMode,
        max_pages: int | None = None,
    ) -> None:
        """Refresh country ranking cache."""
        lock_key = f"country:{ruleset}"
        lock = self._get_refresh_lock(lock_key)

        # Skip if already refreshing this specific country ranking
        if lock.locked():
            logger.debug(f"Country ranking cache refresh already in progress for {ruleset}")
            return

        async with lock:
            if max_pages is None:
                max_pages = settings.ranking_cache_max_pages

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

    async def refresh_top_scores_cache(
        self, session: AsyncSession, ruleset: GameMode, max_pages: int | None = None
    ) -> None:
        """Refresh top scores cache."""
        from app.database import BestScore, Score, ScoreModel

        wheres = [
            Score.gamemode == ruleset,
            col(Score.id).in_(select(BestScore.score_id).where(BestScore.gamemode == ruleset)),
        ]

        lock_key = f"top_scores:{ruleset}"
        lock = self._get_refresh_lock(lock_key)

        # Skip if already refreshing this specific top scores cache
        if lock.locked():
            logger.debug(f"Top scores cache refresh already in progress for {ruleset}")
            return

        async with lock:
            if max_pages is None:
                max_pages = settings.top_score_cache_max_pages

            try:
                logger.info(f"Starting top scores cache refresh for {ruleset}")

                # Get top scores
                for page in range(1, max_pages + 1):
                    if page == 1:
                        cursor = sys.maxsize
                    else:
                        cursor = (
                            await session.exec(
                                select(Score.pp)
                                .where(*wheres)
                                .order_by(col(Score.pp).desc())
                                .offset((page - 1) * 50 - 1)
                                .limit(1)
                            )
                        ).first()
                        if cursor is None:
                            break
                    scores = (
                        await session.exec(
                            select(Score)
                            .where(*wheres, col(Score.pp) <= cursor)
                            .order_by(col(Score.pp).desc())
                            .limit(50)
                        )
                    ).all()
                    data = [
                        await score.to_resp(
                            session, api_version=NEW_SCORE_FORMAT_VER + 1, includes=ScoreModel.DEFAULT_SCORE_INCLUDES
                        )
                        for score in scores
                    ]
                    await self.cache_top_scores(data, ruleset, page)

                    await asyncio.sleep(0.1)

                logger.info(f"Completed top scores cache refresh for {ruleset}")

            except Exception as e:
                logger.error(f"Top scores cache refresh failed for {ruleset}: {e}")

    async def refresh_mania_key_ranking_cache(
        self,
        session: AsyncSession,
        key_count: int,
        type: Literal["performance", "score"],
        country: str | None = None,
        max_pages: int | None = None,
    ) -> None:
        """Refresh mania key-specific ranking cache.

        Args:
            session: Database session.
            key_count: The mania key count (e.g. 4, 7).
            type: Ranking sort type.
            country: Optional country code for country rankings.
            max_pages: Maximum pages to cache.
        """
        country_part = f":{country}" if country else ""
        lock_key = f"mania_key:{key_count}:{type}{country_part}"
        lock = self._get_refresh_lock(lock_key)

        # Skip if already refreshing this specific mania key ranking
        if lock.locked():
            logger.debug(f"Mania key ranking cache refresh already in progress for {key_count}K:{type}{country_part}")
            return

        async with lock:
            if max_pages is None:
                max_pages = settings.ranking_cache_max_pages

            try:
                logger.info(f"Starting mania key ranking cache refresh for {key_count}K:{type}{country_part}")

                wheres = [
                    col(ManiaKeyStatistics.key_count) == key_count,
                    col(ManiaKeyStatistics.pp) > 0,
                    col(ManiaKeyStatistics.is_ranked).is_(True),
                ]
                include = ManiaKeyStatistics.RANKING_INCLUDES.copy()

                if type == "performance":
                    order_by = col(ManiaKeyStatistics.pp).desc()
                else:
                    order_by = col(ManiaKeyStatistics.ranked_score).desc()

                if country:
                    wheres.append(col(ManiaKeyStatistics.user).has(country_code=country.upper()))
                    include.append("country_rank")

                total_users_query = select(ManiaKeyStatistics).where(
                    *wheres,
                    ~User.is_restricted_query(col(ManiaKeyStatistics.user_id)),
                )
                total_users = len((await session.exec(total_users_query)).all())

                stats = {
                    "total": total_users,
                    "total_users": total_users,
                    "last_updated": utcnow().isoformat(),
                    "type": type,
                    "ruleset": f"mania_{key_count}",
                    "country": country,
                }

                await self.cache_mania_key_stats(key_count, type, stats, country)

                for page in range(1, max_pages + 1):
                    try:
                        statistics_list = await session.exec(
                            select(ManiaKeyStatistics)
                            .where(
                                *wheres,
                                ~User.is_restricted_query(col(ManiaKeyStatistics.user_id)),
                            )
                            .order_by(order_by)
                            .limit(50)
                            .offset(50 * (page - 1))
                        )

                        data = statistics_list.all()
                        if not data:
                            break

                        ranking_data = []
                        for stat in data:
                            resp = await ManiaKeyStatisticsModel.transform(stat, includes=include)

                            if settings.enable_asset_proxy:
                                try:
                                    resp = await replace_asset_urls(resp)
                                except Exception as e:
                                    logger.warning(f"Asset proxy processing failed for mania key ranking: {e}")

                            ranking_data.append(resp)

                        await self.cache_mania_key_ranking(key_count, type, ranking_data, country, page)

                        if page < max_pages:
                            await asyncio.sleep(0.1)

                    except Exception as e:
                        logger.error(f"Error caching mania key {key_count}K page {page} for {type}: {e}")

                logger.info(f"Completed mania key ranking cache refresh for {key_count}K:{type}{country_part}")

            except Exception as e:
                logger.error(f"Mania key ranking cache refresh failed for {key_count}K:{type}{country_part}: {e}")

    async def refresh_all_rankings(self, session: AsyncSession) -> None:
        """Refresh all ranking caches."""
        game_modes = [GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA, GameMode.OSURX]
        ranking_types: list[Literal["performance", "score"]] = ["performance", "score"]

        # Get list of countries to cache (top 20 countries by active user count)
        from app.database import User

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

            task = self.refresh_top_scores_cache(session, mode)
            refresh_tasks.append(task)

        # Mania key-specific rankings (for common key counts: 2K-10K)
        for key_count in MANIA_COMMON_KEY_COUNTS:
            for ranking_type in ranking_types:
                task = self.refresh_mania_key_ranking_cache(session, key_count, ranking_type)
                refresh_tasks.append(task)

        # Concurrent refresh with limited parallelism
        semaphore = asyncio.Semaphore(15)

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

                # Also invalidate mania key rankings when ruleset is mania
                if ruleset.is_mania():
                    mk_deleted = await self._invalidate_mania_key_pattern(
                        f"mania_key_ranking:*:{type}{country_part}:page:*",
                    )
                    deleted_keys += mk_deleted
                    mk_stats_deleted = await self._invalidate_mania_key_pattern(
                        f"mania_key_ranking:stats:*:{type}{country_part}",
                    )
                    deleted_keys += mk_stats_deleted
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

                # Also invalidate all mania key rankings when ruleset is mania
                if ruleset.is_mania():
                    mk_deleted = await self._invalidate_mania_key_pattern("mania_key_ranking:*")
                    deleted_keys += mk_deleted
            else:
                # Delete all ranking caches (including mania key rankings)
                patterns = ["ranking:*"]
                if include_country_ranking:
                    patterns.append("country_ranking:*")

                for pattern in patterns:
                    keys = await self.redis.keys(pattern)
                    if keys:
                        await self.redis.delete(*keys)
                        deleted_keys += len(keys)

                # Also delete all mania key ranking caches
                mk_deleted = await self._invalidate_mania_key_pattern("mania_key_ranking:*")
                deleted_keys += mk_deleted

                logger.info(f"Invalidated all {deleted_keys} ranking cache keys")

        except Exception as e:
            logger.error(f"Error invalidating cache: {e}")

    async def _invalidate_mania_key_pattern(self, pattern: str) -> int:
        """Invalidate cache keys matching a mania key pattern.

        Args:
            pattern: Redis key glob pattern.

        Returns:
            Number of keys deleted.
        """
        keys = await self.redis.keys(pattern)
        if keys:
            await self.redis.delete(*keys)
            logger.info(f"Invalidated {len(keys)} mania key cache keys matching '{pattern}'")
            return len(keys)
        return 0

    async def invalidate_mania_key_cache(
        self,
        key_count: int | None = None,
        type: Literal["performance", "score"] | None = None,
    ) -> None:
        """Invalidate mania key ranking caches.

        Args:
            key_count: Specific key count to invalidate. If None, invalidates all.
            type: Specific ranking type to invalidate. If None, invalidates both.
        """
        try:
            base = f"mania_key_ranking:{key_count}" if key_count is not None else "mania_key_ranking"

            if type is not None:
                patterns = [f"{base}:{type}:*", f"{base}:stats:{type}*"]
            else:
                patterns = [f"{base}:*"]

            total_deleted = 0
            for pattern in patterns:
                total_deleted += await self._invalidate_mania_key_pattern(pattern)

            logger.info(f"Invalidated {total_deleted} mania key ranking cache keys")
        except Exception as e:
            logger.error(f"Error invalidating mania key cache: {e}")

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
            # Get mania key ranking cache keys
            mania_key_keys = await self.redis.keys("mania_key_ranking:*")

            total_keys = ranking_keys + country_keys + mania_key_keys
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
                "cached_mania_key_rankings": len(mania_key_keys),
                "total_cached_rankings": len(total_keys),
                "estimated_total_size_mb": (round(total_size / 1024 / 1024, 2) if total_size > 0 else 0),
                "refreshing": self.is_any_refreshing,
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
