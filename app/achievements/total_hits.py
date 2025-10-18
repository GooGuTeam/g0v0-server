from functools import partial

from app.database.score import Beatmap, Score
from app.database.statistics import UserStatistics
from app.models.achievement import Achievement, Medals
from app.models.score import GameMode

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


async def process_tth(
    tth: int,
    next_tth: int,
    gamemode: GameMode,
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    if tth < 1:
        return False
    if next_tth != 0 and tth >= next_tth:
        return False
    if score.gamemode != gamemode:
        return False
    stats = (
        await session.exec(
            select(UserStatistics).where(
                UserStatistics.mode == score.gamemode,
                UserStatistics.user_id == score.user_id,
            )
        )
    ).first()
    if not stats:
        return False
    return bool(tth <= stats.total_hits < next_tth or (next_tth == 0 and stats.play_count >= tth))


MEDALS: Medals = {
    Achievement(
        id=77,
        name="30,000 Drum Hits",
        desc="Did that drum have a face?",
        assets_id="taiko-hits-30000",
    ): partial(process_tth, 30000, 300000, GameMode.TAIKO),
    Achievement(
        id=78,
        name="300,000 Drum Hits",
        desc="The rhythm never stops.",
        assets_id="taiko-hits-300000",
    ): partial(process_tth, 300000, 3000000, GameMode.TAIKO),
    Achievement(
        id=79,
        name="3,000,000 Drum Hits",
        desc="Truly, the Don of dons.",
        assets_id="taiko-hits-3000000",
    ): partial(process_tth, 3000000, 30000000, GameMode.TAIKO),
    Achievement(
        id=80,
        name="30,000,000 Drum Hits",
        desc="Your rhythm, eternal.",
        assets_id="taiko-hits-30000000",
    ): partial(process_tth, 30000000, 0, GameMode.TAIKO),
    Achievement(
        id=81,
        name="Catch 20,000 fruits",
        desc="That is a lot of dietary fiber.",
        assets_id="fruits-hits-20000",
    ): partial(process_tth, 20000, 200000, GameMode.FRUITS),
    Achievement(
        id=82,
        name="Catch 200,000 fruits",
        desc="So, I heard you like fruit...",
        assets_id="fruits-hits-200000",
    ): partial(process_tth, 200000, 2000000, GameMode.FRUITS),
    Achievement(
        id=83,
        name="Catch 2,000,000 fruits",
        desc="Downright healthy.",
        assets_id="fruits-hits-2000000",
    ): partial(process_tth, 2000000, 20000000, GameMode.FRUITS),
    Achievement(
        id=84,
        name="Catch 20,000,000 fruits",
        desc="Nothing left behind.",
        assets_id="fruits-hits-20000000",
    ): partial(process_tth, 20000000, 0, GameMode.FRUITS),
    Achievement(
        id=85,
        name="40,000 Keys",
        desc="Just the start of the rainbow.",
        assets_id="mania-hits-40000",
    ): partial(process_tth, 40000, 400000, GameMode.MANIA),
    Achievement(
        id=86,
        name="400,000 Keys",
        desc="Four hundred thousand and still not even close.",
        assets_id="mania-hits-400000",
    ): partial(process_tth, 400000, 4000000, GameMode.MANIA),
    Achievement(
        id=87,
        name="4,000,000 Keys",
        desc="Is this the end of the rainbow?",
        assets_id="mania-hits-4000000",
    ): partial(process_tth, 4000000, 40000000, GameMode.MANIA),
    Achievement(
        id=88,
        name="40,000,000 Keys",
        desc="When someone asks which keys you play, the answer is now 'yes'.",
        assets_id="mania-hits-40000000",
    ): partial(process_tth, 40000000, 0, GameMode.MANIA),
}
