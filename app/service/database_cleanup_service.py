"""
数据库清理服务 - 清理过期的验证码和会话
"""

from datetime import timedelta

from app.database.auth import OAuthToken
from app.database.verification import EmailVerification, LoginSession, TrustedDevice
from app.log import logger
from app.utils import utcnow

from sqlmodel import col, func, select
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
                logger.debug(f"Cleaned up {deleted_count} expired email verification codes")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"Error cleaning expired verification codes: {e!s}")
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

            stmt = select(LoginSession).where(
                LoginSession.expires_at < current_time, col(LoginSession.is_verified).is_(False)
            )
            result = await db.exec(stmt)
            expired_sessions = result.all()

            # 删除过期的记录
            deleted_count = 0
            for session in expired_sessions:
                await db.delete(session)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(f"Cleaned up {deleted_count} expired login sessions")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"Error cleaning expired login sessions: {e!s}")
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
                logger.debug(f"Cleaned up {deleted_count} used verification codes older than {days_old} days")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"Error cleaning old used verification codes: {e!s}")
            return 0

    @staticmethod
    async def cleanup_unverified_login_sessions(db: AsyncSession, hours_old: int = 1) -> int:
        """
        清理指定小时前创建但仍未验证的登录会话

        Args:
            db: 数据库会话
            hours_old: 清理多少小时前创建但仍未验证的会话，默认1小时

        Returns:
            int: 清理的记录数
        """
        try:
            # 计算截止时间
            cutoff_time = utcnow() - timedelta(hours=hours_old)

            # 查找指定时间前创建且仍未验证的会话记录
            stmt = select(LoginSession).where(
                col(LoginSession.is_verified).is_(False), LoginSession.created_at < cutoff_time
            )
            result = await db.exec(stmt)
            unverified_sessions = result.all()

            # 删除未验证的会话记录
            deleted_count = 0
            for session in unverified_sessions:
                await db.delete(session)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(f"Cleaned up {deleted_count} unverified login sessions older than {hours_old} hour(s)")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"Error cleaning unverified login sessions: {e!s}")
            return 0

    @staticmethod
    async def cleanup_outdated_verified_sessions(db: AsyncSession) -> int:
        """
        清理过期会话记录

        Args:
            db: 数据库会话

        Returns:
            int: 清理的记录数
        """
        try:
            stmt = select(LoginSession).where(
                col(LoginSession.is_verified).is_(True), col(LoginSession.token_id).is_(None)
            )
            result = await db.exec(stmt)
            # 删除旧的已验证记录
            deleted_count = 0
            for session in result.all():
                await db.delete(session)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(f"Cleaned up {deleted_count} outdated verified sessions")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"Error cleaning outdated verified sessions: {e!s}")
            return 0

    @staticmethod
    async def cleanup_outdated_trusted_devices(db: AsyncSession) -> int:
        """
        清理过期的受信任设备记录

        Args:
            db: 数据库会话

        Returns:
            int: 清理的记录数
        """
        try:
            # 查找过期的受信任设备记录
            current_time = utcnow()

            stmt = select(TrustedDevice).where(TrustedDevice.expires_at < current_time)
            result = await db.exec(stmt)
            expired_devices = result.all()

            # 删除过期的记录
            deleted_count = 0
            for device in expired_devices:
                await db.delete(device)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(f"Cleaned up {deleted_count} expired trusted devices")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"Error cleaning expired trusted devices: {e!s}")
            return 0

    @staticmethod
    async def cleanup_outdated_tokens(db: AsyncSession) -> int:
        """
        清理过期的 OAuth 令牌

        Args:
            db: 数据库会话

        Returns:
            int: 清理的记录数
        """
        try:
            current_time = utcnow()

            stmt = select(OAuthToken).where(OAuthToken.refresh_token_expires_at < current_time)
            result = await db.exec(stmt)
            expired_tokens = result.all()

            deleted_count = 0
            for token in expired_tokens:
                await db.delete(token)
                deleted_count += 1

            await db.commit()

            if deleted_count > 0:
                logger.debug(f"Cleaned up {deleted_count} expired OAuth tokens")

            return deleted_count

        except Exception as e:
            await db.rollback()
            logger.error(f"Error cleaning expired OAuth tokens: {e!s}")
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

        # 清理1小时前未验证的登录会话
        results["unverified_login_sessions"] = await DatabaseCleanupService.cleanup_unverified_login_sessions(db, 1)

        # 清理7天前的已使用验证码
        results["old_used_verification_codes"] = await DatabaseCleanupService.cleanup_old_used_verification_codes(db, 7)

        # 清理过期的受信任设备
        results["outdated_trusted_devices"] = await DatabaseCleanupService.cleanup_outdated_trusted_devices(db)

        # 清理过期的 OAuth 令牌
        results["outdated_oauth_tokens"] = await DatabaseCleanupService.cleanup_outdated_tokens(db)

        # 清理过期（token 过期）的已验证会话
        results["outdated_verified_sessions"] = await DatabaseCleanupService.cleanup_outdated_verified_sessions(db)

        total_cleaned = sum(results.values())
        if total_cleaned > 0:
            logger.debug(f"Full cleanup completed, total cleaned: {total_cleaned} records - {results}")

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
            cutoff_1_hour = current_time - timedelta(hours=1)
            cutoff_7_days = current_time - timedelta(days=7)
            cutoff_30_days = current_time - timedelta(days=30)

            # 统计过期的验证码数量
            expired_codes_stmt = (
                select(func.count()).select_from(EmailVerification).where(EmailVerification.expires_at < current_time)
            )
            expired_codes_result = await db.exec(expired_codes_stmt)
            expired_codes_count = expired_codes_result.one()

            # 统计过期的登录会话数量
            expired_sessions_stmt = (
                select(func.count()).select_from(LoginSession).where(LoginSession.expires_at < current_time)
            )
            expired_sessions_result = await db.exec(expired_sessions_stmt)
            expired_sessions_count = expired_sessions_result.one()

            # 统计1小时前未验证的登录会话数量
            unverified_sessions_stmt = (
                select(func.count())
                .select_from(LoginSession)
                .where(col(LoginSession.is_verified).is_(False), LoginSession.created_at < cutoff_1_hour)
            )
            unverified_sessions_result = await db.exec(unverified_sessions_stmt)
            unverified_sessions_count = unverified_sessions_result.one()

            # 统计7天前的已使用验证码数量
            old_used_codes_stmt = select(EmailVerification).where(col(EmailVerification.is_used).is_(True))
            old_used_codes_result = await db.exec(old_used_codes_stmt)
            all_used_codes = old_used_codes_result.all()
            old_used_codes_count = len(
                [code for code in all_used_codes if code.used_at and code.used_at < cutoff_7_days]
            )

            # 统计30天前的已验证会话数量
            outdated_verified_sessions_stmt = select(LoginSession).where(col(LoginSession.is_verified).is_(True))
            outdated_verified_sessions_result = await db.exec(outdated_verified_sessions_stmt)
            all_verified_sessions = outdated_verified_sessions_result.all()
            outdated_verified_sessions_count = len(
                [
                    session
                    for session in all_verified_sessions
                    if session.verified_at and session.verified_at < cutoff_30_days
                ]
            )

            # 统计过期的 OAuth 令牌数量
            outdated_tokens_stmt = (
                select(func.count()).select_from(OAuthToken).where(OAuthToken.refresh_token_expires_at < current_time)
            )
            outdated_tokens_result = await db.exec(outdated_tokens_stmt)
            outdated_tokens_count = outdated_tokens_result.one()

            # 统计过期的受信任设备数量
            outdated_devices_stmt = (
                select(func.count()).select_from(TrustedDevice).where(TrustedDevice.expires_at < current_time)
            )
            outdated_devices_result = await db.exec(outdated_devices_stmt)
            outdated_devices_count = outdated_devices_result.one()

            return {
                "expired_verification_codes": expired_codes_count,
                "expired_login_sessions": expired_sessions_count,
                "unverified_login_sessions": unverified_sessions_count,
                "old_used_verification_codes": old_used_codes_count,
                "outdated_verified_sessions": outdated_verified_sessions_count,
                "outdated_oauth_tokens": outdated_tokens_count,
                "outdated_trusted_devices": outdated_devices_count,
                "total_cleanable": expired_codes_count
                + expired_sessions_count
                + unverified_sessions_count
                + old_used_codes_count
                + outdated_verified_sessions_count
                + outdated_tokens_count
                + outdated_devices_count,
            }

        except Exception as e:
            logger.error(f"Error getting cleanup statistics: {e!s}")
            return {
                "expired_verification_codes": 0,
                "expired_login_sessions": 0,
                "unverified_login_sessions": 0,
                "old_used_verification_codes": 0,
                "outdated_verified_sessions": 0,
                "outdated_oauth_tokens": 0,
                "outdated_trusted_devices": 0,
                "total_cleanable": 0,
            }
