"""Special statistics creation startup tasks.

Provides backfill operations to create missing user statistics records
for special game modes (Relax, Autopilot, custom rulesets).
"""

from app.config import settings
from app.const import BANCHOBOT_ID
from app.database.statistics import UserStatistics
from app.database.user import User
from app.dependencies.database import with_db
from app.log import logger
from app.models.score import GameMode

from sqlalchemy import exists
from sqlmodel import select


async def create_rx_statistics() -> None:
    """Create missing Relax and Autopilot statistics for all users.

    Ensures every user has UserStatistics records for RX/AP game modes
    when those modes are enabled in settings.
    """
    async with with_db() as session:
        users = (await session.exec(select(User.id))).all()
        total_users = len(users)
        logger.info(f"Ensuring RX/AP statistics exist for {total_users} users")
        rx_created = 0
        ap_created = 0
        for i in users:
            if i == BANCHOBOT_ID:
                continue

            if settings.enable_rx:
                for mode in (
                    GameMode.OSURX,
                    GameMode.TAIKORX,
                    GameMode.FRUITSRX,
                ):
                    is_exist = (
                        await session.exec(
                            select(exists()).where(
                                UserStatistics.user_id == i,
                                UserStatistics.mode == mode,
                            )
                        )
                    ).first()
                    if not is_exist:
                        statistics_rx = UserStatistics(mode=mode, user_id=i)
                        session.add(statistics_rx)
                        rx_created += 1
            if settings.enable_ap:
                is_exist = (
                    await session.exec(
                        select(exists()).where(
                            UserStatistics.user_id == i,
                            UserStatistics.mode == GameMode.OSUAP,
                        )
                    )
                ).first()
                if not is_exist:
                    statistics_ap = UserStatistics(mode=GameMode.OSUAP, user_id=i)
                    session.add(statistics_ap)
                    ap_created += 1
        await session.commit()
        if rx_created or ap_created:
            logger.success(
                f"Created {rx_created} RX statistics rows and {ap_created} AP statistics rows during backfill"
            )


async def create_custom_ruleset_statistics() -> None:
    """Create missing custom ruleset statistics for all users.

    Ensures every user has UserStatistics records for all custom
    rulesets defined in GameMode.
    """
    async with with_db() as session:
        users = (await session.exec(select(User.id))).all()
        total_users = len(users)
        logger.info(f"Ensuring custom ruleset statistics exist for {total_users} users")
        created_count = 0
        for i in users:
            if i == BANCHOBOT_ID:
                continue

            for mode in GameMode:
                if not mode.is_custom_ruleset():
                    continue

                is_exist = (
                    await session.exec(
                        select(exists()).where(
                            UserStatistics.user_id == i,
                            UserStatistics.mode == mode,
                        )
                    )
                ).first()
                if not is_exist:
                    statistics = UserStatistics(mode=mode, user_id=i)
                    session.add(statistics)
                    created_count += 1
        await session.commit()
        if created_count:
            logger.success(f"Created {created_count} custom ruleset statistics rows during backfill")
