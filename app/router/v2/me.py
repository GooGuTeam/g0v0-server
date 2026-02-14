"""Current user (me) API endpoints.

This module provides endpoints for retrieving information about the currently
authenticated user, including their profile and favourite beatmapsets.
"""

from typing import Annotated

from app.database import FavouriteBeatmapset, User
from app.database.user import UserModel
from app.dependencies.database import Database
from app.dependencies.user import UserAndToken, get_current_user, get_current_user_and_token
from app.helpers import api_doc
from app.models.score import GameMode

from .router import router

from fastapi import Path, Security
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import select

ME_INCLUDES = [*User.USER_INCLUDES, "session_verified", "session_verification_method", "user_preferences"]


class BeatmapsetIds(BaseModel):
    """Response model containing a list of beatmapset IDs.

    Attributes:
        beatmapset_ids: List of beatmapset IDs the user has favourited.
    """

    beatmapset_ids: list[int]


@router.get(
    "/me/beatmapset-favourites",
    response_model=BeatmapsetIds,
    name="Get current user's favourite beatmapset IDs",
    description="Get the list of beatmapset IDs favourited by the currently logged-in user.",
    tags=["Users", "Beatmapsets"],
)
async def get_user_beatmapset_favourites(
    session: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["identify"])],
) -> BeatmapsetIds:
    """Get the list of favourite beatmapset IDs for the current user.

    Args:
        session: Database session dependency.
        current_user: The authenticated user.

    Returns:
        BeatmapsetIds: Object containing the list of favourite beatmapset IDs.
    """
    beatmapset_ids = await session.exec(
        select(FavouriteBeatmapset.beatmapset_id).where(FavouriteBeatmapset.user_id == current_user.id)
    )
    return BeatmapsetIds(beatmapset_ids=list(beatmapset_ids.all()))


@router.get(
    "/me/{ruleset}",
    responses={200: api_doc("Current user info (with specified ruleset statistics)", UserModel, ME_INCLUDES)},
    name="Get current user info (with ruleset)",
    description="Get the currently logged-in user's info with the specified ruleset statistics.",
    tags=["Users"],
)
async def get_user_info_with_ruleset(
    ruleset: Annotated[GameMode, Path(description="The specified ruleset")],
    user_and_token: Annotated[UserAndToken, Security(get_current_user_and_token, scopes=["identify"])],
):
    """Get the current user's info with statistics for a specific ruleset.

    Args:
        ruleset: The game mode/ruleset to retrieve statistics for.
        user_and_token: Tuple containing the user and their auth token.

    Returns:
        UserModel: The user's profile information with ruleset-specific statistics.
    """
    user_resp = await UserModel.transform(
        user_and_token[0], ruleset=ruleset, token_id=user_and_token[1].id, includes=ME_INCLUDES
    )
    return user_resp


@router.get(
    "/me/",
    responses={200: api_doc("Current user info", UserModel, ME_INCLUDES)},
    name="Get current user info",
    description="Get the currently logged-in user's info.",
    tags=["Users"],
)
async def get_user_info_default(
    user_and_token: Annotated[UserAndToken, Security(get_current_user_and_token, scopes=["identify"])],
):
    """Get the current user's info with default statistics.

    Args:
        user_and_token: Tuple containing the user and their auth token.

    Returns:
        UserModel: The user's profile information.
    """
    user_resp = await UserModel.transform(
        user_and_token[0], ruleset=None, token_id=user_and_token[1].id, includes=ME_INCLUDES
    )
    return user_resp


@router.put("/users/{user_id}/page", include_in_schema=False)
async def update_userpage():
    """Redirect to the private user page update endpoint."""
    return RedirectResponse(url="/api/private/user/page", status_code=307)


@router.post("/me/validate-bbcode", include_in_schema=False)
async def validate_bbcode():
    """Redirect to the private BBCode validation endpoint."""
    return RedirectResponse(url="/api/private/user/validate-bbcode", status_code=307)
