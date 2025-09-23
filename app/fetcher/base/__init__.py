from __future__ import annotations

import time
from urllib.parse import quote

from app.dependencies.database import get_redis
from app.log import logger

from httpx import AsyncClient


class TokenAuthError(Exception):
    """Token 授权失败异常"""

    pass


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
        # 检查 token 是否过期，如果过期则刷新
        if self.is_token_expired():
            await self.refresh_access_token()

        header = kwargs.pop("headers", {})
        header.update(self.header)

        async with AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=header,
                **kwargs,
            )

            # 处理 401 错误
            if response.status_code == 401:
                logger.warning(f"Received 401 error for {url}")
                await self._clear_tokens()
                raise TokenAuthError(f"Authentication failed. Please re-authorize using: {self.authorize_url}")

            response.raise_for_status()
            return response.json()

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

    async def refresh_access_token(self) -> None:
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
                logger.info(f"Successfully refreshed access token for client {self.client_id}")
        except Exception as e:
            logger.error(f"Failed to refresh access token for client {self.client_id}: {e}")
            await self._clear_tokens()
            logger.warning(f"Cleared invalid tokens. Please re-authorize: {self.authorize_url}")
            raise

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
