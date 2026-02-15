"""Beatmapset API endpoints.

This module provides endpoints for searching, retrieving, downloading,
and favouriting beatmapsets.
"""

import re
from typing import Annotated, Literal
from urllib.parse import parse_qs

from app.database import (
    Beatmap,
    Beatmapset,
    BeatmapsetModel,
    FavouriteBeatmapset,
    SearchBeatmapsetsResp,
    User,
)
from app.dependencies.beatmap_download import DownloadService
from app.dependencies.cache import BeatmapsetCacheService, UserCacheService
from app.dependencies.database import Database, Redis
from app.dependencies.fetcher import Fetcher
from app.dependencies.geoip import IPAddress, get_geoip_helper
from app.dependencies.user import ClientUser, get_current_user
from app.helpers import api_doc, asset_proxy_response
from app.models.beatmap import SearchQueryModel
from app.models.error import ErrorType, RequestError
from app.service.beatmapset_cache_service import generate_hash

from .router import router

from fastapi import (
    BackgroundTasks,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    Security,
)
from fastapi.responses import RedirectResponse
from httpx import HTTPError
from sqlmodel import select


@router.get(
    "/beatmapsets/search",
    name="Search beatmapsets",
    tags=["Beatmapsets"],
    response_model=SearchBeatmapsetsResp,
)
@asset_proxy_response
async def search_beatmapset(
    query: Annotated[SearchQueryModel, Query()],
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
    redis: Redis,
    cache_service: BeatmapsetCacheService,
):
    """Search for beatmapsets.

    Args:
        query: Search query parameters.
        request: The FastAPI request object.
        background_tasks: Background tasks handler.
        current_user: The authenticated user.
        fetcher: API fetcher dependency.
        redis: Redis connection dependency.
        cache_service: Beatmapset cache service.

    Returns:
        SearchBeatmapsetsResp: Search results containing matching beatmapsets.

    Raises:
        RequestError: If the search fails.
    """
    params = parse_qs(qs=request.url.query, keep_blank_values=True)
    cursor = {}

    # Parse cursor[field] format parameters
    for k, v in params.items():
        match = re.match(r"cursor\[(\w+)\]", k)
        if match:
            field_name = match.group(1)
            field_value = v[0] if v else None
            if field_value is not None:
                # Convert to appropriate type
                try:
                    if field_name in ["approved_date", "id"]:
                        cursor[field_name] = int(field_value)
                    else:
                        # Try to convert to numeric type
                        try:
                            # First try to convert to integer
                            cursor[field_name] = int(field_value)
                        except ValueError:
                            try:
                                # Then try to convert to float
                                cursor[field_name] = float(field_value)
                            except ValueError:
                                # Finally keep as string
                                cursor[field_name] = field_value
                except ValueError:
                    cursor[field_name] = field_value

    if (
        "recommended" in query.c
        or len(query.r) > 0
        or query.played
        or "follows" in query.c
        or "mine" in query.s
        or "favourites" in query.s
    ):
        # TODO: search locally
        return SearchBeatmapsetsResp(total=0, beatmapsets=[])

    # Generate hash for query and cursor for caching
    query_hash = generate_hash(query.model_dump())
    cursor_hash = generate_hash(cursor)

    # Try to get search results from cache
    cached_result = await cache_service.get_search_from_cache(query_hash, cursor_hash)
    if cached_result:
        sets = SearchBeatmapsetsResp(**cached_result)
        return sets

    try:
        sets = await fetcher.search_beatmapset(query, cursor, redis)

        # Cache search results
        await cache_service.cache_search_result(query_hash, cursor_hash, sets.model_dump())
        return sets
    except HTTPError as e:
        raise RequestError(ErrorType.INTERNAL, {"message": str(e)}) from e


@router.get(
    "/beatmapsets/lookup",
    tags=["Beatmapsets"],
    responses={200: api_doc("Beatmapset details", BeatmapsetModel, BeatmapsetModel.BEATMAPSET_TRANSFORMER_INCLUDES)},
    name="Lookup beatmapset (by beatmap ID)",
    description="Look up a beatmapset by beatmap ID.",
)
@asset_proxy_response
async def lookup_beatmapset(
    db: Database,
    request: Request,
    beatmap_id: Annotated[int, Query(description="Beatmap ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
    cache_service: BeatmapsetCacheService,
):
    """Look up a beatmapset by one of its beatmap IDs.

    Args:
        db: Database session dependency.
        request: The FastAPI request object.
        beatmap_id: The beatmap ID to look up.
        current_user: The authenticated user.
        fetcher: API fetcher dependency.
        cache_service: Beatmapset cache service.

    Returns:
        BeatmapsetModel: The beatmapset details.

    Raises:
        RequestError: If beatmap not found.
    """
    # Try to get from cache first
    cached_resp = await cache_service.get_beatmap_lookup_from_cache(beatmap_id)
    if cached_resp:
        return cached_resp

    try:
        beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=beatmap_id)

        resp = await BeatmapsetModel.transform(
            beatmap.beatmapset, user=current_user, includes=BeatmapsetModel.API_INCLUDES
        )

        # Cache result
        await cache_service.cache_beatmap_lookup(beatmap_id, resp)
        return resp
    except HTTPError as exc:
        raise RequestError(ErrorType.BEATMAP_NOT_FOUND) from exc


@router.get(
    "/beatmapsets/{beatmapset_id}",
    tags=["Beatmapsets"],
    responses={200: api_doc("Beatmapset details", BeatmapsetModel, BeatmapsetModel.BEATMAPSET_TRANSFORMER_INCLUDES)},
    name="Get beatmapset details",
    description="Get details for a single beatmapset.",
)
@asset_proxy_response
async def get_beatmapset(
    db: Database,
    request: Request,
    beatmapset_id: Annotated[int, Path(..., description="Beatmapset ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    fetcher: Fetcher,
    cache_service: BeatmapsetCacheService,
):
    """Get details for a single beatmapset.

    Args:
        db: Database session dependency.
        request: The FastAPI request object.
        beatmapset_id: The beatmapset ID.
        current_user: The authenticated user.
        fetcher: API fetcher dependency.
        cache_service: Beatmapset cache service.

    Returns:
        BeatmapsetModel: The beatmapset details.

    Raises:
        RequestError: If beatmapset not found.
    """
    # Try to get from cache first
    cached_resp = await cache_service.get_beatmapset_from_cache(beatmapset_id)
    if cached_resp:
        return cached_resp

    try:
        beatmapset = await Beatmapset.get_or_fetch(db, fetcher, beatmapset_id)
        await db.refresh(current_user)
        resp = await BeatmapsetModel.transform(beatmapset, includes=BeatmapsetModel.API_INCLUDES, user=current_user)

        # Cache result
        await cache_service.cache_beatmapset(resp)
        return resp
    except HTTPError as exc:
        raise RequestError(ErrorType.BEATMAPSET_NOT_FOUND) from exc


@router.get(
    "/beatmapsets/{beatmapset_id}/download",
    tags=["Beatmapsets"],
    name="Download beatmapset",
    description=(
        "\nDownload a beatmapset file. Intelligently routes based on request IP geolocation, "
        "with load balancing and automatic failover. Chinese IPs use Sayobot mirror, "
        "other regions use Nerinyan and OsuDirect mirrors."
    ),
)
async def download_beatmapset(
    client_ip: IPAddress,
    beatmapset_id: Annotated[int, Path(..., description="Beatmapset ID")],
    current_user: ClientUser,
    download_service: DownloadService,
    no_video: Annotated[bool, Query(alias="noVideo", description="Whether to download the no-video version")] = True,
):
    """Download a beatmapset file.

    Args:
        client_ip: Client IP address for geolocation.
        beatmapset_id: The beatmapset ID to download.
        current_user: The authenticated user.
        download_service: Download service for load balancing.
        no_video: Whether to download the no-video version.

    Returns:
        RedirectResponse: Redirect to the download URL.
    """
    geoip_helper = get_geoip_helper()
    geo_info = geoip_helper.lookup(client_ip)
    country_code = geo_info.get("country_iso", "")

    # Prefer IP geolocation, fall back to user account country code if unavailable
    is_china = country_code == "CN" or (not country_code and current_user.country_code == "CN")

    try:
        # Use load balancing service to get download URL
        download_url = download_service.get_download_url(
            beatmapset_id=beatmapset_id, no_video=no_video, is_china=is_china
        )
        return RedirectResponse(download_url)
    except HTTPException:
        # Fall back to original logic if load balancing service fails
        if is_china:
            return RedirectResponse(
                f"https://dl.sayobot.cn/beatmaps/download/{'novideo' if no_video else 'full'}/{beatmapset_id}"
            )
        else:
            return RedirectResponse(f"https://catboy.best/d/{beatmapset_id}{'n' if no_video else ''}")


@router.post(
    "/beatmapsets/{beatmapset_id}/favourites",
    tags=["Beatmapsets"],
    name="Favourite or unfavourite beatmapset",
    description="\nFavourite or unfavourite a specified beatmapset.",
)
async def favourite_beatmapset(
    db: Database,
    cache_service: UserCacheService,
    beatmapset_id: Annotated[int, Path(..., description="Beatmapset ID")],
    action: Annotated[
        Literal["favourite", "unfavourite"],
        Form(description="Action type: favourite / unfavourite"),
    ],
    current_user: ClientUser,
) -> None:
    """Favourite or unfavourite a beatmapset.

    Args:
        db: Database session dependency.
        cache_service: User cache service.
        beatmapset_id: The beatmapset ID.
        action: Action to perform (favourite or unfavourite).
        current_user: The authenticated user.
    """
    existing_favourite = (
        await db.exec(
            select(FavouriteBeatmapset).where(
                FavouriteBeatmapset.user_id == current_user.id,
                FavouriteBeatmapset.beatmapset_id == beatmapset_id,
            )
        )
    ).first()

    if (action == "favourite" and existing_favourite) or (action == "unfavourite" and not existing_favourite):
        return

    if action == "favourite":
        favourite = FavouriteBeatmapset(user_id=current_user.id, beatmapset_id=beatmapset_id)
        db.add(favourite)
    else:
        await db.delete(existing_favourite)
    await cache_service.invalidate_user_beatmapsets_cache(current_user.id)
    await db.commit()
