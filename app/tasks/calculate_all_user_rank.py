"""User rank calculation scheduled task.

Provides daily rank calculation and recording for all users
across all game modes. Updates rank history and tracks top ranks.
"""

from datetime import timedelta

from app.database import RankHistory, UserStatistics
from app.database.rank_history import RankTop
from app.dependencies.database import with_db
from app.dependencies.scheduler import get_scheduler
from app.log import logger
from app.models.score import GameMode
from app.utils import utcnow

from sqlmodel import col, exists, select, update


@get_scheduler().scheduled_job("cron", hour=0, minute=0, second=0, id="calculate_user_rank")
async def calculate_user_rank(is_today: bool = False) -> None:
    """Calculate and record user ranks for all game modes.

    Runs daily at midnight to compute ranks based on PP values.
    Updates RankHistory for historical tracking and RankTop for peak ranks.

    Args:
        is_today: If True, calculate for today's date. Otherwise, calculate
            for yesterday (default behavior for scheduled runs).
    """
    today = utcnow().date()
    target_date = today if is_today else today - timedelta(days=1)
    logger.info("Starting user rank calculation for {}", target_date)
    async with with_db() as session:
        for gamemode in GameMode:
            logger.info("Calculating ranks for {} on {}", gamemode.name, target_date)
            users = await session.exec(
                select(UserStatistics)
                .where(
                    UserStatistics.mode == gamemode,
                    UserStatistics.pp > 0,
                    col(UserStatistics.is_ranked).is_(True),
                )
                .order_by(
                    col(UserStatistics.pp).desc(),
                    col(UserStatistics.total_score).desc(),
                )
            )
            rank = 1
            processed_users = 0
            for user in users:
                is_exist = (
                    await session.exec(
                        select(exists()).where(
                            RankHistory.user_id == user.user_id,
                            RankHistory.mode == gamemode,
                            RankHistory.date == target_date,
                        )
                    )
                ).first()
                if not is_exist:
                    rank_history = RankHistory(
                        user_id=user.user_id,
                        mode=gamemode,
                        rank=rank,
                        date=today,
                    )
                    session.add(rank_history)
                else:
                    await session.execute(
                        update(RankHistory)
                        .where(
                            col(RankHistory.user_id) == user.user_id,
                            col(RankHistory.mode) == gamemode,
                            col(RankHistory.date) == target_date,
                        )
                        .values(rank=rank)
                    )

                rank_top = (
                    await session.exec(
                        select(RankTop).where(
                            RankTop.user_id == user.user_id,
                            RankTop.mode == gamemode,
                        )
                    )
                ).first()
                if not rank_top:
                    rank_top = RankTop(
                        user_id=user.user_id,
                        mode=gamemode,
                        rank=rank,
                        date=today,
                    )
                    session.add(rank_top)
                else:
                    if rank_top.rank > rank:
                        rank_top.rank = rank
                        rank_top.date = today

                rank += 1
                processed_users += 1
            await session.commit()
            if processed_users > 0:
                logger.info(
                    "Updated ranks for {} on {} ({} users)",
                    gamemode.name,
                    target_date,
                    processed_users,
                )
            else:
                logger.info("No users found for {} on {}", gamemode.name, target_date)
    logger.success("User rank calculation completed for {}", target_date)
