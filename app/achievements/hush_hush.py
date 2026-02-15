from datetime import datetime

from app.calculating import get_calculator
from app.database.beatmap import calculate_beatmap_attributes
from app.database.score import Beatmap, Score
from app.dependencies.database import get_redis
from app.dependencies.fetcher import get_fetcher
from app.models.achievement import Achievement, Medals
from app.models.beatmap import BeatmapRankStatus
from app.models.mods import get_speed_rate, mod_to_save
from app.models.performance import DifficultyAttributesUnion
from app.models.score import Rank

from sqlmodel.ext.asyncio.session import AsyncSession


async def _calculate_attributes(score: Score, beatmap: Beatmap) -> DifficultyAttributesUnion | None:
    fetcher = await get_fetcher()
    redis = get_redis()
    mods_ = score.mods.copy()

    if await get_calculator().can_calculate_difficulty(score.gamemode) is False:
        return None

    attribute = await calculate_beatmap_attributes(beatmap.id, score.gamemode, mods_, redis, fetcher)
    return attribute


async def jackpot(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass a map with a score above 100,000, where every digit of the score is
    # identical (222,222 / 7,777,777 / 99,999,999 / etc).
    return (
        score.passed
        and score.total_score > 100000
        and all(d == str(score.total_score)[0] for d in str(score.total_score))
    )


async def nonstop(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any map with a drain time of 8:41 or longer (before mods).
    return (score.rank == Rank.X or score.rank == Rank.XH) and beatmap.hit_length > 521


async def time_dilation(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass any map that is 8 minutes or longer (after mods),
    # Using either of the specified mods: DT, NC.
    # NF is not allowed, but all other difficulty reducing mods are.
    if not score.passed:
        return False
    mods_ = mod_to_save(score.mods)
    if "NF" in mods_:
        return False
    if "DT" not in mods_ and "NC" not in mods_:
        return False
    rate = get_speed_rate(score.mods)
    return beatmap.hit_length / rate > 480


async def to_the_core(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass any map that contains 'Nightcore' in the title or artist name,
    # using either of the mods specified: DT, NC
    if not score.passed:
        return False
    if ("Nightcore" not in beatmap.beatmapset.title) and "Nightcore" not in beatmap.beatmapset.artist:
        return False
    mods_ = mod_to_save(score.mods)
    return not ("DT" not in mods_ or "NC" not in mods_)


async def wysi(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass any map by song artist "xi" with X7.27% accuracy (97.27%, 87.27%, etc.).
    if not score.passed:
        return False
    if str(round(score.accuracy, ndigits=4))[3:] != "727":
        return False
    return "xi" in beatmap.beatmapset.artist


async def prepared(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any map, using the mods specified: NF
    if score.rank != Rank.X and score.rank != Rank.XH:
        return False
    mods_ = mod_to_save(score.mods)
    return "NF" in mods_


async def reckless_adandon(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any map, using the mods specified: HR, SD, that is star 3+ after mods.
    if score.rank != Rank.X and score.rank != Rank.XH:
        return False
    mods_ = mod_to_save(score.mods)
    if "HR" not in mods_ or "SD" not in mods_:
        return False

    attribute = await _calculate_attributes(score, beatmap)
    if attribute is None:
        return False
    return not attribute.star_rating < 3


async def lights_out(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
):
    # Pass any map, using the mods specified: FL, NC
    if not score.passed:
        return False
    mods_ = mod_to_save(score.mods)
    return "FL" in mods_ and "NC" in mods_


async def camera_shy(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
):
    # PFC any map, using the mods specified: HD, NF
    if score.rank != Rank.X and score.rank != Rank.XH:
        return False
    mods_ = mod_to_save(score.mods)
    return "HD" in mods_ and "NF" in mods_


async def the_sun_of_all_fears(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
):
    # "PFC" any map, but miss on the very first or very last combo.
    if not score.passed:
        return False
    return score.max_combo == (beatmap.max_combo or 0) - 1


async def hour_before_the_down(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any difficulty of ginkiha - EOS.
    if score.rank != Rank.X and score.rank != Rank.XH:
        return False
    return beatmap.beatmapset.artist == "ginkiha" and beatmap.beatmapset.title == "EOS"


async def slow_and_steady(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any map, using the mods specified: HT, PF, that is star 3+ after mods.
    if score.rank != Rank.X and score.rank != Rank.XH:
        return False
    mods_ = mod_to_save(score.mods)
    if "HT" not in mods_ or "PF" not in mods_:
        return False

    attribute = await _calculate_attributes(score, beatmap)
    if attribute is None:
        return False
    return attribute.star_rating >= 3


async def no_time_to_spare(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any map, using the mods specified, that is 30 seconds or shorter (after mods).
    if score.rank != Rank.X and score.rank != Rank.XH:
        return False
    mods_ = mod_to_save(score.mods)
    if "DT" not in mods_ and "NC" not in mods_:
        return False
    rate = get_speed_rate(score.mods)
    return (beatmap.total_length / rate) <= 30


async def sognare(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass LeaF - Evanescent using HT (difficulty reduction allowed)
    if not score.passed:
        return False
    mods_ = mod_to_save(score.mods)
    if "HT" not in mods_:
        return False
    return beatmap.beatmapset.artist == "LeaF" and beatmap.beatmapset.title == "Evanescent"


async def realtor_extraordinaire(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any difficulty of cYsmix - House With Legs using DT/HR (DT/NC interchangeable)
    if score.rank != Rank.X and score.rank != Rank.XH:
        return False
    mods_ = mod_to_save(score.mods)
    if not ("DT" in mods_ or "NC" in mods_) or "HR" not in mods_:
        return False
    return beatmap.beatmapset.artist == "cYsmix" and beatmap.beatmapset.title == "House With Legs"


async def impeccable(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass any map using the mods specified: DT, PF, that is star 4+ after mods.
    if not score.passed:
        return False
    mods_ = mod_to_save(score.mods)
    # DT and NC interchangeable
    if not ("DT" in mods_ or "NC" in mods_) or "PF" not in mods_:
        return False

    attribute = await _calculate_attributes(score, beatmap)
    if attribute is None:
        return False
    return attribute.star_rating >= 4


async def aeon(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any map that was ranked before or during 2011,
    # using the mods specified: FL, HD, HT.
    # The map must be at least star 4+ and at least 3 minutes long after mods.
    if not score.passed:
        return False
    mods_ = mod_to_save(score.mods)
    if "FL" not in mods_ or "HD" not in mods_ or "HT" not in mods_:
        return False
    if not beatmap.beatmapset.ranked_date or beatmap.beatmapset.ranked_date > datetime(2012, 1, 1):
        return False
    if beatmap.total_length < 180:
        return False

    attribute = await _calculate_attributes(score, beatmap)
    if attribute is None:
        return False
    return attribute.star_rating >= 4


async def quick_maths(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Get exactly 34 misses on any difficulty of Function Phantom - Variable.
    if score.nmiss != 34:
        return False
    return beatmap.beatmapset.artist == "Function Phantom" and beatmap.beatmapset.title == "Variable"


async def kaleidoscope(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass The Flashbulb - DIDJ PVC [EX III] with 80% accuracy or higher,
    # using the mods specified: EZ HT
    if not score.passed:
        return False
    mods_ = mod_to_save(score.mods)
    if "EZ" not in mods_ or "HT" not in mods_:
        return False
    return beatmap.id == 2022237 and score.accuracy >= 0.8


async def valediction(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass a_hisa - Alexithymia | Lupinus | Tokei no Heya to Seishin Sekai
    # with 90% accuracy or higher.
    return (
        score.passed
        and beatmap.beatmapset.artist == "a_hisa"
        and beatmap.beatmapset.title == "Alexithymia | Lupinus | Tokei no Heya to Seishin Sekai"
        and score.accuracy >= 0.9
    )


async def right_on_time(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Submit a score on Kola Kid - timer on the first minute of any hour
    if not score.passed:
        return False
    if not (beatmap.beatmapset.artist == "Kola Kid" and beatmap.beatmapset.title == "timer"):
        return False
    return score.ended_at.minute == 0


async def not_again(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass ARForest - Regret. with 1x Miss and 99%+ accuracy
    if not score.passed:
        return False
    if score.nmiss != 1:
        return False
    if score.accuracy < 0.99:
        return False
    return beatmap.beatmapset.artist == "ARForest" and beatmap.beatmapset.title == "Regret"


async def deliberation(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # PFC any ranked or loved map, with HT, that is 6+ stars after mods
    if score.rank != Rank.X and score.rank != Rank.XH:
        return False
    mods_ = mod_to_save(score.mods)
    if "HT" not in mods_:
        return False
    if not beatmap.beatmap_status.has_pp() and beatmap.beatmap_status != BeatmapRankStatus.LOVED:
        return False

    attribute = await _calculate_attributes(score, beatmap)
    if attribute is None:
        return False
    return attribute.star_rating >= 6


async def clarity(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass rrtyui's mapset of Camellia vs Akira Complex - Reality Distortion
    if not score.passed:
        return False
    return beatmap.beatmapset.id == 582089


async def autocreation(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass any map where the artist and the host of the mapset are the same person
    if not score.passed:
        return False
    return beatmap.beatmapset.creator == beatmap.beatmapset.artist


async def value_your_identity(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Achieve a score where max combo equals the last 3 digits of User ID
    if not score.passed:
        return False
    user_id = score.user_id
    last_3_digits = user_id % 1000
    if last_3_digits == 0:
        last_3_digits = 1000
    return score.max_combo == last_3_digits


async def by_the_skin_of_the_teeth(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Set a play using Accuracy Challenge that is exactly the accuracy specified
    if not score.passed:
        return False

    mods_ = mod_to_save(score.mods)
    if "AC" not in mods_:
        return False

    for mod in score.mods:
        if mod.get("acronym") == "AC" and "settings" in mod and "minimum_accuracy" in mod["settings"]:
            target_accuracy = mod["settings"]["minimum_accuracy"]
            if isinstance(target_accuracy, int | float):
                return abs(score.accuracy - float(target_accuracy)) < 0.0001
    return False


async def meticulous_mayhem(
    session: AsyncSession,
    score: Score,
    beatmap: Beatmap,
) -> bool:
    # Pass any map with 15 or more mods enabled
    if not score.passed:
        return False
    return len(score.mods) >= 15


# TODO: Quick Draw, Obsessed, Jack of All Trades, Ten To One, Persistence Is Key
# Tribulation, Replica, All Good, Time Sink, You're Here Forever, Hospitality,
# True North, Superfan, Resurgence, Festive Fever, Deciduous Arborist,
# Infectious Enthusiasm, Exquisite, Mad Scientist
MEDALS: Medals = {
    Achievement(
        id=105,
        name="Jackpot",
        desc="Lucky sevens is a mild understatement.",
        assets_id="all-secret-jackpot",
    ): jackpot,
    Achievement(
        id=106,
        name="Nonstop",
        desc="Breaks? What are those?",
        assets_id="all-secret-nonstop",
    ): nonstop,
    Achievement(
        id=107,
        name="Time Dilation",
        desc="Longer is shorter when all is said and done.",
        assets_id="all-secret-tidi",
    ): time_dilation,
    Achievement(
        id=108,
        name="To The Core",
        desc="In for a penny, in for a pound. Pounding bass, that is.",
        assets_id="all-secret-tothecore",
    ): to_the_core,
    Achievement(
        id=109,
        name="When You See It",
        desc="Three numbers which will haunt you forevermore.",
        assets_id="all-secret-when-you-see-it",
    ): wysi,
    Achievement(
        id=110,
        name="Prepared",
        desc="Do it for real next time.",
        assets_id="all-secret-prepared",
    ): prepared,
    Achievement(
        id=111,
        name="Reckless Abandon",
        desc="Throw it all to the wind.",
        assets_id="all-secret-reckless",
    ): reckless_adandon,
    Achievement(
        id=112,
        name="Lights Out",
        desc="The party's just getting started.",
        assets_id="all-secret-lightsout",
    ): lights_out,
    Achievement(
        id=113,
        name="Camera Shy",
        desc="Stop being cute.",
        assets_id="all-secret-uguushy",
    ): camera_shy,
    Achievement(
        id=114,
        name="The Sun of All Fears",
        desc="Unfortunate.",
        assets_id="all-secret-nuked",
    ): the_sun_of_all_fears,
    Achievement(
        id=115,
        name="Hour Before The Down",
        desc="Eleven skies of everlasting sunrise.",
        assets_id="all-secret-hourbeforethedawn",
    ): hour_before_the_down,
    Achievement(
        id=116,
        name="Slow And Steady",
        desc="Win the race, or start again.",
        assets_id="all-secret-slowandsteady",
    ): slow_and_steady,
    Achievement(
        id=117,
        name="No Time To Spare",
        desc="Places to be, things to do.",
        assets_id="all-secret-ntts",
    ): no_time_to_spare,
    Achievement(
        id=118,
        name="Sognare",
        desc="A dream in stop-motion, soon forever gone.",
        assets_id="all-secret-sognare",
    ): sognare,
    Achievement(
        id=119,
        name="Realtor Extraordinaire",
        desc="An acre-wide stride.",
        assets_id="all-secret-realtor",
    ): realtor_extraordinaire,
    Achievement(
        id=120,
        name="Impeccable",
        desc="Speed matters not to the exemplary.",
        assets_id="all-secret-impeccable",
    ): impeccable,
    Achievement(
        id=121,
        name="Aeon",
        desc="In the mire of thawing time, memory shall be your guide.",
        assets_id="all-secret-aeon",
    ): aeon,
    Achievement(
        id=122,
        name="Quick Maths",
        desc="Beats per minute over... this isn't quick at all!",
        assets_id="all-secret-quickmaffs",
    ): quick_maths,
    Achievement(
        id=123,
        name="Kaleidoscope",
        desc="So many pretty colours. Most of them red.",
        assets_id="all-secret-kaleidoscope",
    ): kaleidoscope,
    Achievement(
        id=124,
        name="Valediction",
        desc="One last time.",
        assets_id="all-secret-valediction",
    ): valediction,
    # Achievement(
    #     id=125,
    #     name="Exquisite",
    #     desc="Indubitably.",
    #     assets_id="all-secret-exquisite",
    # ): exquisite,
    # Achievement(
    #     id=126,
    #     name="Mad Scientist",
    #     desc="The experiment... it's all gone!",
    #     assets_id="all-secret-madscientist",
    # ): mad_scientist,
    Achievement(
        id=127,
        name="Right On Time",
        desc="The first minute is always the hardest.",
        assets_id="all-secret-rightontime",
    ): right_on_time,
    Achievement(
        id=128,
        name="Not Again",
        desc="Regret everything.",
        assets_id="all-secret-notagain",
    ): not_again,
    Achievement(
        id=129,
        name="Deliberation",
        desc="The challenge remains.",
        assets_id="all-secret-deliberation",
    ): deliberation,
    Achievement(
        id=130,
        name="Clarity",
        desc="And yet in our memories, you remain crystal clear.",
        assets_id="all-secret-clarity",
    ): clarity,
    Achievement(
        id=131,
        name="Autocreation",
        desc="Absolute rule.",
        assets_id="all-secret-autocreation",
    ): autocreation,
    Achievement(
        id=132,
        name="Value Your Identity",
        desc="As perfect as you are.",
        assets_id="all-secret-identity",
    ): value_your_identity,
    Achievement(
        id=133,
        name="By The Skin Of The Teeth",
        desc="You're that accurate.",
        assets_id="all-secret-skinoftheteeth",
    ): by_the_skin_of_the_teeth,
    Achievement(
        id=134,
        name="Meticulous Mayhem",
        desc="How did we get here?",
        assets_id="all-secret-meticulousmayhem",
    ): meticulous_mayhem,
}
