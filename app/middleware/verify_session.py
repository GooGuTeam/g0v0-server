"""Apply the persistent session policy without leaking ORM objects across requests."""

from collections.abc import Callable
from typing import ClassVar

from app.auth import get_token_by_access_token
from app.database import User
from app.dependencies.database import get_redis, with_db
from app.helpers import extract_user_agent
from app.log import log
from app.models.error import ErrorType, RequestError
from app.service.auth_security import bearer_token
from app.service.verification_service import (
    EmailVerificationService,
    LoginSessionService,
    SessionState as SessionState,
    SessionStatus,
)

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = log("Middleware")


class VerifySessionMiddleware(BaseHTTPMiddleware):
    SKIP_VERIFICATION_ROUTES: ClassVar[set[str]] = {
        "/api/v2/session/verify",
        "/api/v2/session/verify/reissue",
        "/api/v2/session/verify/mail-fallback",
        "/api/v2/me",
        "/api/v2/me/",
        "/api/v2/logout",
        "/oauth/token",
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
    ALWAYS_VERIFY_PATTERNS: ClassVar[set[str]] = {"/api/private/admin/"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path in self.SKIP_VERIFICATION_ROUTES or not path.startswith("/api/") or request.method == "OPTIONS":
            return await call_next(request)
        token = bearer_token(request.headers.get("Authorization"))
        if token is None:
            return await call_next(request)
        try:
            async with with_db() as db:
                token_record = await get_token_by_access_token(db, token)
                if token_record is not None and token_record.user_id is not None:
                    user = await db.get(User, token_record.user_id)
                    if user is None:
                        raise RequestError(ErrorType.USER_NOT_FOUND)
                    try:
                        api_version = int(request.headers.get("x-api-version", "0"))
                    except ValueError:
                        api_version = 0
                    state = await LoginSessionService.get_state(db, get_redis(), user.id, token_record.id, api_version)
                    state.require_valid()
                    request.state.auth_context = state
                    protected = (
                        any(path.startswith(prefix) for prefix in self.ALWAYS_VERIFY_PATTERNS)
                        or not user.is_active
                        or request.method in {"POST", "PUT", "DELETE", "PATCH"}
                    )
                    if state.status == SessionStatus.PENDING and protected:
                        if state.method == "mail":
                            await EmailVerificationService.send_verification_email(
                                db,
                                get_redis(),
                                user.id,
                                user.username,
                                user.email,
                                user_agent=extract_user_agent(request.headers.get("User-Agent")),
                                country_code=user.country_code,
                            )
                        return JSONResponse(
                            status_code=401,
                            content={"method": state.method or "mail", "message": "Session verification required"},
                        )
        except RequestError as error:
            return JSONResponse(
                status_code=error.status_code,
                headers=error.headers,
                content={"error": error.formatted_message, "msg_key": error.msg_key, **error.details},
            )
        except Exception as error:
            logger.error("Session authentication infrastructure failed ({})", type(error).__name__)
            return JSONResponse(status_code=500, content={"error": "Internal Server Error"})
        return await call_next(request)
