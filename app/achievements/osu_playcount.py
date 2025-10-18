from functools import partial

from app.database import UserStatistics
from app.database.beatmap import Beatmap
from app.database.score import Score
from app.models.achievement import Achievement, Medals
from app.models.score import GameMode

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


async def process_playcount(
    pc: int,
    next_pc: int,
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    if pc < 1:
        return False
    if next_pc != 0 and pc >= next_pc:
        return False
    if score.gamemode != GameMode.OSU:
        return False
    stats = (
        await session.exec(
            select(UserStatistics).where(
                UserStatistics.mode == GameMode.OSU,
                UserStatistics.user_id == score.user_id,
            )
        )
    ).first()
    if not stats:
        return False
    return bool(pc <= stats.play_count < next_pc or (next_pc == 0 and stats.play_count >= pc))


MEDALS: Medals = {
    Achievement(
        id=73,
        name="5,000 Plays",
        desc="There's a lot more where that came from",
        assets_id="osu-plays-5000",
    ): partial(process_playcount, 5000, 15000),
    Achievement(
        id=74,
        name="15,000 Plays",
        desc="Must.. click.. circles..",
        assets_id="osu-plays-15000",
    ): partial(process_playcount, 15000, 25000),
    Achievement(
        id=75,
        name="25,000 Plays",
        desc="There's no going back.",
        assets_id="osu-plays-25000",
    ): partial(process_playcount, 25000, 50000),
    Achievement(
        id=76,
        name="50,000 Plays",
        desc="You're here forever.",
        assets_id="osu-plays-50000",
    ): partial(process_playcount, 50000, 0),
}
