from app.database.score import Score
from app.dependencies.database import Database, Redis
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.service.user_cache_service import refresh_user_cache_background

from .router import router

from fastapi import BackgroundTasks, HTTPException


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
    redis: Redis,
    current_user: ClientUser,
    storage_service: StorageService,
):
    """删除成绩

    删除成绩，同时删除对应的统计信息、排行榜分数、pp、回放文件

    参数:
    - score_id: 成绩ID

    错误情况:
    - 404: 找不到指定成绩
    """
    if await current_user.is_restricted(session):
        # avoid deleting the evidence of cheating
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    score = await session.get(Score, score_id)
    if not score or score.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="找不到指定成绩")

    gamemode = score.gamemode
    user_id = score.user_id
    await score.delete(session, storage_service)
    await session.commit()
    background_task.add_task(refresh_user_cache_background, redis, user_id, gamemode)
