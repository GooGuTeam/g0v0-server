"""Score management endpoints.

Provides API for users to delete their own scores (if enabled).
"""

from app.config import settings
from app.database.score import Score
from app.dependencies.database import Database, Redis
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError
from app.service.user_cache_service import refresh_user_cache_background

from .router import router

from fastapi import BackgroundTasks

if settings.allow_delete_scores:

    @router.delete(
        "/score/{score_id}",
        name="Delete score by ID",
        tags=["Score", "g0v0 API"],
        status_code=204,
        description="Delete a score.",
    )
    async def delete_score(
        session: Database,
        background_task: BackgroundTasks,
        score_id: int,
        redis: Redis,
        current_user: ClientUser,
        storage_service: StorageService,
    ):
        if await current_user.is_restricted(session):
            # Avoid deleting evidence of cheating
            raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

        score = await session.get(Score, score_id)
        if not score or score.user_id != current_user.id:
            raise RequestError(ErrorType.SCORE_NOT_FOUND)

        gamemode = score.gamemode
        user_id = score.user_id
        await score.delete(session, storage_service)
        await session.commit()
        background_task.add_task(refresh_user_cache_background, redis, user_id, gamemode)
