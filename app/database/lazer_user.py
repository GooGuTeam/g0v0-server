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
            for i in await obj.awaitable_attrs.statistics:
                if i.mode == ruleset:
                    current_stattistics = i
                    break
            u.statistics = (
                await UserStatisticsResp.from_db(current_stattistics, session, obj.country_code)
                if current_stattistics
                else None
            )

        if "statistics_rulesets" in include:
            u.statistics_rulesets = {
                i.mode.value: await UserStatisticsResp.from_db(i, session, obj.country_code)
                for i in await obj.awaitable_attrs.statistics
            }

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
            rank_history = await RankHistoryResp.from_db(session, obj.id, ruleset)
            if len(rank_history.data) != 0:
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
        u.scores_pinned_count = (
            await session.exec(
                select(func.count())
                .select_from(Score)
                .where(
                    Score.user_id == obj.id,
                    Score.pinned_order > 0,
                    Score.gamemode == ruleset,
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
                    PPBestScore.gamemode == ruleset,
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
                    Score.gamemode == ruleset,
                    col(Score.passed).is_(True),
                    Score.ended_at > utcnow() - timedelta(hours=24),
                )
            )
        ).one()
        u.scores_first_count = await get_user_first_score_count(session, obj.id, ruleset)
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
    ) -> "MeResp":
        from app.dependencies.database import get_redis
        from app.service.verification_service import LoginSessionService

        u = await super().from_db(obj, session, ["session_verified", *include], ruleset, token_id=token_id)
        u = cls.model_validate(u.model_dump())
        if settings.enable_totp_verification or settings.enable_email_verification:
            redis = get_redis()
            if not u.session_verified:
                u.session_verification_method = await LoginSessionService.get_login_method(obj.id, redis)
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
