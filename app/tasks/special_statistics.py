"""Special statistics creation startup tasks.

Provides backfill operations to create missing user statistics records
for special game modes (Relax, Autopilot, custom rulesets) and mania key counts.
"""

from app.config import settings
from app.const import BANCHOBOT_ID, MANIA_MAX_KEY_COUNT, MANIA_MIN_KEY_COUNT
from app.database.statistics import UserStatistics
from app.database.user import User
from app.dependencies.database import with_db
from app.log import logger
from app.models.score import GameMode

from sqlalchemy import exists, func
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


async def create_mania_key_statistics() -> None:
    """Create missing ManiaKeyStatistics records for existing mania players.

    Finds all users who have mania scores and ensures they have
    ManiaKeyStatistics records for each key count they've played.
    Then recalculates the statistics from their existing scores.
    """
    from app.database.beatmap import Beatmap
    from app.database.mania_key_statistics import ManiaKeyStatistics, recalculate_mania_key_statistics
    from app.database.score import Score

    async with with_db() as session:
        # Find distinct (user_id, key_count) pairs from mania scores
        result = await session.exec(
            select(Score.user_id, func.floor(Beatmap.cs).label("key_count"))
            .join(Beatmap, Score.beatmap_id == Beatmap.id)
            .where(
                Score.gamemode == GameMode.MANIA,
                Score.passed == True,  # noqa: E712
            )
            .group_by(Score.user_id, func.floor(Beatmap.cs))
        )
        pairs = result.all()

        if not pairs:
            logger.info("No mania scores found, skipping mania key statistics backfill")
            return

        logger.info(f"Found {len(pairs)} user/key_count pairs for mania key statistics backfill")
        created_count = 0
        recalculated_count = 0

        for user_id, key_count in pairs:
            if user_id == BANCHOBOT_ID:
                continue

            key_count = int(key_count)
            if key_count < MANIA_MIN_KEY_COUNT or key_count > MANIA_MAX_KEY_COUNT:
                continue

            # Check if ManiaKeyStatistics already exists
            existing = (
                await session.exec(
                    select(ManiaKeyStatistics).where(
                        ManiaKeyStatistics.user_id == user_id,
                        ManiaKeyStatistics.key_count == key_count,
                    )
                )
            ).first()

            if existing is None:
                # Create the record and recalculate from existing scores
                await recalculate_mania_key_statistics(session, user_id, key_count, GameMode.MANIA)
                created_count += 1
            # Existing records are assumed to be correct; they get updated
            # on new score submissions

        await session.commit()
        if created_count:
            logger.success(
                f"Created and recalculated {created_count} mania key statistics rows during backfill"
            )
        else:
            logger.info("All mania key statistics already exist, no backfill needed")
