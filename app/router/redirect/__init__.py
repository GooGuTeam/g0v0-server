from __future__ import annotations

import urllib.parse

from app.config import settings

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

redirect_router = APIRouter(include_in_schema=False)


@redirect_router.get("/users/{path:path}")
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


@redirect_api_router.get("/{path}")
async def redirect_to_api_root(request: Request, path: str):
    if path in {
        "get_beatmaps",
        "get_user",
        "get_scores",
        "get_user_best",
        "get_user_recent",
        "get_replay",
    }:
        return RedirectResponse(f"/api/v1/{path}?{request.url.query}", status_code=302)
    raise HTTPException(404, detail="Not Found")
