from functools import partial

from app.database.score import Beatmap, Score
from app.models.achievement import Achievement, Medals
from app.models.mods import API_MODS

from sqlmodel.ext.asyncio.session import AsyncSession


async def process_mod(
    mod: str,
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    if not score.passed:
        return False
    if not beatmap.beatmap_status.has_leaderboard():
        return False
    return not (len(score.mods) != 1 or score.mods[0]["acronym"] != mod)


async def process_category_mod(
    category: str,
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    if not score.passed:
        return False
    if not beatmap.beatmap_status.has_leaderboard():
        return False
    if len(score.mods) == 0:
        return False
    api_mods = {
        k
        for k, v in API_MODS[int(score.gamemode)].items()  # pyright: ignore[reportArgumentType]
        if v["Type"] == category
    }
    return all(mod["acronym"] in api_mods for mod in score.mods)


MEDALS: Medals = {
    Achievement(
        id=89,
        name="Finality",
        desc="High stakes, no regrets.",
        assets_id="all-intro-suddendeath",
    ): partial(process_mod, "SD"),
    Achievement(
        id=90,
        name="Perfectionist",
        desc="Accept nothing but the best.",
        assets_id="all-intro-perfect",
    ): partial(process_mod, "PF"),
    Achievement(
        id=91,
        name="Rock Around The Clock",
        desc="You can't stop the rock.",
        assets_id="all-intro-hardrock",
    ): partial(process_mod, "HR"),
    Achievement(
        id=92,
        name="Time And A Half",
        desc="Having a right ol' time. One and a half of them, almost.",
        assets_id="all-intro-doubletime",
    ): partial(process_mod, "DT"),
    Achievement(
        id=93,
        name="Sweet Rave Party",
        desc="Founded in the fine tradition of changing things that were just fine as they were.",
        assets_id="all-intro-nightcore",
    ): partial(process_mod, "NC"),
    Achievement(
        id=94,
        name="Blindsight",
        desc="I can see just perfectly.",
        assets_id="all-intro-hidden",
    ): partial(process_mod, "HD"),
    Achievement(
        id=95,
        name="Are You Afraid Of The Dark?",
        desc="Harder than it looks, probably because it's hard to look.",
        assets_id="all-intro-flashlight",
    ): partial(process_mod, "FL"),
    Achievement(
        id=96,
        name="Dial It Right Back",
        desc="Sometimes you just want to take it easy.",
        assets_id="all-intro-easy",
    ): partial(process_mod, "EZ"),
    Achievement(
        id=97,
        name="Risk Averse",
        desc="Safety nets are fun!",
        assets_id="all-intro-nofail",
    ): partial(process_mod, "NF"),
    Achievement(
        id=98,
        name="Slowboat",
        desc="You got there. Eventually.",
        assets_id="all-intro-halftime",
    ): partial(process_mod, "HT"),
    Achievement(
        id=99,
        name="Burned Out",
        desc="One cannot always spin to win.",
        assets_id="all-intro-spunout",
    ): partial(process_mod, "SO"),
    Achievement(
        id=100,
        name="Gear Shift",
        desc="Tailor your experience to your perfect fit.",
        assets_id="all-intro-conversion",
    ): partial(process_category_mod, "Conversion"),
    Achievement(
        id=101,
        name="Game Night",
        desc="Mum said it's my turn with the beatmap!",
        assets_id="all-intro-fun",
    ): partial(process_category_mod, "Fun"),
}
