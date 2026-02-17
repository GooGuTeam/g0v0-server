from typing import Annotated

from app.dependencies.database import Redis
from app.service.beatmapset_cache_service import (
    BeatmapsetCacheService as OriginBeatmapsetCacheService,
    get_beatmapset_cache_service,
)
from app.service.user_cache_service import (
    UserCacheService as OriginUserCacheService,
    get_user_cache_service,
)

from fast_depends import Depends as FastDepends
from fastapi import Depends


def get_beatmapset_cache_dependency(redis: Redis) -> OriginBeatmapsetCacheService:
    return get_beatmapset_cache_service(redis)


def get_user_cache_dependency(redis: Redis) -> OriginUserCacheService:
    return get_user_cache_service(redis)


BeatmapsetCacheService = Annotated[
    OriginBeatmapsetCacheService, Depends(get_beatmapset_cache_dependency), FastDepends(get_beatmapset_cache_dependency)
]
UserCacheService = Annotated[
    OriginUserCacheService, Depends(get_user_cache_dependency), FastDepends(get_user_cache_dependency)
]
