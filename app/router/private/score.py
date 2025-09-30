from __future__ import annotations

from app.database.lazer_user import User
from app.database.score import Score
from app.dependencies.database import Database, get_redis
from app.dependencies.storage import get_storage_service
from app.dependencies.user import get_client_user
from app.service.user_cache_service import refresh_user_cache_background
from app.storage.base import StorageService

from .router import router

from fastapi import BackgroundTasks, Depends, HTTPException, Security
from redis.asyncio import Redis


@router.delete(
    "/score/{score_id}",
    name="删除指定ID的成绩",
    tags=["成绩", "g0v0 API"],
    status_code=204,
)
async def delete_score(
    session: Database,
    background_task: BackgroundTasks,
    score_id: int,
    redis: Redis = Depends(get_redis),
    current_user: User = Security(get_client_user),
    storage_service: StorageService = Depends(get_storage_service),
):
    """删除成绩

    删除成绩，同时删除对应的统计信息、排行榜分数、pp、回放文件

    参数:
    - score_id: 成绩ID

    错误情况:
    - 404: 找不到指定成绩
    """
    score = await session.get(Score, score_id)
    if not score or score.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="找不到指定成绩")

    gamemode = score.gamemode
    user_id = score.user_id
    await score.delete(session, storage_service)
    await session.commit()
    background_task.add_task(refresh_user_cache_background, redis, user_id, gamemode)
