from datetime import datetime, timedelta
import json
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from app.config import settings
from app.database.auth import TotpKeys
from app.models.model import UTCBaseModel
from app.models.score import GameMode
from app.models.user import Country, Page
from app.path import STATIC_DIR
from app.utils import utcnow

from .achievement import UserAchievement, UserAchievementResp
from .beatmap_playcounts import BeatmapPlaycounts
from .counts import CountResp, MonthlyPlaycounts, ReplayWatchedCount
from .daily_challenge import DailyChallengeStats, DailyChallengeStatsResp
from .events import Event
from .rank_history import RankHistory, RankHistoryResp, RankTop
from .statistics import UserStatistics, UserStatisticsResp
from .team import Team, TeamMember
from .user_account_history import UserAccountHistory, UserAccountHistoryResp

from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Field,
    Relationship,
    SQLModel,
    col,
    func,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .favourite_beatmapset import FavouriteBeatmapset
    from .relationship import RelationshipResp


class Kudosu(TypedDict):
    available: int
    total: int


class RankHighest(TypedDict):
    rank: int
    updated_at: datetime


class UserProfileCover(TypedDict):
    url: str
    custom_url: NotRequired[str]
    id: NotRequired[str]


Badge = TypedDict(
    "Badge",
    {
        "awarded_at": datetime,
        "description": str,
        "image@2x_url": str,
        "image_url": str,
        "url": str,
    },
)

COUNTRIES = json.loads((STATIC_DIR / "iso3166.json").read_text())


class UserBase(UTCBaseModel, SQLModel):
    avatar_url: str = ""
    country_code: str = Field(default="CN", max_length=2, index=True)
    # ? default_group: str|None
    is_active: bool = True
    is_bot: bool = False
    is_supporter: bool = False
    last_visit: datetime | None = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True)))
    pm_friends_only: bool = False
    profile_colour: str | None = None
    username: str = Field(max_length=32, unique=True, index=True)
    page: Page = Field(sa_column=Column(JSON), default=Page(html="", raw=""))
    previous_usernames: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    support_level: int = 0
    badges: list[Badge] = Field(default_factory=list, sa_column=Column(JSON))

    # optional
    is_restricted: bool = False
    # blocks
    cover: UserProfileCover = Field(
        default=UserProfileCover(url=""),
        sa_column=Column(JSON),
    )
    beatmap_playcounts_count: int = 0
    # kudosu

    # UserExtended
    playmode: GameMode = GameMode.OSU
    discord: str | None = None
    has_supported: bool = False
    interests: str | None = None
    join_date: datetime = Field(default_factory=utcnow)
    location: str | None = None
    max_blocks: int = 50
    max_friends: int = 500
    occupation: str | None = None
    playstyle: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # TODO: post_count
    profile_hue: int | None = None
    profile_order: list[str] = Field(
        default_factory=lambda: [
            "me",
            "recent_activity",
            "top_ranks",
            "medals",
            "historical",
            "beatmaps",
            "kudosu",
        ],
        sa_column=Column(JSON),
    )
    title: str | None = None
    title_url: str | None = None
    twitter: str | None = None
    website: str | None = None

    # undocumented
    comments_count: int = 0
    post_count: int = 0
    is_admin: bool = False
    is_gmt: bool = False
    is_qat: bool = False
    is_bng: bool = False

    @field_validator("playmode", mode="before")
    @classmethod
    def validate_playmode(cls, v):
        """将字符串转换为 GameMode 枚举"""
        if isinstance(v, str):
            try:
                return GameMode(v)
            except ValueError:
                # 如果转换失败，返回默认值
                return GameMode.OSU
        return v


class User(AsyncAttrs, UserBase, table=True):
    __tablename__: str = "lazer_users"

    id: int = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True, index=True),
    )
    account_history: list[UserAccountHistory] = Relationship()
    statistics: list[UserStatistics] = Relationship()
    achievement: list[UserAchievement] = Relationship(back_populates="user")
    team_membership: TeamMember | None = Relationship(back_populates="user")
    daily_challenge_stats: DailyChallengeStats | None = Relationship(back_populates="user")
    monthly_playcounts: list[MonthlyPlaycounts] = Relationship(back_populates="user")
    replays_watched_counts: list[ReplayWatchedCount] = Relationship(back_populates="user")
    favourite_beatmapsets: list["FavouriteBeatmapset"] = Relationship(back_populates="user")
    rank_history: list[RankHistory] = Relationship(
        back_populates="user",
    )
    events: list[Event] = Relationship(back_populates="user")
    totp_key: TotpKeys | None = Relationship(back_populates="user")

    email: str = Field(max_length=254, unique=True, index=True, exclude=True)
    priv: int = Field(default=1, exclude=True)
    pw_bcrypt: str = Field(max_length=60, exclude=True)
    silence_end_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)), exclude=True)
    donor_end_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)), exclude=True)

    async def is_user_can_pm(self, from_user: "User", session: AsyncSession) -> tuple[bool, str]:
        from .relationship import Relationship, RelationshipType

        from_relationship = (
            await session.exec(
                select(Relationship).where(
                    Relationship.user_id == from_user.id,
                    Relationship.target_id == self.id,
                )
            )
        ).first()
        if from_relationship and from_relationship.type == RelationshipType.BLOCK:
            return False, "You have blocked the target user."
        if from_user.pm_friends_only and (not from_relationship or from_relationship.type != RelationshipType.FOLLOW):
            return (
                False,
                "You have disabled non-friend communications and target user is not your friend.",
            )

        relationship = (
            await session.exec(
                select(Relationship).where(
                    Relationship.user_id == self.id,
                    Relationship.target_id == from_user.id,
                )
            )
        ).first()
        if relationship and relationship.type == RelationshipType.BLOCK:
            return False, "Target user has blocked you."
        if self.pm_friends_only and (not relationship or relationship.type != RelationshipType.FOLLOW):
            return False, "Target user has disabled non-friend communications"
        return True, ""


class UserResp(UserBase):
    id: int | None = None
    is_online: bool = False
    groups: list = []  # TODO
    country: Country = Field(default_factory=lambda: Country(code="CN", name="China"))
    favourite_beatmapset_count: int = 0
    graveyard_beatmapset_count: int = 0  # TODO
    guest_beatmapset_count: int = 0  # TODO
    loved_beatmapset_count: int = 0  # TODO
    mapping_follower_count: int = 0  # TODO
    nominated_beatmapset_count: int = 0  # TODO
    pending_beatmapset_count: int = 0  # TODO
    ranked_beatmapset_count: int = 0  # TODO
    follow_user_mapping: list[int] = Field(default_factory=list)
    follower_count: int = 0
    friends: list["RelationshipResp"] | None = None
    scores_best_count: int = 0
    scores_first_count: int = 0
    scores_recent_count: int = 0
    scores_pinned_count: int = 0
    beatmap_playcounts_count: int = 0
    account_history: list[UserAccountHistoryResp] = []
    active_tournament_banners: list[dict] = []  # TODO
    kudosu: Kudosu = Field(default_factory=lambda: Kudosu(available=0, total=0))  # TODO
    monthly_playcounts: list[CountResp] = Field(default_factory=list)
    replay_watched_counts: list[CountResp] = Field(default_factory=list)
    unread_pm_count: int = 0  # TODO
    rank_history: RankHistoryResp | None = None
    rank_highest: RankHighest | None = None
    statistics: UserStatisticsResp | None = None
    statistics_rulesets: dict[str, UserStatisticsResp] | None = None
    user_achievements: list[UserAchievementResp] = Field(default_factory=list)
    cover_url: str = ""  # deprecated
    team: Team | None = None
    session_verified: bool = True
    daily_challenge_user_stats: DailyChallengeStatsResp | None = None
    default_group: str = ""
    is_deleted: bool = False  # TODO

    # TODO: monthly_playcounts, unread_pm_count， rank_history, user_preferences

    @classmethod
    async def from_db(
        cls,
        obj: User,
        session: AsyncSession,
        include: list[str] = [],
        ruleset: GameMode | None = None,
        *,
        token_id: int | None = None,
        client_type: str | None = None,
    ) -> "UserResp":
        from app.dependencies.database import get_redis

        from .best_score import BestScore
        from .favourite_beatmapset import FavouriteBeatmapset
        from .pp_best_score import PPBestScore
        from .relationship import Relationship, RelationshipResp, RelationshipType
        from .score import Score, get_user_first_score_count

        ruleset = ruleset or obj.playmode

        u = cls.model_validate(obj.model_dump())
        u.id = obj.id
        u.default_group = "bot" if u.is_bot else "default"

        # 根据客户端类型转换默认游戏模式
        if client_type == "osu_lazer":
            u.playmode = _convert_mode_for_lazer_client(obj.playmode)
        else:
            # 对于未知客户端类型或公开查看，保持原始模式
            u.playmode = obj.playmode
        u.country = Country(code=obj.country_code, name=COUNTRIES.get(obj.country_code, "Unknown"))
        u.follower_count = (
            await session.exec(
                select(func.count())
                .select_from(Relationship)
                .where(
                    Relationship.target_id == obj.id,
                    Relationship.type == RelationshipType.FOLLOW,
                )
            )
        ).one()
        u.scores_best_count = (
            await session.exec(
                select(func.count())
                .select_from(BestScore)
                .where(
                    BestScore.user_id == obj.id,
                )
                .limit(200)
            )
        ).one()
        redis = get_redis()
        u.is_online = bool(await redis.exists(f"metadata:online:{obj.id}"))
        u.cover_url = obj.cover.get("url", "") if obj.cover else ""

        if "friends" in include:
            u.friends = [
                await RelationshipResp.from_db(session, r)
                for r in (
                    await session.exec(
                        select(Relationship).where(
                            Relationship.user_id == obj.id,
                            Relationship.type == RelationshipType.FOLLOW,
                        )
                    )
                ).all()
            ]

        if "team" in include:
            if team_membership := await obj.awaitable_attrs.team_membership:
                u.team = team_membership.team

        if "account_history" in include:
            u.account_history = [UserAccountHistoryResp.from_db(ah) for ah in await obj.awaitable_attrs.account_history]

        if "daily_challenge_user_stats":
            if daily_challenge_stats := await obj.awaitable_attrs.daily_challenge_stats:
                u.daily_challenge_user_stats = DailyChallengeStatsResp.from_db(daily_challenge_stats)

        if "statistics" in include:
            current_stattistics = None

            if client_type == "osu_lazer":
                # osu_lazer 客户端：查找用户的实际模式数据，但返回时显示标准模式
                search_modes, display_mode = _get_search_modes_for_lazer(ruleset, obj.playmode)

                # 按优先级查找统计数据
                for search_mode in search_modes:
                    for i in await obj.awaitable_attrs.statistics:
                        if i.mode == search_mode:
                            current_stattistics = i
                            break
                    if current_stattistics:
                        break

                if current_stattistics:
                    u.statistics = await UserStatisticsResp.from_db(current_stattistics, session, obj.country_code)
                    u.statistics.mode = display_mode  # 显示标准模式名称
                else:
                    u.statistics = None
            else:
                # 其他客户端：直接使用请求的模式
                for i in await obj.awaitable_attrs.statistics:
                    if i.mode == ruleset:
                        current_stattistics = i
                        break
                if current_stattistics:
                    u.statistics = await UserStatisticsResp.from_db(current_stattistics, session, obj.country_code)
                    u.statistics.mode = ruleset
                else:
                    u.statistics = None

        if "statistics_rulesets" in include:
            statistics_rulesets = {}

            if client_type == "osu_lazer":
                # osu_lazer 客户端：只返回标准模式的统计数据
                standard_modes = {GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA}
                for i in await obj.awaitable_attrs.statistics:
                    # 将扩展模式转换为对应的标准模式
                    standard_mode = _convert_extended_mode_to_standard(i.mode)

                    # 只处理标准模式，避免重复
                    if standard_mode in standard_modes and standard_mode.value not in statistics_rulesets:
                        stat_resp = await UserStatisticsResp.from_db(i, session, obj.country_code)
                        stat_resp.mode = standard_mode
                        statistics_rulesets[standard_mode.value] = stat_resp
            else:
                # osu_web 等其他客户端：返回所有模式的统计数据
                processed_standard_modes = set()

                for i in await obj.awaitable_attrs.statistics:
                    stat_resp = await UserStatisticsResp.from_db(i, session, obj.country_code)
                    statistics_rulesets[i.mode.value] = stat_resp

                    # 为标准模式创建对应的扩展模式统计数据
                    if i.mode == GameMode.OSU and GameMode.OSU not in processed_standard_modes:
                        processed_standard_modes.add(GameMode.OSU)
                        # 为 osurx 和 osuap 创建相同的统计数据
                        osurx_resp = await UserStatisticsResp.from_db(i, session, obj.country_code)
                        osurx_resp.mode = GameMode.OSURX
                        statistics_rulesets[GameMode.OSURX.value] = osurx_resp

                        osuap_resp = await UserStatisticsResp.from_db(i, session, obj.country_code)
                        osuap_resp.mode = GameMode.OSUAP
                        statistics_rulesets[GameMode.OSUAP.value] = osuap_resp
                    elif i.mode == GameMode.TAIKO and GameMode.TAIKO not in processed_standard_modes:
                        processed_standard_modes.add(GameMode.TAIKO)
                        # 为 taikorx 创建相同的统计数据
                        taikorx_resp = await UserStatisticsResp.from_db(i, session, obj.country_code)
                        taikorx_resp.mode = GameMode.TAIKORX
                        statistics_rulesets[GameMode.TAIKORX.value] = taikorx_resp
                    elif i.mode == GameMode.FRUITS and GameMode.FRUITS not in processed_standard_modes:
                        processed_standard_modes.add(GameMode.FRUITS)
                        # 为 fruitsrx 创建相同的统计数据
                        fruitsrx_resp = await UserStatisticsResp.from_db(i, session, obj.country_code)
                        fruitsrx_resp.mode = GameMode.FRUITSRX
                        statistics_rulesets[GameMode.FRUITSRX.value] = fruitsrx_resp

            u.statistics_rulesets = statistics_rulesets

        if "monthly_playcounts" in include:
            u.monthly_playcounts = [CountResp.from_db(pc) for pc in await obj.awaitable_attrs.monthly_playcounts]
            if len(u.monthly_playcounts) == 1:
                d = u.monthly_playcounts[0].start_date
                u.monthly_playcounts.insert(0, CountResp(start_date=d - timedelta(days=20), count=0))

        if "replays_watched_counts" in include:
            u.replay_watched_counts = [
                CountResp.from_db(rwc) for rwc in await obj.awaitable_attrs.replays_watched_counts
            ]
            if len(u.replay_watched_counts) == 1:
                d = u.replay_watched_counts[0].start_date
                u.replay_watched_counts.insert(0, CountResp(start_date=d - timedelta(days=20), count=0))

        if "achievements" in include:
            u.user_achievements = [UserAchievementResp.from_db(ua) for ua in await obj.awaitable_attrs.achievement]
        if "rank_history" in include:
            rank_history = None
            rank_top = None

            if client_type == "osu_lazer":
                # osu_lazer 客户端：查找用户的实际模式数据，但返回时显示标准模式
                search_modes, display_mode = _get_search_modes_for_lazer(ruleset, obj.playmode)

                # 按优先级查找段位历史数据
                for search_mode in search_modes:
                    rank_history = await RankHistoryResp.from_db(session, obj.id, search_mode)
                    if len(rank_history.data) != 0:
                        rank_history.mode = display_mode  # 显示标准模式名称
                        break
                    rank_top = (
                        await session.exec(
                            select(RankTop).where(RankTop.user_id == obj.id, RankTop.mode == search_mode)
                        )
                    ).first()
                    if rank_top:
                        break

                if rank_history and len(rank_history.data) != 0:
                    u.rank_history = rank_history
            else:
                # 其他客户端：直接使用请求的模式
                rank_history = await RankHistoryResp.from_db(session, obj.id, ruleset)
                if len(rank_history.data) != 0:
                    rank_history.mode = ruleset
                    u.rank_history = rank_history

                rank_top = (
                    await session.exec(select(RankTop).where(RankTop.user_id == obj.id, RankTop.mode == ruleset))
                ).first()
            if rank_top:
                u.rank_highest = (
                    RankHighest(
                        rank=rank_top.rank,
                        updated_at=datetime.combine(rank_top.date, datetime.min.time()),
                    )
                    if rank_top
                    else None
                )

        u.favourite_beatmapset_count = (
            await session.exec(
                select(func.count()).select_from(FavouriteBeatmapset).where(FavouriteBeatmapset.user_id == obj.id)
            )
        ).one()
        # 根据客户端类型确定查询的模式
        if client_type == "osu_lazer":
            # osu_lazer 客户端：查找用户的实际模式数据
            search_modes, display_mode = _get_search_modes_for_lazer(ruleset, obj.playmode)
            # 使用第一个可用的模式进行查询（优先用户偏好的模式）
            query_mode = search_modes[0]
        else:
            # 其他客户端：直接使用请求的模式
            query_mode = ruleset

        u.scores_pinned_count = (
            await session.exec(
                select(func.count())
                .select_from(Score)
                .where(
                    Score.user_id == obj.id,
                    Score.pinned_order > 0,
                    Score.gamemode == query_mode,
                    col(Score.passed).is_(True),
                )
            )
        ).one()
        u.scores_best_count = (
            await session.exec(
                select(func.count())
                .select_from(PPBestScore)
                .where(
                    PPBestScore.user_id == obj.id,
                    PPBestScore.gamemode == query_mode,
                )
                .limit(200)
            )
        ).one()
        u.scores_recent_count = (
            await session.exec(
                select(func.count())
                .select_from(Score)
                .where(
                    Score.user_id == obj.id,
                    Score.gamemode == query_mode,
                    col(Score.passed).is_(True),
                    Score.ended_at > utcnow() - timedelta(hours=24),
                )
            )
        ).one()
        u.scores_first_count = await get_user_first_score_count(session, obj.id, query_mode)
        u.beatmap_playcounts_count = (
            await session.exec(
                select(func.count())
                .select_from(BeatmapPlaycounts)
                .where(
                    BeatmapPlaycounts.user_id == obj.id,
                )
            )
        ).one()

        if "session_verified" in include:
            from app.service.verification_service import LoginSessionService

            u.session_verified = (
                not await LoginSessionService.check_is_need_verification(session, user_id=obj.id, token_id=token_id)
                if token_id
                else True
            )

        return u


class MeResp(UserResp):
    session_verification_method: Literal["totp", "mail"] | None = None

    @classmethod
    async def from_db(
        cls,
        obj: User,
        session: AsyncSession,
        include: list[str] = [],
        ruleset: GameMode | None = None,
        *,
        token_id: int | None = None,
        client_type: str | None = None,
    ) -> "MeResp":
        from app.dependencies.database import get_redis
        from app.service.verification_service import LoginSessionService

        u = await super().from_db(
            obj, session, ["session_verified", *include], ruleset, token_id=token_id, client_type=client_type
        )
        u = cls.model_validate(u.model_dump())
        if (settings.enable_totp_verification or settings.enable_email_verification) and token_id:
            redis = get_redis()
            if not u.session_verified:
                u.session_verification_method = await LoginSessionService.get_login_method(obj.id, token_id, redis)
        else:
            u.session_verification_method = None
        return u


ALL_INCLUDED = [
    "friends",
    "team",
    "account_history",
    "daily_challenge_user_stats",
    "statistics",
    "statistics_rulesets",
    "achievements",
    "monthly_playcounts",
    "replays_watched_counts",
    "rank_history",
    "session_verified",
]


SEARCH_INCLUDED = [
    "team",
    "daily_challenge_user_stats",
    "statistics",
    "statistics_rulesets",
    "achievements",
    "monthly_playcounts",
    "replays_watched_counts",
    "rank_history",
]

BASE_INCLUDES = [
    "team",
    "daily_challenge_user_stats",
    "statistics",
]

RANKING_INCLUDES = [
    "team",
    "statistics",
]


def _convert_mode_for_lazer_client(mode: GameMode) -> GameMode:
    """
    为 osu_lazer 客户端转换游戏模式
    将扩展模式转换为标准模式，因为 lazer 客户端不支持显示扩展模式

    Args:
        mode: 原始游戏模式

    Returns:
        GameMode: 转换后的游戏模式
    """
    conversion_map = {
        GameMode.OSURX: GameMode.OSU,  # osurx -> osu
        GameMode.OSUAP: GameMode.OSU,  # osuap -> osu
        GameMode.TAIKORX: GameMode.TAIKO,  # taikorx -> taiko
        GameMode.FRUITSRX: GameMode.FRUITS,  # fruitsrx -> fruits
    }
    return conversion_map.get(mode, mode)


def _convert_extended_mode_to_standard(mode: GameMode) -> GameMode:
    """
    将扩展模式转换为对应的标准模式，用于数据库查询

    Args:
        mode: 游戏模式

    Returns:
        GameMode: 对应的标准模式
    """
    conversion_map = {
        GameMode.OSURX: GameMode.OSU,  # osurx -> osu
        GameMode.OSUAP: GameMode.OSU,  # osuap -> osu
        GameMode.TAIKORX: GameMode.TAIKO,  # taikorx -> taiko
        GameMode.FRUITSRX: GameMode.FRUITS,  # fruitsrx -> fruits
    }
    return conversion_map.get(mode, mode)


def _get_search_modes_for_lazer(ruleset: GameMode, user_playmode: GameMode) -> tuple[list[GameMode], GameMode]:
    """
    为 osu_lazer 客户端获取查询模式优先级列表和显示模式

    Args:
        ruleset: 请求的模式（可能是标准模式或扩展模式）
        user_playmode: 用户的默认模式

    Returns:
        tuple: (查询模式列表, 显示模式)
    """
    # 首先确定显示的标准模式
    display_mode = _convert_extended_mode_to_standard(ruleset)

    # 然后确定查询优先级
    if display_mode == GameMode.OSU:
        if user_playmode in [GameMode.OSURX, GameMode.OSUAP]:
            search_modes = [user_playmode, GameMode.OSURX, GameMode.OSUAP, GameMode.OSU]
        elif ruleset in [GameMode.OSURX, GameMode.OSUAP]:
            # 如果请求的就是扩展模式，优先使用该扩展模式
            search_modes = [ruleset, GameMode.OSURX, GameMode.OSUAP, GameMode.OSU]
        else:
            search_modes = [GameMode.OSU, GameMode.OSURX, GameMode.OSUAP]
    elif display_mode == GameMode.TAIKO:
        if user_playmode == GameMode.TAIKORX:
            search_modes = [GameMode.TAIKORX, GameMode.TAIKO]
        elif ruleset == GameMode.TAIKORX:
            search_modes = [GameMode.TAIKORX, GameMode.TAIKO]
        else:
            search_modes = [GameMode.TAIKO, GameMode.TAIKORX]
    elif display_mode == GameMode.FRUITS:
        if user_playmode == GameMode.FRUITSRX:
            search_modes = [GameMode.FRUITSRX, GameMode.FRUITS]
        elif ruleset == GameMode.FRUITSRX:
            search_modes = [GameMode.FRUITSRX, GameMode.FRUITS]
        else:
            search_modes = [GameMode.FRUITS, GameMode.FRUITSRX]
    else:
        search_modes = [ruleset]

    return search_modes, display_mode
