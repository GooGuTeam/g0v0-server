"""URL redirect routers for frontend and legacy API compatibility.

This module provides redirect endpoints to:
- Forward user/beatmap/score URLs to the frontend
- Redirect legacy v1 API paths to their proper endpoints
"""

import urllib.parse

from app.config import settings
from app.models.error import ErrorType, RequestError

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

redirect_router = APIRouter(include_in_schema=False)


@redirect_router.get("/users/{path:path}")  # noqa: FAST003
@redirect_router.get("/teams/{team_id}")
@redirect_router.get("/u/{user_id}")
@redirect_router.get("/b/{beatmap_id}")
@redirect_router.get("/s/{beatmapset_id}")
@redirect_router.get("/beatmapsets/{path:path}")
@redirect_router.get("/beatmaps/{path:path}")
@redirect_router.get("/multiplayer/rooms/{room_id}")
@redirect_router.get("/scores/{score_id}")
@redirect_router.get("/home/password-reset")
@redirect_router.get("/oauth/authorize")
async def redirect(request: Request):
    """Redirect various paths to the frontend.

    Handles redirects for user profiles, beatmaps, scores, multiplayer rooms,
    password reset, and OAuth authorization to the configured frontend URL.

    Args:
        request: FastAPI request object.

    Returns:
        RedirectResponse (301) to frontend URL with path and query preserved.
    """
    query_string = request.url.query
    target_path = request.url.path
    redirect_url = urllib.parse.urljoin(str(settings.frontend_url), target_path)
    if query_string:
        redirect_url = f"{redirect_url}?{query_string}"
    return RedirectResponse(
        redirect_url,
        status_code=301,
    )


redirect_api_router = APIRouter(prefix="/api", include_in_schema=False)


@redirect_api_router.get("/v1/get_player_info")
@redirect_api_router.get("/v1/get_player_count")
async def redirect_bancho_py_api(request: Request):
    """Redirect legacy v1 Public API paths to the bancho.py API provided by [plugin](https://github.com/GooGuTeam/g0v0-plugins/tree/main/banchopy_api).

    Handles legacy API paths like /api/v1/get_player_info and redirects them
    to the corresponding endpoints in the banchopy_api plugin.

    This is a compatibility redirect for older clients that may still be using the old API paths for player info.

    Args:
        request: FastAPI request object.

    Returns:
        RedirectResponse (302) to the banchopy_api endpoint.

    Raises:
        RequestError: If path is not a recognized player info API endpoint.
    """
    path = request.url.path.removeprefix("/api/v1/")
    query = f"?{request.url.query}" if request.url.query else ""

    if path in {"get_player_info", "get_player_count"}:
        return RedirectResponse(f"/api/plugins/banchopy_api/{path}{query}", status_code=302)
    raise RequestError(ErrorType.NOT_FOUND)


@redirect_api_router.get("/{path}")
async def redirect_to_api_root(request: Request, path: str):
    """Redirect legacy v1 API paths to proper endpoints.

    Handles legacy API paths like /api/get_beatmaps and redirects them
    to the correct /api/v1/* endpoints.

    Args:
        request: FastAPI request object.
        path: API path segment being requested.

    Returns:
        RedirectResponse (302) to the v1 API endpoint.

    Raises:
        RequestError: If path is not a recognized legacy API endpoint.
    """
    if path in {
        "get_beatmaps",
        "get_user",
        "get_scores",
        "get_user_best",
        "get_user_recent",
        "get_replay",
    }:
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/api/v1/{path}{query}", status_code=302)
    raise RequestError(ErrorType.NOT_FOUND)
