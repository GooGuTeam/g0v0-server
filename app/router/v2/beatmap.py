"""Beatmap API endpoints.

This module provides endpoints for retrieving beatmap information, including
single beatmap lookups, batch retrieval, and difficulty attribute calculations.
"""

import asyncio
import hashlib
import json
from typing import Annotated

from app.calculator import get_calculator
from app.calculators.performance import ConvertError
from app.database import (
    Beatmap,
    BeatmapModel,
    User,
)
from app.database.beatmap import calculate_beatmap_attributes
from app.dependencies.database import Database, Redis
from app.dependencies.fetcher import Fetcher
from app.dependencies.user import get_current_user
from app.helpers import api_doc, asset_proxy_response
from app.models.error import ErrorType, RequestError
from app.models.mods import APIMod, int_to_mods
from app.models.performance import (
    DifficultyAttributes,
    DifficultyAttributesUnion,
)
from app.models.score import (
    GameMode,
)

from .router import router

from fastapi import Path, Query, Security
from httpx import HTTPError, HTTPStatusError
from sqlmodel import col, select


@router.get(
    "/beatmaps/lookup",
    tags=["Beatmaps"],
    name="Lookup single beatmap",
    responses={200: api_doc("Single beatmap details.", BeatmapModel, BeatmapModel.TRANSFORMER_INCLUDES)},
    description="Lookup a single beatmap by ID / MD5 / filename. At least one of id / checksum / filename is required.",
)
@asset_proxy_response
async def lookup_beatmap(
    db: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
    id: Annotated[int | None, Query(alias="id", description="Beatmap ID")] = None,
    md5: Annotated[str | None, Query(alias="checksum", description="Beatmap file MD5")] = None,
    filename: Annotated[str | None, Query(alias="filename", description="Beatmap filename")] = None,
):
    """Lookup a single beatmap by various identifiers.

    Args:
        db: Database session dependency.
        current_user: The authenticated user.
        fetcher: API fetcher dependency.
        id: Beatmap ID (optional).
        md5: Beatmap file MD5 checksum (optional).
        filename: Beatmap filename (optional).

    Returns:
        BeatmapModel: The beatmap details.

    Raises:
        RequestError: If no lookup arguments provided or beatmap not found.
    """
    if id is None and md5 is None and filename is None:
        raise RequestError(ErrorType.BEATMAP_LOOKUP_ARGS_MISSING)
    try:
        beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=id, md5=md5)
    except HTTPError:
        raise RequestError(ErrorType.BEATMAP_NOT_FOUND)

    if beatmap is None:
        raise RequestError(ErrorType.BEATMAP_NOT_FOUND)
    await db.refresh(current_user)

    return await BeatmapModel.transform(beatmap, user=current_user, includes=BeatmapModel.TRANSFORMER_INCLUDES)


@router.get(
    "/beatmaps/{beatmap_id}",
    tags=["Beatmaps"],
    name="Get beatmap details",
    responses={200: api_doc("Single beatmap details.", BeatmapModel, BeatmapModel.TRANSFORMER_INCLUDES)},
    description="Get details for a single beatmap.",
)
@asset_proxy_response
async def get_beatmap(
    db: Database,
    beatmap_id: Annotated[int, Path(..., description="Beatmap ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
):
    """Get details for a single beatmap by ID.

    Args:
        db: Database session dependency.
        beatmap_id: The beatmap ID.
        current_user: The authenticated user.
        fetcher: API fetcher dependency.

    Returns:
        BeatmapModel: The beatmap details.

    Raises:
        RequestError: If beatmap not found.
    """
    try:
        beatmap = await Beatmap.get_or_fetch(db, fetcher, beatmap_id)
        await db.refresh(current_user)
        return await BeatmapModel.transform(
            beatmap,
            user=current_user,
            includes=BeatmapModel.TRANSFORMER_INCLUDES,
        )
    except HTTPError:
        raise RequestError(ErrorType.BEATMAP_NOT_FOUND)


@router.get(
    "/beatmaps/",
    tags=["Beatmaps"],
    name="Batch get beatmaps",
    responses={
        200: api_doc(
            "Beatmap list",
            {"beatmaps": list[BeatmapModel]},
            BeatmapModel.TRANSFORMER_INCLUDES,
            name="BatchBeatmapResponse",
        )
    },
    description="Batch get beatmaps. If ids[] is not provided, returns up to 50 beatmaps sorted by last updated time.",
)
@asset_proxy_response
async def batch_get_beatmaps(
    db: Database,
    beatmap_ids: Annotated[
        list[int],
        Query(alias="ids[]", default_factory=list, description="List of beatmap IDs (max 50)"),
    ],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
):
    """Batch retrieve multiple beatmaps.

    Args:
        db: Database session dependency.
        beatmap_ids: List of beatmap IDs to retrieve (max 50).
        current_user: The authenticated user.
        fetcher: API fetcher dependency.

    Returns:
        dict: Dictionary containing list of beatmaps.
    """
    if not beatmap_ids:
        beatmaps = (await db.exec(select(Beatmap).order_by(col(Beatmap.last_updated).desc()).limit(50))).all()
    else:
        beatmaps = list((await db.exec(select(Beatmap).where(col(Beatmap.id).in_(beatmap_ids)).limit(50))).all())
        not_found_beatmaps = [bid for bid in beatmap_ids if bid not in [bm.id for bm in beatmaps]]
        beatmaps.extend(
            beatmap
            for beatmap in await asyncio.gather(
                *[Beatmap.get_or_fetch(db, fetcher, bid=bid) for bid in not_found_beatmaps],
                return_exceptions=True,
            )
            if isinstance(beatmap, Beatmap)
        )
        for beatmap in beatmaps:
            await db.refresh(beatmap)
    await db.refresh(current_user)
    return {
        "beatmaps": [
            await BeatmapModel.transform(bm, user=current_user, includes=BeatmapModel.TRANSFORMER_INCLUDES)
            for bm in beatmaps
        ]
    }


@router.post(
    "/beatmaps/{beatmap_id}/attributes",
    tags=["Beatmaps"],
    name="Calculate beatmap attributes",
    response_model=DifficultyAttributesUnion,
    description=(
        "Calculate difficulty attributes (difficulty/PP-related attributes) for a beatmap with specified mods/ruleset."
    ),
)
async def get_beatmap_attributes(
    db: Database,
    beatmap_id: Annotated[int, Path(..., description="Beatmap ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    mods: Annotated[
        list[str],
        Query(
            default_factory=list,
            description="Mods list; can be integer bitmask (single element) or JSON/acronym",
        ),
    ],
    redis: Redis,
    fetcher: Fetcher,
    ruleset: Annotated[
        GameMode | None, Query(description="Specify ruleset; if empty, uses the beatmap's own mode")
    ] = None,
    ruleset_id: Annotated[int | None, Query(description="Specify ruleset by number (alternative to ruleset)")] = None,
):
    """Calculate difficulty attributes for a beatmap.

    Args:
        db: Database session dependency.
        beatmap_id: The beatmap ID.
        current_user: The authenticated user.
        mods: List of mods to apply.
        redis: Redis connection dependency.
        fetcher: API fetcher dependency.
        ruleset: Game mode to calculate for.
        ruleset_id: Alternative way to specify game mode by number.

    Returns:
        DifficultyAttributes: The calculated difficulty attributes.

    Raises:
        RequestError: If beatmap not found or calculation not supported.
    """
    mods_ = []
    if mods and mods[0].isdigit():
        mods_ = int_to_mods(int(mods[0]))
    else:
        for i in mods:
            try:
                mods_.append(json.loads(i))
            except json.JSONDecodeError:
                mods_.append(APIMod(acronym=i, settings={}))
    mods_.sort(key=lambda x: x["acronym"])
    if ruleset_id is not None and ruleset is None:
        ruleset = GameMode.from_int(ruleset_id)
    if ruleset is None:
        beatmap_db = await Beatmap.get_or_fetch(db, fetcher, beatmap_id)
        ruleset = beatmap_db.mode
    key = (
        f"beatmap:{beatmap_id}:{ruleset}:"
        f"{hashlib.md5(str(mods_).encode(), usedforsecurity=False).hexdigest()}:attributes"
    )
    if await redis.exists(key):
        return DifficultyAttributes.model_validate_json(await redis.get(key))  # pyright: ignore[reportArgumentType]

    if await get_calculator().can_calculate_difficulty(ruleset) is False:
        raise RequestError(ErrorType.CANNOT_CALCULATE_DIFFICULTY)

    try:
        return await calculate_beatmap_attributes(beatmap_id, ruleset, mods_, redis, fetcher)
    except HTTPStatusError:
        raise RequestError(ErrorType.BEATMAP_NOT_FOUND)
    except ConvertError as e:
        raise RequestError(ErrorType.INVALID_REQUEST, {"error": str(e)}, status_code=400)
