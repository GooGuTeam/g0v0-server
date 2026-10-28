"""Database cleanup service.

Cleans up expired verification codes and sessions from the database.
"""

from datetime import datetime, timedelta

from app.database.auth import OAuthClient, OAuthToken
from app.database.verification import EmailVerification, LoginSession, TrustedDevice
from app.helpers import as_utc, utcnow
from app.log import logger
from app.service.verification_service import LoginSessionService, lock_user

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession


class DatabaseCleanupService:
    """Database cleanup service for expired records.

    Provides static methods to clean up expired verification codes,
    login sessions, OAuth tokens, and other temporary records.
    """

    @staticmethod
    async def cleanup_expired_verification_codes(db: AsyncSession) -> int:
        """
        Clean up expired email verification codes.

        Args:
            db: Database session.

        Returns:
            int: Number of records cleaned up.
        """
        try:
            # Find expired verification code records
            current_time = utcnow()

            stmt = select(EmailVerification).where(EmailVerification.expires_at < current_time)
            result = await db.exec(stmt)
            expired_codes = result.all()

            # Delete expired records
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
        Clean up expired login sessions.

        Args:
            db: Database session.

        Returns:
            int: Number of records cleaned up.
        """
        return await DatabaseCleanupService._revoke_stale_sessions(db)

    @staticmethod
    async def cleanup_old_used_verification_codes(db: AsyncSession, days_old: int = 7) -> int:
        """Clean up old used verification code records.

        Args:
            db: Database session.
            days_old: Clean records older than this many days. Default is 7.

        Returns:
            Number of deleted records.
        """
        try:
            # Find used verification codes older than specified days
            cutoff_time = utcnow() - timedelta(days=days_old)

            stmt = select(EmailVerification).where(col(EmailVerification.is_used).is_(True))
            result = await db.exec(stmt)
            all_used_codes = result.all()

            # Filter out expired records
            old_used_codes = [code for code in all_used_codes if code.used_at and code.used_at < cutoff_time]

            # Delete old used records
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
        """Clean up login sessions created but not verified within the specified hours.

        Args:
            db: Database session.
            hours_old: Clean sessions created but not verified before this many hours. Default is 1.

        Returns:
            int: Number of records cleaned up.
        """
        cutoff = utcnow() - timedelta(hours=hours_old)
        return await DatabaseCleanupService._revoke_stale_sessions(db, cutoff=cutoff)

    @staticmethod
    async def _revoke_stale_sessions(db: AsyncSession, *, cutoff: datetime | None = None) -> int:
        """Recheck under the account lock and remove session + token in one transaction."""
        condition = LoginSession.created_at <= cutoff if cutoff else LoginSession.expires_at <= utcnow()
        candidates = (
            await db.exec(
                select(LoginSession.user_id, LoginSession.id)
                .where(col(LoginSession.is_verified).is_(False), condition)
                .order_by(col(LoginSession.user_id), col(LoginSession.id))
            )
        ).all()
        deleted = 0
        try:
            for user_id, session_id in candidates:
                await lock_user(db, user_id)
                session = (
                    await db.exec(
                        select(LoginSession)
                        .where(LoginSession.id == session_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).first()
                if session is None or session.is_verified:
                    continue
                if (cutoff and as_utc(session.created_at) > cutoff) or (
                    not cutoff and as_utc(session.expires_at) > utcnow()
                ):
                    continue
                token = await db.get(OAuthToken, session.token_id) if session.token_id else None
                await db.delete(session)
                if token is not None:
                    await db.delete(token)
                deleted += 1
            await db.commit()
            return deleted
        except Exception:
            await db.rollback()
            raise

    @staticmethod
    async def cleanup_outdated_verified_sessions(db: AsyncSession) -> int:
        """Clean up expired session records.

        Args:
            db: Database session.

        Returns:
            Number of deleted records.
        """
        try:
            stmt = select(LoginSession).where(
                col(LoginSession.is_verified).is_(True), col(LoginSession.token_id).is_(None)
            )
            result = await db.exec(stmt)
            # Delete old verified records
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
        """Clean up expired trusted device records.

        Args:
            db: Database session.

        Returns:
            Number of deleted records.
        """
        return await DatabaseCleanupService._cleanup_auth_records(db, devices=True)

    @staticmethod
    async def cleanup_outdated_tokens(db: AsyncSession) -> int:
        """Clean up expired OAuth tokens.

        Args:
            db: Database session.

        Returns:
            Number of deleted records.
        """
        return await DatabaseCleanupService._cleanup_auth_records(db, devices=False)

    @staticmethod
    async def _cleanup_auth_records(db: AsyncSession, *, devices: bool) -> int:
        model = TrustedDevice if devices else OAuthToken
        expiry_column = TrustedDevice.expires_at if devices else OAuthToken.refresh_token_expires_at
        candidates = (
            await db.exec(
                select(model.user_id, model.id)
                .where(expiry_column <= utcnow())
                .order_by(col(model.user_id), col(model.id))
            )
        ).all()
        deleted = 0
        try:
            for user_id, record_id in candidates:
                if user_id is not None:
                    await lock_user(db, user_id)
                else:
                    token = await db.get(OAuthToken, record_id)
                    if token is None:
                        continue
                    await db.exec(
                        select(OAuthClient.client_id).where(OAuthClient.client_id == token.client_id).with_for_update()
                    )
                record = await db.get(model, record_id, with_for_update=True, populate_existing=True)
                if record is None:
                    continue
                expires_at = record.expires_at if isinstance(record, TrustedDevice) else record.refresh_token_expires_at
                if as_utc(expires_at) > utcnow():
                    continue
                if isinstance(record, TrustedDevice):
                    await LoginSessionService.remove_trusted_device(db, record)
                else:
                    await db.delete(record)
                deleted += 1
            await db.commit()
            return deleted
        except Exception:
            await db.rollback()
            logger.exception("Error cleaning expired authentication records")
            return 0

    @staticmethod
    async def run_full_cleanup(db: AsyncSession) -> dict[str, int]:
        """Run complete cleanup process.

        Args:
            db: Database session.

        Returns:
            Dictionary with cleanup statistics for each category.
        """
        results = {}

        # Clean up expired verification codes
        results["expired_verification_codes"] = await DatabaseCleanupService.cleanup_expired_verification_codes(db)

        # Clean up expired login sessions
        results["expired_login_sessions"] = await DatabaseCleanupService.cleanup_expired_login_sessions(db)

        # Clean up login sessions not verified within 1 hour
        results["unverified_login_sessions"] = await DatabaseCleanupService.cleanup_unverified_login_sessions(db, 1)

        # Clean up verification codes used more than 7 days ago
        results["old_used_verification_codes"] = await DatabaseCleanupService.cleanup_old_used_verification_codes(db, 7)

        # Clean up expired trusted devices
        results["outdated_trusted_devices"] = await DatabaseCleanupService.cleanup_outdated_trusted_devices(db)

        # Clean up expired OAuth tokens
        results["outdated_oauth_tokens"] = await DatabaseCleanupService.cleanup_outdated_tokens(db)

        # Clean up verified sessions with expired tokens
        results["outdated_verified_sessions"] = await DatabaseCleanupService.cleanup_outdated_verified_sessions(db)

        total_cleaned = sum(results.values())
        if total_cleaned > 0:
            logger.debug(f"Full cleanup completed, total cleaned: {total_cleaned} records - {results}")

        return results

    @staticmethod
    async def get_cleanup_statistics(db: AsyncSession) -> dict[str, int]:
        """Get cleanup statistics.

        Args:
            db: Database session.

        Returns:
            Dictionary with statistics.
        """
        try:
            current_time = utcnow()
            cutoff_1_hour = current_time - timedelta(hours=1)
            cutoff_7_days = current_time - timedelta(days=7)
            cutoff_30_days = current_time - timedelta(days=30)

            # Count expired verification codes
            expired_codes_stmt = (
                select(func.count()).select_from(EmailVerification).where(EmailVerification.expires_at < current_time)
            )
            expired_codes_result = await db.exec(expired_codes_stmt)
            expired_codes_count = expired_codes_result.one()

            # Count expired login sessions
            expired_sessions_stmt = (
                select(func.count()).select_from(LoginSession).where(LoginSession.expires_at < current_time)
            )
            expired_sessions_result = await db.exec(expired_sessions_stmt)
            expired_sessions_count = expired_sessions_result.one()

            # Count unverified login sessions older than 1 hour
            unverified_sessions_stmt = (
                select(func.count())
                .select_from(LoginSession)
                .where(col(LoginSession.is_verified).is_(False), LoginSession.created_at < cutoff_1_hour)
            )
            unverified_sessions_result = await db.exec(unverified_sessions_stmt)
            unverified_sessions_count = unverified_sessions_result.one()

            # Count used verification codes older than 7 days
            old_used_codes_stmt = select(EmailVerification).where(col(EmailVerification.is_used).is_(True))
            old_used_codes_result = await db.exec(old_used_codes_stmt)
            all_used_codes = old_used_codes_result.all()
            old_used_codes_count = len(
                [code for code in all_used_codes if code.used_at and code.used_at < cutoff_7_days]
            )

            # Count verified sessions older than 30 days
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

            # Count expired OAuth tokens
            outdated_tokens_stmt = (
                select(func.count()).select_from(OAuthToken).where(OAuthToken.refresh_token_expires_at < current_time)
            )
            outdated_tokens_result = await db.exec(outdated_tokens_stmt)
            outdated_tokens_count = outdated_tokens_result.one()

            # Count expired trusted devices
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
