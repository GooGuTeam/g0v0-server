"""维护相关调度器。"""

from __future__ import annotations

import asyncio

from app.dependencies.database import engine
from app.log import logger
from app.service.maintenance.database_cleanup_service import DatabaseCleanupService

from sqlmodel.ext.asyncio.session import AsyncSession

__all__ = [
    "DatabaseCleanupScheduler",
    "database_cleanup_scheduler",
    "run_manual_database_cleanup",
    "start_database_cleanup_scheduler",
    "stop_database_cleanup_scheduler",
]


class DatabaseCleanupScheduler:
    """数据库清理调度器"""

    def __init__(self) -> None:
        self.running = False
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动调度器"""
        if self.running:
            return

        self.running = True
        self.task = asyncio.create_task(self._run_scheduler())
        logger.debug("Database cleanup scheduler started")

    async def stop(self) -> None:
        """停止调度器"""
        if not self.running:
            return

        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.debug("Database cleanup scheduler stopped")

    async def _run_scheduler(self) -> None:
        """运行调度器"""
        while self.running:
            try:
                # 每小时运行一次清理
                await asyncio.sleep(3600)  # 3600秒 = 1小时

                if not self.running:
                    break

                await self._run_cleanup()

            except asyncio.CancelledError:
                break
            except Exception as e:  # pragma: no cover - logging branch
                logger.error(f"Database cleanup scheduler error: {e!s}")
                # 发生错误后等待5分钟再继续
                await asyncio.sleep(300)

    async def _run_cleanup(self) -> None:
        """执行清理任务"""
        try:
            async with AsyncSession(engine) as db:
                logger.debug("Starting scheduled database cleanup...")

                # 清理过期的验证码
                expired_codes = await DatabaseCleanupService.cleanup_expired_verification_codes(db)

                # 清理过期的登录会话
                expired_sessions = await DatabaseCleanupService.cleanup_expired_login_sessions(db)

                # 只在有清理记录时输出总结
                total_cleaned = expired_codes + expired_sessions
                if total_cleaned > 0:
                    logger.debug(
                        "Scheduled cleanup completed - codes: %s, sessions: %s",
                        expired_codes,
                        expired_sessions,
                    )

        except Exception as e:  # pragma: no cover - logging branch
            logger.error(f"Error during scheduled database cleanup: {e!s}")

    async def run_manual_cleanup(self) -> dict[str, int]:
        """手动运行完整清理"""
        try:
            async with AsyncSession(engine) as db:
                logger.debug("Starting manual database cleanup...")
                results = await DatabaseCleanupService.run_full_cleanup(db)
                total = sum(results.values())
                if total > 0:
                    logger.debug(
                        "Manual cleanup completed, total records cleaned: %s",
                        total,
                    )
                return results
        except Exception as e:  # pragma: no cover - logging branch
            logger.error(f"Error during manual database cleanup: {e!s}")
            return {}


database_cleanup_scheduler = DatabaseCleanupScheduler()


async def start_database_cleanup_scheduler() -> None:
    """启动数据库清理调度器"""
    await database_cleanup_scheduler.start()


async def stop_database_cleanup_scheduler() -> None:
    """停止数据库清理调度器"""
    await database_cleanup_scheduler.stop()


async def run_manual_database_cleanup() -> dict[str, int]:
    """手动运行数据库清理"""
    return await database_cleanup_scheduler.run_manual_cleanup()
