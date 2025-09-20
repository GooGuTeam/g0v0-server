"""
数据库清理服务 - 清理过期的验证码和会话
"""

from __future__ import annotations

from datetime import timedelta

from app.database.verification import EmailVerification, LoginSession
from app.log import logger
from app.utils import utcnow

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession


class DatabaseCleanupService:
    """数据库清理服务"""

    @staticmethod
    async def cleanup_expired_verification_codes(db: AsyncSession) -> int:
        """
        清理过期的邮件验证码

        Args:
            db: 数据库会话

        Returns:
            int: 清理的记录数
        """
        try:
            # 查找过期的验证码记录
            current_time = utcnow()

            stmt = select(EmailVerification).where(EmailVerification.expires_at < current_time)
            result = await db.exec(stmt)
            expired_codes = result.all()

            # 删除过期的记录
            deleted_count = 0
            for code in expired_codes:
                await db.delete(code)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(f"[Cleanup Service] Cleaned up {deleted_count} expired email verification codes")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"[Cleanup Service] Error cleaning expired verification codes: {e!s}")
            return 0

    @staticmethod
    async def cleanup_expired_login_sessions(db: AsyncSession) -> int:
        """
        清理过期的登录会话

        Args:
            db: 数据库会话

        Returns:
            int: 清理的记录数
        """
        try:
            # 查找过期的登录会话记录
            current_time = utcnow()

            stmt = select(LoginSession).where(LoginSession.expires_at < current_time)
            result = await db.exec(stmt)
            expired_sessions = result.all()

            # 删除过期的记录
            deleted_count = 0
            for session in expired_sessions:
                await db.delete(session)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(f"[Cleanup Service] Cleaned up {deleted_count} expired login sessions")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"[Cleanup Service] Error cleaning expired login sessions: {e!s}")
            return 0

    @staticmethod
    async def cleanup_old_used_verification_codes(db: AsyncSession, days_old: int = 7) -> int:
        """
        清理旧的已使用验证码记录

        Args:
            db: 数据库会话
            days_old: 清理多少天前的已使用记录，默认7天

        Returns:
            int: 清理的记录数
        """
        try:
            # 查找指定天数前的已使用验证码记录
            cutoff_time = utcnow() - timedelta(days=days_old)

            stmt = select(EmailVerification).where(col(EmailVerification.is_used).is_(True))
            result = await db.exec(stmt)
            all_used_codes = result.all()

            # 筛选出过期的记录
            old_used_codes = [code for code in all_used_codes if code.used_at and code.used_at < cutoff_time]

            # 删除旧的已使用记录
            deleted_count = 0
            for code in old_used_codes:
                await db.delete(code)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(
                    f"[Cleanup Service] Cleaned up {deleted_count} used verification codes older than {days_old} days"
                )

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"[Cleanup Service] Error cleaning old used verification codes: {e!s}")
            return 0

    @staticmethod
    async def cleanup_old_verified_sessions(db: AsyncSession, days_old: int = 30) -> int:
        """
        清理旧的已验证会话记录

        Args:
            db: 数据库会话
            days_old: 清理多少天前的已验证记录，默认30天

        Returns:
            int: 清理的记录数
        """
        try:
            # 查找指定天数前的已验证会话记录
            cutoff_time = utcnow() - timedelta(days=days_old)

            stmt = select(LoginSession).where(col(LoginSession.is_verified).is_(True))
            result = await db.exec(stmt)
            all_verified_sessions = result.all()

            # 筛选出过期的记录
            old_verified_sessions = [
                session
                for session in all_verified_sessions
                if session.verified_at and session.verified_at < cutoff_time
            ]

            # 删除旧的已验证记录
            deleted_count = 0
            for session in old_verified_sessions:
                await db.delete(session)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(
                    f"[Cleanup Service] Cleaned up {deleted_count} verified sessions older than {days_old} days"
                )

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"[Cleanup Service] Error cleaning old verified sessions: {e!s}")
            return 0

    @staticmethod
    async def run_full_cleanup(db: AsyncSession) -> dict[str, int]:
        """
        运行完整的清理流程

        Args:
            db: 数据库会话

        Returns:
            dict: 各项清理的结果统计
        """
        results = {}

        # 清理过期的验证码
        results["expired_verification_codes"] = await DatabaseCleanupService.cleanup_expired_verification_codes(db)

        # 清理过期的登录会话
        results["expired_login_sessions"] = await DatabaseCleanupService.cleanup_expired_login_sessions(db)

        # 清理7天前的已使用验证码
        results["old_used_verification_codes"] = await DatabaseCleanupService.cleanup_old_used_verification_codes(db, 7)

        # 清理30天前的已验证会话
        results["old_verified_sessions"] = await DatabaseCleanupService.cleanup_old_verified_sessions(db, 30)

        total_cleaned = sum(results.values())
        if total_cleaned > 0:
            logger.debug(
                f"[Cleanup Service] Full cleanup completed, total cleaned: {total_cleaned} records - {results}"
            )

        return results

    @staticmethod
    async def get_cleanup_statistics(db: AsyncSession) -> dict[str, int]:
        """
        获取清理统计信息

        Args:
            db: 数据库会话

        Returns:
            dict: 统计信息
        """
        try:
            current_time = utcnow()
            cutoff_7_days = current_time - timedelta(days=7)
            cutoff_30_days = current_time - timedelta(days=30)

            # 统计过期的验证码数量
            expired_codes_stmt = select(EmailVerification).where(EmailVerification.expires_at < current_time)
            expired_codes_result = await db.exec(expired_codes_stmt)
            expired_codes_count = len(expired_codes_result.all())

            # 统计过期的登录会话数量
            expired_sessions_stmt = select(LoginSession).where(LoginSession.expires_at < current_time)
            expired_sessions_result = await db.exec(expired_sessions_stmt)
            expired_sessions_count = len(expired_sessions_result.all())

            # 统计7天前的已使用验证码数量
            old_used_codes_stmt = select(EmailVerification).where(col(EmailVerification.is_used).is_(True))
            old_used_codes_result = await db.exec(old_used_codes_stmt)
            all_used_codes = old_used_codes_result.all()
            old_used_codes_count = len(
                [code for code in all_used_codes if code.used_at and code.used_at < cutoff_7_days]
            )

            # 统计30天前的已验证会话数量
            old_verified_sessions_stmt = select(LoginSession).where(col(LoginSession.is_verified).is_(True))
            old_verified_sessions_result = await db.exec(old_verified_sessions_stmt)
            all_verified_sessions = old_verified_sessions_result.all()
            old_verified_sessions_count = len(
                [
                    session
                    for session in all_verified_sessions
                    if session.verified_at and session.verified_at < cutoff_30_days
                ]
            )

            return {
                "expired_verification_codes": expired_codes_count,
                "expired_login_sessions": expired_sessions_count,
                "old_used_verification_codes": old_used_codes_count,
                "old_verified_sessions": old_verified_sessions_count,
                "total_cleanable": expired_codes_count
                + expired_sessions_count
                + old_used_codes_count
                + old_verified_sessions_count,
            }

        except Exception as e:
            logger.error(f"[Cleanup Service] Error getting cleanup statistics: {e!s}")
            return {
                "expired_verification_codes": 0,
                "expired_login_sessions": 0,
                "old_used_verification_codes": 0,
                "old_verified_sessions": 0,
                "total_cleanable": 0,
            }
