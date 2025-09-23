from __future__ import annotations

from app.dependencies.fetcher import get_fetcher
from app.fetcher import Fetcher

from fastapi import APIRouter, Depends

fetcher_router = APIRouter(prefix="/fetcher", include_in_schema=False)


@fetcher_router.get("/callback")
async def callback(code: str, fetcher: Fetcher = Depends(get_fetcher)):
    await fetcher.grant_access_token(code)
    return {"message": "Login successful"}
