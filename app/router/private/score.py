"""Score management endpoints.

Provides API for users to delete their own scores (if enabled).
"""

import sys
from typing import Annotated

from app.config import settings
from app.const import NEW_SCORE_FORMAT_VER
from app.database import BestScore, ScoreModel
from app.database.score import Score
from app.dependencies.database import Database, Redis
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.helpers import api_doc
from app.models.error import ErrorType, RequestError
from app.models.score import GameMode
from app.service.user_cache_service import refresh_user_cache_background

from .router import router

from fastapi import BackgroundTasks, Path, Query
from sqlmodel import col, select

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


@router.get(
    "/top-scores/{ruleset}",
    name="Get top scores",
    tags=["Score", "g0v0 API"],
    description="Get top scores based on performance points (pp). Each page contains 50 scores.",
    responses={
        200: api_doc(
            "Top scores for the specified game mode, ordered by performance points (pp) in descending order.",
            list[ScoreModel],
            ScoreModel.DEFAULT_SCORE_INCLUDES,
        )
    },
)
async def get_top_scores(
    session: Database,
    ruleset: Annotated[GameMode, Path(description="Game mode to filter scores by")],
    page: Annotated[int, Query(description="Page number for pagination", ge=1)] = 1,
):
    wheres = [
        Score.gamemode == ruleset,
        col(Score.id).in_(select(BestScore.score_id).where(BestScore.gamemode == ruleset)),
    ]

    if page == 1:
        cursor = sys.maxsize
    else:
        cursor = (
            await session.exec(
                select(Score.pp).where(*wheres).order_by(col(Score.pp).desc()).offset((page - 1) * 50 - 1).limit(1)
            )
        ).first()
        if cursor is None:
            return []
    scores = (
        await session.exec(
            select(Score).where(*wheres, col(Score.pp) <= cursor).order_by(col(Score.pp).desc()).limit(50)
        )
    ).all()
    return [
        await score.to_resp(session, api_version=NEW_SCORE_FORMAT_VER + 1, includes=ScoreModel.DEFAULT_SCORE_INCLUDES)
        for score in scores
    ]
