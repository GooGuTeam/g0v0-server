"""
会话验证中间件和状态管理

基于osu-web的会话验证系统实现
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import ClassVar

from app.database.lazer_user import User
from app.database.verification import LoginSession
from app.dependencies.database import get_redis, with_db
from app.log import logger
from app.service.verification_service import LoginSessionService

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlmodel.ext.asyncio.session import AsyncSession


class SessionVerificationState:
    """会话验证状态管理类

    参考osu-web的State类实现
    """

    def __init__(self, session: LoginSession, user: User, redis: Redis):
        self.session = session
        self.user = user
        self.redis = redis

    @classmethod
    async def get_current(
        cls,
        request: Request,
        db: AsyncSession,
        redis: Redis,
        user: User,
    ) -> SessionVerificationState | None:
        """获取当前会话验证状态"""
        try:
            # 从请求头或token中获取会话信息
            session_token = cls._extract_session_token(request)
            if not session_token:
                return None

            # 查找会话
            session = await LoginSessionService.find_for_verification(db, session_token)
            if not session or session.user_id != user.id:
                return None

            return cls(session, user, redis)
        except Exception as e:
            logger.error(f"[Session Verification] Error getting current state: {e}")
            return None

    @staticmethod
    def _extract_session_token(request: Request) -> str | None:
        """从请求中提取会话token"""
        # 尝试从Authorization header提取
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]  # 移除"Bearer "前缀

        # 可以扩展其他提取方式
        return None

    def get_method(self) -> str:
        """获取验证方法

        参考osu-web的逻辑，智能选择验证方法
        """
        current_method = self.session.verification_method

        if current_method is None:
            # 智能选择验证方法
            # 参考osu-web: API版本 < 20250913 或用户没有TOTP时使用邮件验证
            # 这里简化为检查用户是否有TOTP
            totp_key = getattr(self.user, "totp_key", None)
            current_method = "totp" if totp_key else "mail"

            # 设置验证方法
            task = asyncio.create_task(self._set_verification_method(current_method))
            # 添加错误处理以防止未捕获的异常
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        return current_method

    async def _set_verification_method(self, method: str) -> None:
        """内部方法：设置验证方法"""
        try:
            token_id = self.session.token_id
            if token_id is not None and method in ["totp", "mail"]:
                # 类型检查确保method是正确的字面量类型
                verification_method = method if method in ["totp", "mail"] else "totp"
                await LoginSessionService.set_login_method(
                    self.user.id,
                    token_id,
                    verification_method,
                    self.redis,  # type: ignore
                )
        except Exception as e:
            logger.error(f"[Session Verification] Error setting verification method: {e}")

    def is_verified(self) -> bool:
        """检查会话是否已验证"""
        return self.session.is_verified

    async def mark_verified(self) -> None:
        """标记会话为已验证"""
        try:
            # 创建专用数据库会话
            db = with_db()
            try:
                token_id = self.session.token_id
                if token_id is not None:
                    await LoginSessionService.mark_session_verified(db, self.redis, self.user.id, token_id)
            finally:
                await db.close()
        except Exception as e:
            logger.error(f"[Session Verification] Error marking session verified: {e}")

    def get_key(self) -> str:
        """获取会话密钥"""
        return str(self.session.id) if self.session.id else ""

    def get_key_for_event(self) -> str:
        """获取用于事件广播的会话密钥"""
        return LoginSessionService.get_key_for_event(self.get_key())

    def user_id(self) -> int:
        """获取用户ID"""
        return self.user.id

    async def issue_mail_if_needed(self) -> None:
        """如果需要，发送验证邮件"""
        try:
            if self.get_method() == "mail":
                from app.service.verification_service import EmailVerificationService

                # 创建专用数据库会话发送邮件
                db = with_db()
                try:
                    await EmailVerificationService.send_verification_email(
                        db, self.redis, self.user.id, self.user.username, self.user.email, None, None, None, None, None
                    )
                finally:
                    await db.close()
        except Exception as e:
            logger.error(f"[Session Verification] Error issuing mail: {e}")


class SessionVerificationController:
    """会话验证控制器

    参考osu-web的Controller类实现
    """

    # 需要跳过验证的路由（参考osu-web的SKIP_VERIFICATION_ROUTES）
    SKIP_VERIFICATION_ROUTES: ClassVar[set[str]] = {
        "/api/v2/session/verify",
        "/api/v2/session/verify/reissue",
        "/api/v2/me",
        "/api/v2/logout",
        "/oauth/token",
    }

    @staticmethod
    def should_skip_verification(request: Request) -> bool:
        """检查是否应该跳过验证"""
        path = request.url.path
        return path in SessionVerificationController.SKIP_VERIFICATION_ROUTES

    @staticmethod
    async def initiate_verification(
        state: SessionVerificationState,
        request: Request,
    ) -> Response:
        """启动会话验证流程

        参考osu-web的initiate方法
        """
        try:
            method = state.get_method()

            # 如果是邮件验证，发送验证邮件
            if method == "mail":
                await state.issue_mail_if_needed()

            # API请求返回JSON响应
            if request.url.path.startswith("/api/"):
                return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"method": method})

            # 其他情况可以扩展支持HTML响应
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"authentication": "verify", "method": method, "message": "Session verification required"},
            )

        except Exception as e:
            logger.error(f"[Session Verification] Error initiating verification: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Verification initiation failed"
            )


class SessionVerificationMiddleware:
    """会话验证中间件

    参考osu-web的VerifyUser中间件实现
    """

    def __init__(self, app: Callable[[Request], Awaitable[Response]]):
        self.app = app

    async def __call__(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """中间件主要逻辑"""
        try:
            # 检查是否需要跳过验证
            if SessionVerificationController.should_skip_verification(request):
                return await call_next(request)

            # 获取依赖项
            user = await self._get_user(request)
            if not user:
                # 未认证用户跳过验证
                return await call_next(request)

            # 获取数据库和Redis连接
            db = await self._get_db()
            redis = await self._get_redis()

            # 获取会话验证状态
            state = await SessionVerificationState.get_current(request, db, redis, user)
            if not state:
                # 无法获取会话状态，继续请求
                return await call_next(request)

            # 检查是否已验证
            if state.is_verified():
                # 已验证，继续请求
                return await call_next(request)

            # 检查是否需要验证
            if not self._requires_verification(request):
                return await call_next(request)

            # 启动验证流程
            return await SessionVerificationController.initiate_verification(state, request)

        except Exception as e:
            logger.error(f"[Session Verification Middleware] Unexpected error: {e}")
            # 出错时允许请求继续，避免阻塞正常流程
            return await call_next(request)

    async def _get_user(self, request: Request) -> User | None:
        """获取当前用户"""
        try:
            # 这里需要手动获取用户，因为在中间件中无法直接使用依赖注入
            # 简化实现，实际应该从token中解析用户
            return None  # 暂时返回None，需要实际实现
        except Exception:
            return None

    async def _get_db(self) -> AsyncSession:
        """获取数据库连接"""
        return with_db()

    async def _get_redis(self) -> Redis:
        """获取Redis连接"""
        return get_redis()

    def _requires_verification(self, request: Request) -> bool:
        """检查是否需要验证

        参考osu-web的requiresVerification方法
        """
        method = request.method

        # GET/HEAD/OPTIONS请求一般不需要验证
        safe_methods = {"GET", "HEAD", "OPTIONS"}
        if method in safe_methods:
            return False

        # POST/PUT/DELETE等修改操作需要验证
        return True


# FastAPI中间件包装器
class FastAPISessionVerificationMiddleware:
    """FastAPI会话验证中间件包装器"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope, receive)

        async def call_next(req: Request) -> Response:
            # 这里需要调用FastAPI应用
            return Response("OK")  # 占位符实现

        middleware = SessionVerificationMiddleware(call_next)
        response = await middleware(request, call_next)

        await response(scope, receive, send)
