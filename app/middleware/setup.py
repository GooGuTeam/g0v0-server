"""
中间件设置和配置

展示如何将会话验证中间件集成到FastAPI应用中
"""

from __future__ import annotations

from app.config import settings
from app.middleware.verify_session import VerifySessionMiddleware

from fastapi import FastAPI


def setup_session_verification_middleware(app: FastAPI) -> None:
    """设置会话验证中间件

    Args:
        app: FastAPI应用实例
    """
    # 只在启用会话验证时添加中间件
    if settings.enable_session_verification:
        app.add_middleware(VerifySessionMiddleware)

        # 可以在这里添加中间件配置日志
        from app.log import logger

        logger.info("[Middleware] Session verification middleware enabled")
    else:
        from app.log import logger

        logger.info("[Middleware] Session verification middleware disabled")


def setup_all_middlewares(app: FastAPI) -> None:
    """设置所有中间件

    Args:
        app: FastAPI应用实例
    """
    # 设置会话验证中间件
    setup_session_verification_middleware(app)

    # 可以在这里添加其他中间件
    # app.add_middleware(OtherMiddleware)

    from app.log import logger

    logger.info("[Middleware] All middlewares configured")
