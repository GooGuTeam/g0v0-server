from functools import partial

from app.database.daily_challenge import DailyChallengeStats
from app.database.score import Beatmap, Score
from app.models.achievement import Achievement

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


async def process_streak(
    streak: int,
    next_streak: int,
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    if not score.passed:
        return False
    if streak < 1:
        return False
    if next_streak != 0 and streak >= next_streak:
        return False
    stats = (
        await session.exec(
            select(DailyChallengeStats).where(
                DailyChallengeStats.user_id == score.user_id,
            )
        )
    ).first()
    if not stats:
        return False
    return bool(
        streak <= stats.daily_streak_best < next_streak or (next_streak == 0 and stats.daily_streak_best >= streak)
    )


MEDALS = {
    Achievement(
        id=102,
        name="Daily Sprout",
        desc="Ready for anything.",
        assets_id="all-skill-dc-1",
    ): partial(process_streak, 1, 7),
    Achievement(
        id=103,
        name="Weekly Sapling",
        desc="Circadian rhythm calibrated.",
        assets_id="all-skill-dc-7",
    ): partial(process_streak, 7, 30),
    Achievement(
        id=104,
        name="Monthly Shrub",
        desc="In for the grind.",
        assets_id="all-skill-dc-30",
    ): partial(process_streak, 30, 0),
}
