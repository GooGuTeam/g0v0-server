"""Base classes and utilities for osu! API fetchers.

This module provides the foundational classes for fetching data from the osu! API,
including OAuth token management, rate limiting, and HTTP request handling.

Classes:
    TokenAuthError: Exception raised when token authorization fails.
    PassiveRateLimiter: A rate limiter that handles 429 responses passively.
    BaseFetcher: Base class for all fetchers with OAuth and request handling.
"""

import asyncio
from datetime import datetime
import time

from app.dependencies.database import get_redis
from app.log import fetcher_logger

from httpx import AsyncClient, HTTPStatusError, TimeoutException


class TokenAuthError(Exception):
    """Exception raised when token authorization fails."""

    pass


class PassiveRateLimiter:
    """A passive rate limiter that handles 429 responses.

    When a 429 response is received, this limiter reads the Retry-After header
    and pauses all subsequent requests until the rate limit period expires.

    Attributes:
        _lock: An asyncio lock for thread-safe operations.
        _retry_after_time: The timestamp when the rate limit expires.
        _waiting_tasks: A set of tasks waiting for the rate limit to expire.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._retry_after_time: float | None = None
        self._waiting_tasks: set[asyncio.Task] = set()

    async def wait_if_limited(self) -> None:
        """Wait if currently rate limited.

        Blocks the caller until the rate limit period expires. If not rate
        limited, returns immediately.
        """
        async with self._lock:
            if self._retry_after_time is not None:
                current_time = time.time()
                if current_time < self._retry_after_time:
                    wait_seconds = self._retry_after_time - current_time
                    logger.warning(f"Rate limited, waiting {wait_seconds:.2f} seconds")
                    await asyncio.sleep(wait_seconds)
                    self._retry_after_time = None

    async def handle_rate_limit(self, retry_after: str | int | None) -> None:
        """Handle a 429 response and set the rate limit time.

        Args:
            retry_after: The value of the Retry-After header, which can be
                either a number of seconds or an HTTP date string.
        """
        async with self._lock:
            if retry_after is None:
                # Default to 60 seconds if no Retry-After header is provided
                wait_seconds = 60
            elif isinstance(retry_after, int):
                wait_seconds = retry_after
            elif retry_after.isdigit():
                wait_seconds = int(retry_after)
            else:
                # Try to parse HTTP date format
                try:
                    retry_time = datetime.strptime(retry_after, "%a, %d %b %Y %H:%M:%S %Z")
                    wait_seconds = max(0, (retry_time - datetime.utcnow()).total_seconds())
                except ValueError:
                    # Default to 60 seconds if parsing fails
                    wait_seconds = 60

            self._retry_after_time = time.time() + wait_seconds
            logger.warning(f"Rate limit triggered, will retry after {wait_seconds} seconds")


logger = fetcher_logger("Fetcher")


class BaseFetcher:
    """Base class for all osu! API fetchers.

    Provides OAuth token management, request handling, and rate limiting
    functionality. All fetchers should inherit from this class.

    Attributes:
        client_id: The OAuth client ID.
        client_secret: The OAuth client secret.
        access_token: The current OAuth access token.
        refresh_token: The OAuth refresh token (reserved for user-based fetchers).
        token_expiry: Unix timestamp when the access token expires.
        callback_url: The OAuth callback URL (reserved for user-based fetchers).
        scope: The OAuth scopes to request.
    """

    # Class-level rate limiter shared across all instances
    _rate_limiter = PassiveRateLimiter()

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scope: list[str] = ["public"],
        callback_url: str = "",
    ):
        """Initialize the fetcher with OAuth credentials.

        Args:
            client_id: The OAuth client ID.
            client_secret: The OAuth client secret.
            scope: The OAuth scopes to request. Defaults to ["public"].
            callback_url: The OAuth callback URL. Defaults to "".
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: str = ""
        self.refresh_token: str = ""
        self.token_expiry: int = 0
        self.callback_url: str = callback_url
        self.scope = scope
        self._token_lock = asyncio.Lock()

    # NOTE: Reserve for user-based fetchers
    # @property
    # def authorize_url(self) -> str:
    #     return (
    #         f"https://osu.ppy.sh/oauth/authorize?client_id={self.client_id}"
    #         f"&response_type=code&scope={quote(' '.join(self.scope))}"
    #         f"&redirect_uri={self.callback_url}"
    #     )

    @property
    def header(self) -> dict[str, str]:
        """Get the HTTP headers for API requests.

        Returns:
            A dictionary containing the Authorization and Content-Type headers.
        """
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def request_api(self, url: str, method: str = "GET", **kwargs) -> dict:
        """Send an API request with passive rate limiting support.

        This method handles token refresh, rate limiting (429 responses),
        and unauthorized (401) responses automatically.

        Args:
            url: The API endpoint URL.
            method: The HTTP method to use. Defaults to "GET".
            **kwargs: Additional arguments to pass to the HTTP client.

        Returns:
            The JSON response from the API as a dictionary.

        Raises:
            TokenAuthError: If authorization fails after retries.
            HTTPStatusError: If an HTTP error other than 429 or 401 occurs.
        """
        await self.ensure_valid_access_token()

        headers = kwargs.pop("headers", {}).copy()
        attempt = 0

        while attempt < 2:
            # Wait for rate limit before sending request
            await self._rate_limiter.wait_if_limited()

            request_headers = {**headers, **self.header}
            request_kwargs = kwargs.copy()

            async with AsyncClient() as client:
                try:
                    response = await client.request(
                        method,
                        url,
                        headers=request_headers,
                        **request_kwargs,
                    )
                    response.raise_for_status()
                    return response.json()

                except HTTPStatusError as e:
                    # Handle 429 rate limit response
                    if e.response.status_code == 429:
                        retry_after = e.response.headers.get("Retry-After")
                        logger.warning(f"Rate limited for {url}, Retry-After: {retry_after}")
                        await self._rate_limiter.handle_rate_limit(retry_after)
                        # Retry without incrementing attempt counter
                        continue

                    # Handle 401 unauthorized response
                    if e.response.status_code == 401:
                        attempt += 1
                        logger.warning(f"Received 401 error for {url}, attempt {attempt}")
                        await self._handle_unauthorized()
                        continue

                    # Re-raise other HTTP errors
                    raise

        await self._clear_access_token()
        logger.warning(f"Failed to authorize after retries for {url}, cleaned up tokens")
        await self.grant_access_token()
        raise TokenAuthError(f"Failed to authorize after retries for {url}")

    def is_token_expired(self) -> bool:
        """Check if the current access token is expired.

        Returns:
            True if the token is expired or not set, False otherwise.
        """
        if not isinstance(self.token_expiry, int):
            return True
        return self.token_expiry <= int(time.time()) or not self.access_token

    async def grant_access_token(self, retries: int = 3, backoff: float = 1.0) -> None:
        """Request a new access token using client credentials.

        This method uses the OAuth client credentials grant flow to obtain
        a new access token from the osu! API.

        Args:
            retries: The number of retry attempts. Defaults to 3.
            backoff: The base backoff time in seconds between retries.
                Defaults to 1.0.

        Raises:
            TokenAuthError: If the token request fails after all retries.
        """
        last_error: Exception | None = None
        async with AsyncClient(timeout=30.0) as client:
            for attempt in range(1, retries + 1):
                try:
                    response = await client.post(
                        "https://osu.ppy.sh/oauth/token",
                        data={
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "grant_type": "client_credentials",
                            "scope": "public",
                        },
                    )
                    response.raise_for_status()
                    token_data = response.json()
                    self.access_token = token_data["access_token"]
                    self.token_expiry = int(time.time()) + token_data["expires_in"]
                    redis = get_redis()
                    await redis.set(
                        f"fetcher:access_token:{self.client_id}",
                        self.access_token,
                        ex=token_data["expires_in"],
                    )
                    await redis.set(
                        f"fetcher:expire_at:{self.client_id}",
                        self.token_expiry,
                        ex=token_data["expires_in"],
                    )
                    logger.success(
                        f"Granted new access token for client {self.client_id}, "
                        f"expires in {token_data['expires_in']} seconds"
                    )
                    return

                except TimeoutException as exc:
                    last_error = exc
                    logger.warning(
                        f"Timed out while requesting access token for "
                        f"client {self.client_id} (attempt {attempt}/{retries})"
                    )
                except HTTPStatusError as exc:
                    last_error = exc
                    logger.warning(
                        f"HTTP error while requesting access token for client {self.client_id}"
                        f" (status: {exc.response.status_code}, attempt {attempt}/{retries})"
                    )
                except Exception as exc:
                    last_error = exc
                    logger.exception(
                        f"Unexpected error while requesting access token for client {self.client_id}"
                        f" (attempt {attempt}/{retries})"
                    )

                if attempt < retries:
                    await asyncio.sleep(backoff * attempt)

        raise TokenAuthError("Failed to grant access token after retries") from last_error

    async def ensure_valid_access_token(self) -> None:
        """Ensure the access token is valid, refreshing if necessary."""
        if self.is_token_expired():
            await self.grant_access_token()

    async def _handle_unauthorized(self) -> None:
        """Handle an unauthorized response by refreshing the token."""
        await self.grant_access_token()

    async def _clear_access_token(self) -> None:
        """Clear the access token from memory and Redis cache."""
        logger.warning(f"Clearing access token for client {self.client_id}")

        self.access_token = ""
        self.token_expiry = 0

        redis = get_redis()
        await redis.delete(f"fetcher:access_token:{self.client_id}")
        await redis.delete(f"fetcher:expire_at:{self.client_id}")
