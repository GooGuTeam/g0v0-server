import asyncio
import time

from app.dependencies.database import get_redis
from app.log import fetcher_logger

from httpx import AsyncClient


class TokenAuthError(Exception):
    """Token 授权失败异常"""

    pass


logger = fetcher_logger("Fetcher")


class BaseFetcher:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scope: list[str] = ["public"],
        callback_url: str = "",
    ):
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
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def request_api(self, url: str, method: str = "GET", **kwargs) -> dict:
        """
        发送 API 请求
        """
        await self.ensure_valid_access_token()

        headers = kwargs.pop("headers", {}).copy()
        attempt = 0

        while attempt < 2:
            request_headers = {**headers, **self.header}
            request_kwargs = kwargs.copy()

            async with AsyncClient() as client:
                response = await client.request(
                    method,
                    url,
                    headers=request_headers,
                    **request_kwargs,
                )

            if response.status_code != 401:
                response.raise_for_status()
                return response.json()

            attempt += 1
            logger.warning(f"Received 401 error for {url}, attempt {attempt}")
            await self._handle_unauthorized()

        await self._clear_access_token()
        logger.warning(f"Failed to authorize after retries for {url}, cleaned up tokens")
        await self.grant_access_token()
        raise TokenAuthError(f"Failed to authorize after retries for {url}")

    def is_token_expired(self) -> bool:
        if not isinstance(self.token_expiry, int) or self.token_expiry == 0:
            return True
        return self.token_expiry <= int(time.time()) or not self.access_token

    async def grant_access_token(self) -> None:
        async with AsyncClient() as client:
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
                f"Granted new access token for client {self.client_id}, expires in {token_data['expires_in']} seconds"
            )

    async def ensure_valid_access_token(self) -> None:
        if self.is_token_expired():
            await self.grant_access_token()

    async def _handle_unauthorized(self) -> None:
        await self.grant_access_token()

    async def _clear_access_token(self) -> None:
        logger.warning(f"Clearing access token for client {self.client_id}")

        self.access_token = ""
        self.token_expiry = 0

        redis = get_redis()
        await redis.delete(f"fetcher:access_token:{self.client_id}")
        await redis.delete(f"fetcher:expire_at:{self.client_id}")
