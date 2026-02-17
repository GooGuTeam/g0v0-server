"""
FastAPI会话验证中间件

基于osu-web的会话验证系统，适配FastAPI框架
"""

from collections.abc import Callable
from typing import ClassVar

from app.auth import get_token_by_access_token
from app.const import SUPPORT_TOTP_VERIFICATION_VER
from app.database.user import User
from app.database.verification import LoginSession
from app.dependencies.database import get_redis, with_db
from app.helpers import extract_user_agent
from app.log import log
from app.service.verification_service import LoginSessionService

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

logger = log("Middleware")


class SessionState:
    """会话状态类

    简化版本的会话状态管理
    """

    def __init__(self, session: LoginSession, user: User, redis: Redis, db: AsyncSession, api_version: int = 0) -> None:
        self.session = session
        self.user = user
        self.redis = redis
        self.db = db
        self.api_version = api_version
        self._verification_method: str | None = None

    def is_verified(self) -> bool:
        """检查会话是否已验证"""
        return self.session.is_verified

    async def get_method(self) -> str:
        """获取验证方法"""
        if self._verification_method is None:
            # 从Redis获取已设置的方法
            token_id = self.session.token_id
            if token_id is not None:
                self._verification_method = await LoginSessionService.get_login_method(
                    self.user.id, token_id, self.redis
                )

            if self._verification_method is None:
                if self.api_version < SUPPORT_TOTP_VERIFICATION_VER:
                    self._verification_method = "mail"
                    return self._verification_method

                await self.user.awaitable_attrs.totp_key
                totp_key = self.user.totp_key
                self._verification_method = "totp" if totp_key else "mail"

                token_id = self.session.token_id
                if token_id is not None:
                    await LoginSessionService.set_login_method(
                        self.user.id, token_id, self._verification_method, self.redis
                    )

        return self._verification_method

    async def mark_verified(self) -> None:
        """标记会话为已验证"""
        try:
            token_id = self.session.token_id
            if token_id is not None:
                await LoginSessionService.mark_session_verified(
                    self.db,
                    self.redis,
                    self.user.id,
                    token_id,
                    self.session.ip_address,
                    extract_user_agent(self.session.user_agent),
                    self.session.web_uuid,
                )
        except Exception as e:
            logger.error(f"Error marking verified: {e}")

    async def issue_mail_if_needed(self) -> None:
        """如果需要，发送验证邮件"""
        try:
            if await self.get_method() == "mail":
                from app.service.verification_service import EmailVerificationService

                # 这里可以触发邮件发送
                await EmailVerificationService.send_verification_email(
                    self.db,
                    self.redis,
                    self.user.id,
                    self.user.username,
                    self.user.email,
                    None,
                    None,
                    self.user.country_code,
                )
        except Exception as e:
            logger.error(f"Error issuing mail: {e}")

    def get_key(self) -> str:
        """获取会话密钥"""
        return str(self.session.id) if self.session.id else ""

    @property
    def key_for_event(self) -> str:
        """获取用于事件广播的会话密钥"""
        return LoginSessionService.get_key_for_event(self.get_key())

    @property
    def user_id(self) -> int:
        """获取用户ID"""
        return self.user.id


class VerifySessionMiddleware(BaseHTTPMiddleware):
    """会话验证中间件

    参考osu-web的VerifyUser中间件，适配FastAPI
    """

    # 需要跳过验证的路由
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

    # 总是需要验证的路由前缀
    ALWAYS_VERIFY_PATTERNS: ClassVar[set[str]] = {
        "/api/private/admin/",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """中间件主处理逻辑"""
        # 检查是否跳过验证
        if self._should_skip_verification(request):
            return await call_next(request)

        # 获取当前用户
        user = await self._get_current_user(request)
        if not user:
            # 未登录用户跳过验证
            return await call_next(request)

        # 获取会话状态
        session_state = await self._get_session_state(request, user)
        if not session_state:
            # 无会话状态，继续请求
            return await call_next(request)

        # 检查是否已验证
        if session_state.is_verified():
            return await call_next(request)

        # 检查是否需要验证
        if not self._requires_verification(request, user):
            return await call_next(request)

        # 启动验证流程
        return await self._initiate_verification(session_state)

    def _should_skip_verification(self, request: Request) -> bool:
        """检查是否应该跳过验证"""
        path = request.url.path

        # 完全匹配的跳过路由
        if path in self.SKIP_VERIFICATION_ROUTES:
            return True

        # 非API请求跳过
        return bool(not path.startswith("/api/"))

    def _requires_verification(self, request: Request, user: User) -> bool:
        """检查是否需要验证"""
        path = request.url.path
        method = request.method

        # 检查是否为强制验证的路由
        for pattern in self.ALWAYS_VERIFY_PATTERNS:
            if path.startswith(pattern):
                return True

        if not user.is_active:
            return True

        # 安全方法（GET/HEAD/OPTIONS）一般不需要验证
        safe_methods = {"GET", "HEAD", "OPTIONS"}
        if method in safe_methods:
            return False

        # 修改操作（POST/PUT/DELETE/PATCH）需要验证
        return method in {"POST", "PUT", "DELETE", "PATCH"}

    async def _get_current_user(self, request: Request) -> User | None:
        """获取当前用户"""
        try:
            # 从Authorization header提取token
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return None

            token = auth_header[7:]  # 移除"Bearer "前缀

            # 创建专用数据库会话
            async with with_db() as db:
                # 获取token记录
                token_record = await get_token_by_access_token(db, token)
                if not token_record:
                    return None

                # 获取用户
                user = (await db.exec(select(User).where(User.id == token_record.user_id))).first()
                return user

        except Exception as e:
            logger.debug(f"Error getting user: {e}")
            return None

    async def _get_session_state(self, request: Request, user: User) -> SessionState | None:
        """获取会话状态"""
        try:
            # 提取会话token（这里简化为使用相同的auth token）
            auth_header = request.headers.get("Authorization", "")
            api_version = 0
            raw_api_version = request.headers.get("x-api-version")
            if raw_api_version is not None:
                try:
                    api_version = int(raw_api_version)
                except ValueError:
                    api_version = 0

            if not auth_header.startswith("Bearer "):
                return None

            session_token = auth_header[7:]

            # 获取数据库和Redis连接
            async with with_db() as db:
                redis = get_redis()

                # 查找会话
                session = await LoginSessionService.find_for_verification(db, session_token)
                if not session or session.user_id != user.id:
                    return None

                return SessionState(session, user, redis, db, api_version)

        except Exception as e:
            logger.error(f"Error getting session state: {e}")
            return None

    async def _initiate_verification(self, state: SessionState) -> Response:
        """启动验证流程"""
        try:
            method = await state.get_method()
            if method == "mail":
                await state.issue_mail_if_needed()

            # 返回验证要求响应
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"method": method, "message": "Session verification required"},
            )

        except Exception as e:
            logger.error(f"Error initiating verification: {e}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": "Verification initiation failed"}
            )
