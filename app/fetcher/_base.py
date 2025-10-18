import asyncio
import time
from urllib.parse import quote

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

    @property
    def authorize_url(self) -> str:
        return (
            f"https://osu.ppy.sh/oauth/authorize?client_id={self.client_id}"
            f"&response_type=code&scope={quote(' '.join(self.scope))}"
            f"&redirect_uri={self.callback_url}"
        )

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
        await self._ensure_valid_access_token()

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

        await self._clear_tokens()
        raise TokenAuthError(f"Authentication failed. Please re-authorize using: {self.authorize_url}")

    def is_token_expired(self) -> bool:
        return self.token_expiry <= int(time.time())

    async def grant_access_token(self, code: str) -> None:
        async with AsyncClient() as client:
            response = await client.post(
                "https://osu.ppy.sh/oauth/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.callback_url,
                    "code": code,
                },
            )
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data["access_token"]
            self.refresh_token = token_data.get("refresh_token", "")
            self.token_expiry = int(time.time()) + token_data["expires_in"]
            redis = get_redis()
            await redis.set(
                f"fetcher:access_token:{self.client_id}",
                self.access_token,
                ex=token_data["expires_in"],
            )
            await redis.set(
                f"fetcher:refresh_token:{self.client_id}",
                self.refresh_token,
            )

    async def refresh_access_token(self, *, force: bool = False) -> None:
        if not force and not self.is_token_expired():
            return

        async with self._token_lock:
            if not force and not self.is_token_expired():
                return

            if force:
                await self._clear_access_token()

            if not self.refresh_token:
                logger.error(f"Missing refresh token for client {self.client_id}")
                await self._clear_tokens()
                raise TokenAuthError(f"Missing refresh token. Please re-authorize using: {self.authorize_url}")

            try:
                logger.info(f"Refreshing access token for client {self.client_id}")
                async with AsyncClient() as client:
                    response = await client.post(
                        "https://osu.ppy.sh/oauth/token",
                        data={
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "grant_type": "refresh_token",
                            "refresh_token": self.refresh_token,
                        },
                    )
                    response.raise_for_status()
                    token_data = response.json()
                    self.access_token = token_data["access_token"]
                    self.refresh_token = token_data.get("refresh_token", self.refresh_token)
                    self.token_expiry = int(time.time()) + token_data["expires_in"]
                    redis = get_redis()
                    await redis.set(
                        f"fetcher:access_token:{self.client_id}",
                        self.access_token,
                        ex=token_data["expires_in"],
                    )
                    await redis.set(
                        f"fetcher:refresh_token:{self.client_id}",
                        self.refresh_token,
                    )
                    logger.info(f"Successfully refreshed access token for client {self.client_id}")
            except Exception as e:
                logger.error(f"Failed to refresh access token for client {self.client_id}: {e}")
                await self._clear_tokens()
                logger.warning(f"Cleared invalid tokens. Please re-authorize: {self.authorize_url}")
                raise

    async def _ensure_valid_access_token(self) -> None:
        if self.is_token_expired():
            await self.refresh_access_token()

    async def _handle_unauthorized(self) -> None:
        await self.refresh_access_token(force=True)

    async def _clear_access_token(self) -> None:
        logger.warning(f"Clearing access token for client {self.client_id}")

        self.access_token = ""
        self.token_expiry = 0

        redis = get_redis()
        await redis.delete(f"fetcher:access_token:{self.client_id}")

    async def _clear_tokens(self) -> None:
        """
        清除所有 token
        """
        logger.warning(f"Clearing tokens for client {self.client_id}")

        # 清除内存中的 token
        self.access_token = ""
        self.refresh_token = ""
        self.token_expiry = 0

        # 清除 Redis 中的 token
        redis = get_redis()
        await redis.delete(f"fetcher:access_token:{self.client_id}")
        await redis.delete(f"fetcher:refresh_token:{self.client_id}")

    def get_auth_status(self) -> dict:
        """
        获取当前授权状态信息
        """
        return {
            "client_id": self.client_id,
            "has_access_token": bool(self.access_token),
            "has_refresh_token": bool(self.refresh_token),
            "token_expired": self.is_token_expired(),
            "authorize_url": self.authorize_url,
        }
