"""Beatmapset-related private API endpoints.

Provides endpoints for beatmapset rating and synchronization operations.
"""

from typing import Annotated

from app.database.beatmap import Beatmap
from app.database.beatmapset import Beatmapset
from app.database.beatmapset_ratings import BeatmapRating
from app.database.score import Score
from app.dependencies.database import Database
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError
from app.service.beatmapset_update_service import get_beatmapset_update_service

from .router import router

from fastapi import Body, Depends, Path, Query
from fastapi_limiter.depends import RateLimiter
from pyrate_limiter import Duration, Limiter, Rate
from sqlmodel import col, exists, select


@router.get(
    "/beatmapsets/{beatmapset_id}/can_rate",
    name="Check if user can rate beatmapset",
    response_model=bool,
    tags=["Beatmapset", "g0v0 API"],
    description="Check if the user can rate a beatmapset.",
)
async def can_rate_beatmapset(
    beatmapset_id: int,
    session: Database,
    current_user: ClientUser,
):
    if await current_user.is_restricted(session):
        return False

    user_id = current_user.id
    prev_ratings = (await session.exec(select(BeatmapRating).where(BeatmapRating.user_id == user_id))).first()
    if prev_ratings is not None:
        return False
    query = select(exists()).where(
        Score.user_id == user_id,
        col(Score.beatmap).has(col(Beatmap.beatmapset_id) == beatmapset_id),
        col(Score.passed).is_(True),
    )
    return (await session.exec(query)).first() or False


@router.post(
    "/beatmapsets/{beatmapset_id}/ratings",
    name="Submit beatmapset rating",
    status_code=201,
    tags=["Beatmapset", "g0v0 API"],
    description="Submit a rating for a beatmapset.",
)
async def rate_beatmaps(
    beatmapset_id: int,
    session: Database,
    rating: Annotated[int, Body(..., ge=0, le=10)],
    current_user: ClientUser,
):
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    user_id = current_user.id
    current_beatmapset = (await session.exec(select(exists()).where(Beatmapset.id == beatmapset_id))).first()
    if not current_beatmapset:
        raise RequestError(ErrorType.BEATMAPSET_NOT_FOUND)
    can_rating = await can_rate_beatmapset(beatmapset_id, session, current_user)
    if not can_rating:
        raise RequestError(ErrorType.BEATMAPSET_RATING_FORBIDDEN)
    new_rating: BeatmapRating = BeatmapRating(beatmapset_id=beatmapset_id, user_id=user_id, rating=rating)
    session.add(new_rating)
    await session.commit()


@router.post(
    "/beatmapsets/{beatmapset_id}/sync",
    name="Request beatmapset sync",
    status_code=202,
    tags=["Beatmapset", "g0v0 API"],
    dependencies=[Depends(RateLimiter(limiter=Limiter(Rate(50, Duration.HOUR))))],
    description="Request to sync a beatmapset from Bancho.",
)
async def sync_beatmapset(
    beatmapset_id: Annotated[int, Path(..., description="Beatmapset ID")],
    session: Database,
    current_user: ClientUser,
    immediate: Annotated[bool, Query(description="Whether to sync immediately")] = False,
):
    current_beatmapset = (await session.exec(select(exists()).where(Beatmapset.id == beatmapset_id))).first()
    if not current_beatmapset:
        raise RequestError(ErrorType.BEATMAPSET_NOT_FOUND)
    await get_beatmapset_update_service().add_missing_beatmapset(beatmapset_id, immediate)
