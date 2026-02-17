"""Replay endpoint module for osu! API v1.

This module provides endpoints for retrieving replay data compatible
with the legacy osu! API v1 specification.
"""

import base64
from datetime import date
from typing import Annotated, Literal

from app.database.counts import ReplayWatchedCount
from app.database.score import Score
from app.dependencies.database import Database
from app.dependencies.storage import StorageService
from app.models.error import ErrorType, RequestError
from app.models.events.score import ReplayDownloadedEvent
from app.models.mods import int_to_mods
from app.models.score import GameMode
from app.plugins import hub

from .router import router

from fastapi import Depends, Query
from fastapi_limiter.depends import RateLimiter
from pydantic import BaseModel
from pyrate_limiter import Duration, Limiter, Rate
from sqlmodel import col, select


class ReplayModel(BaseModel):
    """V1 API replay response model.

    Attributes:
        content: Base64-encoded replay data.
        encoding: Encoding type (always 'base64').
    """

    content: str
    encoding: Literal["base64"] = "base64"


@router.get(
    "/get_replay",
    response_model=ReplayModel,
    name="Get Replay",
    description="Get the replay data for a specific score.",
    dependencies=[Depends(RateLimiter(limiter=Limiter(Rate(10, Duration.MINUTE))))],
)
async def download_replay(
    session: Database,
    beatmap: Annotated[int, Query(..., alias="b", description="Beatmap ID")],
    user: Annotated[str, Query(..., alias="u", description="User")],
    storage_service: StorageService,
    ruleset_id: Annotated[
        int | None,
        Query(
            alias="m",
            description="Ruleset ID",
            ge=0,
        ),
    ] = None,
    score_id: Annotated[int | None, Query(alias="s", description="Score ID")] = None,
    type: Annotated[
        Literal["string", "id"] | None, Query(description="User type: string for username / id for user ID")
    ] = None,
    mods: Annotated[int, Query(description="Score mods")] = 0,
):
    """Download replay data for a score.

    This endpoint retrieves the replay file for a specific score and returns it
    as base64-encoded data. It also increments the replay watch counter for
    the score owner.

    Args:
        session: Database session.
        beatmap: The beatmap ID.
        user: The user (username or ID based on type parameter).
        storage_service: Storage service for file access.
        ruleset_id: Game mode filter (0=osu!, 1=taiko, 2=catch, 3=mania).
        score_id: Specific score ID to retrieve.
        type: Interpret user parameter as 'string' (username) or 'id'.
        mods: Filter by specific mods.

    Returns:
        ReplayModel with base64-encoded replay data.

    Raises:
        RequestError: If score or replay file is not found.
    """
    mods_ = int_to_mods(mods)
    if score_id is not None:
        score_record = await session.get(Score, score_id)
        if score_record is None:
            raise RequestError(ErrorType.SCORE_NOT_FOUND)
    else:
        try:
            score_record = (
                await session.exec(
                    select(Score).where(
                        Score.beatmap_id == beatmap,
                        Score.user_id == user if type == "id" or user.isdigit() else col(Score.user).has(username=user),
                        Score.mods == mods_,
                        Score.gamemode == GameMode.from_int_extra(ruleset_id) if ruleset_id is not None else True,
                    )
                )
            ).first()
            if score_record is None:
                raise RequestError(ErrorType.SCORE_NOT_FOUND)
        except KeyError:
            raise RequestError(ErrorType.INVALID_REQUEST)

    filepath = score_record.replay_filename
    if not await storage_service.is_exists(filepath):
        raise RequestError(ErrorType.REPLAY_FILE_NOT_FOUND)

    replay_watched_count = (
        await session.exec(
            select(ReplayWatchedCount).where(
                ReplayWatchedCount.user_id == score_record.user_id,
                ReplayWatchedCount.year == date.today().year,
                ReplayWatchedCount.month == date.today().month,
            )
        )
    ).first()
    if replay_watched_count is None:
        replay_watched_count = ReplayWatchedCount(
            user_id=score_record.user_id,
            year=date.today().year,
            month=date.today().month,
        )
        session.add(replay_watched_count)
    replay_watched_count.count += 1

    hub.emit(ReplayDownloadedEvent(score_id=score_record.id, owner_user_id=score_record.user_id))

    await session.commit()

    data = await storage_service.read_file(filepath)

    return ReplayModel(content=base64.b64encode(data).decode("utf-8"), encoding="base64")
