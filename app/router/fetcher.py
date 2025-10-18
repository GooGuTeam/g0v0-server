from app.dependencies.fetcher import Fetcher

from fastapi import APIRouter

fetcher_router = APIRouter(prefix="/fetcher", include_in_schema=False)


@fetcher_router.get("/callback")
async def callback(code: str, fetcher: Fetcher):
    await fetcher.grant_access_token(code)
    return {"message": "Login successful"}
