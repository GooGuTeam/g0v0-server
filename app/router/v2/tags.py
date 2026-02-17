"""Beatmap tag voting API endpoints.

This module provides endpoints for managing user-submitted tags on beatmaps,
including viewing available tags and voting/unvoting on beatmap tags.
"""

from typing import Annotated

from app.database.beatmap import Beatmap
from app.database.beatmap_tags import BeatmapTagVote
from app.database.score import Score
from app.database.user import User
from app.dependencies.database import Database
from app.dependencies.user import get_client_user
from app.models.error import ErrorType, RequestError
from app.models.score import Rank
from app.models.tags import BeatmapTags, get_all_tags, get_tag_by_id

from .router import router

from fastapi import Depends, Path
from pydantic import BaseModel
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession


class APITagCollection(BaseModel):
    """Collection of available beatmap tags.

    Attributes:
        tags: List of all available beatmap tags.
    """

    tags: list[BeatmapTags]


@router.get(
    "/tags",
    tags=["User Tags"],
    response_model=APITagCollection,
    name="Get all tags",
    description="Get all available beatmap tags.",
)
async def router_get_all_tags() -> APITagCollection:
    """Retrieve all available beatmap tags.

    Returns:
        APITagCollection: Collection containing all available tags.
    """
    return APITagCollection(tags=get_all_tags())


async def check_user_can_vote(user: User, beatmap_id: int, session: AsyncSession) -> bool:
    """Check if a user is eligible to vote on a beatmap's tags.

    A user can vote if they have a passing score (not F or D rank) on the beatmap
    in its original game mode.

    Args:
        user: The user attempting to vote.
        beatmap_id: The beatmap ID to check voting eligibility for.
        session: The database session.

    Returns:
        bool: True if the user can vote, False otherwise.
    """
    user_beatmap_score = (
        await session.exec(
            select(exists())
            .where(Score.beatmap_id == beatmap_id)
            .where(Score.user_id == user.id)
            .where(col(Score.rank).not_in([Rank.F, Rank.D]))
            .where(col(Score.beatmap).has(col(Beatmap.mode) == Score.gamemode))
        )
    ).first()
    return user_beatmap_score is not None


@router.put(
    "/beatmaps/{beatmap_id}/tags/{tag_id}",
    tags=["User Tags"],
    status_code=204,
    name="Vote for beatmap tag",
    description="Add a tag vote for the specified beatmap.",
)
async def vote_beatmap_tags(
    beatmap_id: Annotated[int, Path(..., description="Beatmap ID")],
    tag_id: Annotated[int, Path(..., description="Tag ID")],
    session: Database,
    current_user: Annotated[User, Depends(get_client_user)],
) -> None:
    """Vote for a tag on a beatmap.

    Args:
        beatmap_id: The ID of the beatmap to tag.
        tag_id: The ID of the tag to vote for.
        session: Database session dependency.
        current_user: The authenticated user.

    Raises:
        RequestError: If the user is restricted, beatmap not found, or tag not found.
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    try:
        get_tag_by_id(tag_id)
        beatmap = (await session.exec(select(exists()).where(Beatmap.id == beatmap_id))).first()
        if beatmap is None or (not beatmap):
            raise RequestError(ErrorType.BEATMAP_NOT_FOUND)
        previous_votes = (
            await session.exec(
                select(BeatmapTagVote)
                .where(BeatmapTagVote.beatmap_id == beatmap_id)
                .where(BeatmapTagVote.tag_id == tag_id)
                .where(BeatmapTagVote.user_id == current_user.id)
            )
        ).first()
        if previous_votes is None and check_user_can_vote(current_user, beatmap_id, session):
            new_vote = BeatmapTagVote(tag_id=tag_id, beatmap_id=beatmap_id, user_id=current_user.id)
            session.add(new_vote)
        await session.commit()
    except ValueError:
        raise RequestError(ErrorType.TAG_NOT_FOUND)


@router.delete(
    "/beatmaps/{beatmap_id}/tags/{tag_id}",
    tags=["User Tags", "Beatmaps"],
    status_code=204,
    name="Remove beatmap tag vote",
    description="Remove your tag vote from the specified beatmap.",
)
async def devote_beatmap_tags(
    beatmap_id: Annotated[int, Path(..., description="Beatmap ID")],
    tag_id: Annotated[int, Path(..., description="Tag ID")],
    session: Database,
    current_user: Annotated[User, Depends(get_client_user)],
) -> None:
    """Remove a tag vote from a beatmap.

    Args:
        beatmap_id: The ID of the beatmap.
        tag_id: The ID of the tag to remove the vote from.
        session: Database session dependency.
        current_user: The authenticated user.

    Raises:
        RequestError: If the user is restricted, beatmap not found, or tag not found.
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    try:
        tag = get_tag_by_id(tag_id)
        assert tag is not None
        beatmap = await session.get(Beatmap, beatmap_id)
        if beatmap is None:
            raise RequestError(ErrorType.BEATMAP_NOT_FOUND)
        previous_votes = (
            await session.exec(
                select(BeatmapTagVote)
                .where(BeatmapTagVote.beatmap_id == beatmap_id)
                .where(BeatmapTagVote.tag_id == tag_id)
                .where(BeatmapTagVote.user_id == current_user.id)
            )
        ).first()
        if previous_votes is not None:
            await session.delete(previous_votes)
        await session.commit()
    except ValueError:
        raise RequestError(ErrorType.TAG_NOT_FOUND)
