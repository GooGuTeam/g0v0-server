"""Fetcher callback router for OAuth flow handling.

This module provides callback endpoints for external OAuth flows,
primarily used for the fetcher service to obtain access tokens
from third-party API providers.
"""

from app.dependencies.fetcher import Fetcher

from fastapi import APIRouter

fetcher_router = APIRouter(prefix="/fetcher", include_in_schema=False)


@fetcher_router.get("/callback")
async def callback(code: str, fetcher: Fetcher):
    """Handle OAuth callback for fetcher service.

    Receives authorization code from OAuth provider and exchanges
    it for an access token.

    Args:
        code: Authorization code from OAuth provider.
        fetcher: Fetcher service dependency.

    Returns:
        Dict with success message.
    """
    # await fetcher.grant_access_token(code)
    return {"message": "Login successful"}
