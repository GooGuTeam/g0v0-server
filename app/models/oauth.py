# OAuth 相关模型
from typing import Annotated, Any, cast
from typing_extensions import Doc

from fastapi import HTTPException, Request
from fastapi.openapi.models import OAuthFlows
from fastapi.security import OAuth2
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel
from starlette.status import HTTP_401_UNAUTHORIZED


class TokenRequest(BaseModel):
    grant_type: str
    username: str | None = None
    password: str | None = None
    refresh_token: str | None = None
    client_id: str
    client_secret: str
    scope: str = "*"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105
    expires_in: int
    refresh_token: str
    scope: str = "*"


class UserCreate(BaseModel):
    username: str
    password: str
    email: str
    country_code: str = "CN"


class OAuthErrorResponse(BaseModel):
    error: str
    error_description: str
    hint: str
    message: str


class RegistrationErrorResponse(BaseModel):
    """注册错误响应模型"""

    form_error: dict


class UserRegistrationErrors(BaseModel):
    """用户注册错误模型"""

    username: list[str] = []
    user_email: list[str] = []
    password: list[str] = []


class RegistrationRequestErrors(BaseModel):
    """注册请求错误模型"""

    message: str | None = None
    redirect: str | None = None
    user: UserRegistrationErrors | None = None


class OAuth2ClientCredentialsBearer(OAuth2):
    def __init__(
        self,
        tokenUrl: Annotated[  # noqa: N803
            str,
            Doc(
                """
                The URL to obtain the OAuth2 token.
                """
            ),
        ],
        refreshUrl: Annotated[  # noqa: N803
            str | None,
            Doc(
                """
                The URL to refresh the token and obtain a new one.
                """
            ),
        ] = None,
        scheme_name: Annotated[
            str | None,
            Doc(
                """
                Security scheme name.

                It will be included in the generated OpenAPI (e.g. visible at `/docs`).
                """
            ),
        ] = None,
        scopes: Annotated[
            dict[str, str] | None,
            Doc(
                """
                The OAuth2 scopes that would be required by the *path operations* that
                use this dependency.
                """
            ),
        ] = None,
        description: Annotated[
            str | None,
            Doc(
                """
                Security scheme description.

                It will be included in the generated OpenAPI (e.g. visible at `/docs`).
                """
            ),
        ] = None,
        auto_error: Annotated[
            bool,
            Doc(
                """
                By default, if no HTTP Authorization header is provided, required for
                OAuth2 authentication, it will automatically cancel the request and
                send the client an error.

                If `auto_error` is set to `False`, when the HTTP Authorization header
                is not available, instead of erroring out, the dependency result will
                be `None`.

                This is useful when you want to have optional authentication.

                It is also useful when you want to have authentication that can be
                provided in one of multiple optional ways (for example, with OAuth2
                or in a cookie).
                """
            ),
        ] = True,
    ):
        if not scopes:
            scopes = {}
        flows = OAuthFlows(
            clientCredentials=cast(
                Any,
                {
                    "tokenUrl": tokenUrl,
                    "refreshUrl": refreshUrl,
                    "scopes": scopes,
                },
            )
        )
        super().__init__(
            flows=flows,
            scheme_name=scheme_name,
            description=description,
            auto_error=auto_error,
        )

    async def __call__(self, request: Request) -> str | None:
        authorization = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer":
            if self.auto_error:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED,
                    detail="Not authenticated",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            else:
                return None  # pragma: nocover
        return param
