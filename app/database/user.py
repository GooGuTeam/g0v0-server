from datetime import datetime, timedelta
import json
from typing import TYPE_CHECKING, ClassVar, Literal, NotRequired, TypedDict, overload

from app.config import settings
from app.database.relationship import RelationshipModel
from app.models.notification import NotificationName
from app.models.score import GameMode
from app.models.user import Country, Page
from app.path import STATIC_DIR
from app.utils import utcnow

from ._base import DatabaseModel, OnDemand, included, ondemand
from .achievement import UserAchievement, UserAchievementResp
from .auth import TotpKeys
from .beatmap_playcounts import BeatmapPlaycounts
from .counts import CountResp, MonthlyPlaycounts, ReplayWatchedCount
from .daily_challenge import DailyChallengeStats, DailyChallengeStatsResp
from .events import Event
from .notification import Notification, UserNotification
from .rank_history import RankHistory, RankHistoryResp, RankTop
from .statistics import UserStatistics, UserStatisticsModel
from .team import Team, TeamMember
from .user_account_history import UserAccountHistory, UserAccountHistoryResp, UserAccountHistoryType
from .user_preference import DEFAULT_ORDER, UserPreference

from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import Mapped
from sqlmodel import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Field,
    Relationship,
    col,
    exists,
    func,
    select,
    text,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .favourite_beatmapset import FavouriteBeatmapset
    from .matchmaking import MatchmakingUserStats
    from .relationship import Relationship, RelationshipDict
    from .statistics import UserStatisticsDict


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


class UserDict(TypedDict):
    avatar_url: str
    country_code: str
    id: int
    is_active: bool
    is_bot: bool
    is_supporter: bool
    last_visit: datetime | None
    pm_friends_only: bool
    profile_colour: str | None
    username: str
    g0v0_playmode: GameMode
    page: NotRequired[Page]
    previous_usernames: NotRequired[list[str]]
    support_level: NotRequired[int]
    badges: NotRequired[list[Badge]]
    cover: NotRequired[UserProfileCover]
    beatmap_playcounts_count: NotRequired[int]
    playmode: NotRequired[GameMode]
    discord: NotRequired[str | None]
    has_supported: NotRequired[bool]
    interests: NotRequired[str | None]
    join_date: NotRequired[datetime]
    location: NotRequired[str | None]
    max_blocks: NotRequired[int]
    max_friends: NotRequired[int]
    occupation: NotRequired[str | None]
    playstyle: NotRequired[list[str]]
    profile_hue: NotRequired[int | None]
    title: NotRequired[str | None]
    title_url: NotRequired[str | None]
    twitter: NotRequired[str | None]
    website: NotRequired[str | None]
    comments_count: NotRequired[int]
    post_count: NotRequired[int]
    is_admin: NotRequired[bool]
    is_gmt: NotRequired[bool]
    is_qat: NotRequired[bool]
    is_bng: NotRequired[bool]
    groups: NotRequired[list[str]]
    active_tournament_banners: NotRequired[list[dict]]
    graveyard_beatmapset_count: NotRequired[int]
    loved_beatmapset_count: NotRequired[int]
    mapping_follower_count: NotRequired[int]
    nominated_beatmapset_count: NotRequired[int]
    guest_beatmapset_count: NotRequired[int]
    pending_beatmapset_count: NotRequired[int]
    ranked_beatmapset_count: NotRequired[int]
    follow_user_mapping: NotRequired[list[int]]
    is_deleted: NotRequired[bool]
    country: NotRequired[Country]
    favourite_beatmapset_count: NotRequired[int]
    follower_count: NotRequired[int]
    scores_best_count: NotRequired[int]
    scores_pinned_count: NotRequired[int]
    scores_recent_count: NotRequired[int]
    scores_first_count: NotRequired[int]
    cover_url: NotRequired[str]
    profile_order: NotRequired[list[str]]
    user_preference: NotRequired[UserPreference | None]
    friends: NotRequired[list["RelationshipDict"]]
    team: NotRequired[Team | None]
    account_history: NotRequired[list[UserAccountHistoryResp]]
    daily_challenge_user_stats: NotRequired[DailyChallengeStatsResp | None]
    statistics: NotRequired["UserStatisticsDict | None"]
    statistics_rulesets: NotRequired[dict[str, "UserStatisticsDict"]]
    monthly_playcounts: NotRequired[list[CountResp]]
    replay_watched_counts: NotRequired[list[CountResp]]
    user_achievements: NotRequired[list[UserAchievementResp]]
    rank_history: NotRequired[RankHistoryResp | None]
    rank_highest: NotRequired[RankHighest | None]
    is_restricted: NotRequired[bool]
    kudosu: NotRequired[Kudosu]
    unread_pm_count: NotRequired[int]
    default_group: NotRequired[str]
    is_online: NotRequired[bool]
    session_verified: NotRequired[bool]
    session_verification_method: NotRequired[Literal["totp", "mail"] | None]


class UserModel(DatabaseModel[UserDict]):
    # https://github.com/ppy/osu-web/blob/d0407b1f2846dfd8b85ec0cf20e3fe3028a7b486/app/Transformers/UserCompactTransformer.php#L22-L39
    CARD_INCLUDES: ClassVar[list[str]] = [
        "country",
        "cover",
        "groups",
        "team",
    ]
    LIST_INCLUDES: ClassVar[list[str]] = [
        *CARD_INCLUDES,
        "statistics",
        "support_level",
    ]

    # https://github.com/ppy/osu-web/blob/d0407b1f2846dfd8b85ec0cf20e3fe3028a7b486/app/Transformers/UserTransformer.php#L36-L53
    USER_TRANSFORMER_INCLUDES: ClassVar[list[str]] = [
        "cover_url",
        "discord",
        "has_supported",
        "interests",
        "join_date",
        "location",
        "max_blocks",
        "max_friends",
        "occupation",
        "playmode",
        "playstyle",
        "post_count",
        "profile_hue",
        "profile_order",
        "title",
        "title_url",
        "twitter",
        "website",
        # https://github.com/ppy/osu-web/blob/d0407b1f2846dfd8b85ec0cf20e3fe3028a7b486/app/Transformers/UserTransformer.php#L13C22-L25
        "cover",
        "country",
        "is_admin",
        "is_bng",
        "is_full_bn",
        "is_gmt",
        "is_limited_bn",
        "is_moderator",
        "is_nat",
        "is_restricted",
        "is_silenced",
        "kudosu",
    ]

    # https://github.com/ppy/osu-web/blob/d0407b1f2846dfd8b85ec0cf20e3fe3028a7b486/app/Transformers/UserCompactTransformer.php#L41-L51
    PROFILE_HEADER_INCLUDES: ClassVar[list[str]] = [
        "active_tournament_banner",
        "active_tournament_banners",
        "badges",
        "comments_count",
        "follower_count",
        "groups",
        "mapping_follower_count",
        "previous_usernames",
        "support_level",
    ]

    # https://github.com/ppy/osu-web/blob/3f08fe12d70bcac1e32455c31e984eb6ef589b42/app/Http/Controllers/UsersController.php#L900-L937
    USER_INCLUDES: ClassVar[list[str]] = [
        # == apiIncludes ==
        # historical
        "beatmap_playcounts_count",
        "monthly_playcounts",
        "replays_watched_counts",
        "scores_recent_count",
        # beatmapsets
        "favourite_beatmapset_count",
        "graveyard_beatmapset_count",
        "guest_beatmapset_count",
        "loved_beatmapset_count",
        "nominated_beatmapset_count",
        "pending_beatmapset_count",
        "ranked_beatmapset_count",
        # top scores
        "scores_best_count",
        "scores_first_count",
        "scores_pinned_count",
        # others
        "account_history",
        "current_season_stats",
        "daily_challenge_user_stats",
        "page",
        "pending_beatmapset_count",
        "rank_highest",
        "rank_history",
        "statistics",
        "statistics.country_rank",
        "statistics.rank",
        "statistics.variants",
        "team",
        "user_achievements",
        *PROFILE_HEADER_INCLUDES,
        *USER_TRANSFORMER_INCLUDES,
    ]

    # https://github.com/ppy/osu-web/blob/d0407b1f2846dfd8b85ec0cf20e3fe3028a7b486/app/Transformers/UserCompactTransformer.php#L133-L150
    avatar_url: str = "https://lazer-data.g0v0.top/default.jpg"
    country_code: str = Field(default="CN", max_length=2, index=True)
    # ? default_group: str|None
    id: int = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True, index=True),
    )
    is_active: bool = True
    is_bot: bool = False
    is_supporter: bool = False
    last_visit: datetime | None = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True)))
    pm_friends_only: bool = False
    profile_colour: str | None = None
    username: str = Field(max_length=32, unique=True, index=True)

    page: OnDemand[Page] = Field(sa_column=Column(JSON), default=Page(html="", raw=""))
    previous_usernames: OnDemand[list[str]] = Field(default_factory=list, sa_column=Column(JSON))
    support_level: OnDemand[int] = Field(default=0)
    badges: OnDemand[list[Badge]] = Field(default_factory=list, sa_column=Column(JSON))

    # optional
    # blocks
    cover: OnDemand[UserProfileCover] = Field(
        default=UserProfileCover(url=""),
        sa_column=Column(JSON),
    )
    # kudosu

    # UserExtended
    playmode: OnDemand[GameMode] = Field(default=GameMode.OSU)
    discord: OnDemand[str | None] = Field(default=None)
    has_supported: OnDemand[bool] = Field(default=False)
    interests: OnDemand[str | None] = Field(default=None)
    join_date: OnDemand[datetime] = Field(default_factory=utcnow)
    location: OnDemand[str | None] = Field(default=None)
    max_blocks: OnDemand[int] = Field(default=50)
    max_friends: OnDemand[int] = Field(default=500)
    occupation: OnDemand[str | None] = Field(default=None)
    playstyle: OnDemand[list[str]] = Field(default_factory=list, sa_column=Column(JSON))
    # TODO: post_count
    profile_hue: OnDemand[int | None] = Field(default=None)
    title: OnDemand[str | None] = Field(default=None)
    title_url: OnDemand[str | None] = Field(default=None)
    twitter: OnDemand[str | None] = Field(default=None)
    website: OnDemand[str | None] = Field(default=None)

    # undocumented
    comments_count: OnDemand[int] = Field(default=0)
    post_count: OnDemand[int] = Field(default=0)
    is_admin: OnDemand[bool] = Field(default=False)
    is_gmt: OnDemand[bool] = Field(default=False)
    is_qat: OnDemand[bool] = Field(default=False)
    is_bng: OnDemand[bool] = Field(default=False)

    # g0v0-extra
    g0v0_playmode: GameMode = GameMode.OSU

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

    @ondemand
    @staticmethod
    async def groups(_session: AsyncSession, _obj: "User") -> list[str]:
        return []

    @ondemand
    @staticmethod
    async def active_tournament_banners(_session: AsyncSession, _obj: "User") -> list[dict]:
        return []

    @ondemand
    @staticmethod
    async def graveyard_beatmapset_count(_session: AsyncSession, _obj: "User") -> int:
        return 0

    @ondemand
    @staticmethod
    async def loved_beatmapset_count(_session: AsyncSession, _obj: "User") -> int:
        return 0

    @ondemand
    @staticmethod
    async def mapping_follower_count(_session: AsyncSession, _obj: "User") -> int:
        return 0

    @ondemand
    @staticmethod
    async def nominated_beatmapset_count(_session: AsyncSession, _obj: "User") -> int:
        return 0

    @ondemand
    @staticmethod
    async def guest_beatmapset_count(_session: AsyncSession, _obj: "User") -> int:
        return 0

    @ondemand
    @staticmethod
    async def pending_beatmapset_count(_session: AsyncSession, _obj: "User") -> int:
        return 0

    @ondemand
    @staticmethod
    async def ranked_beatmapset_count(_session: AsyncSession, _obj: "User") -> int:
        return 0

    @ondemand
    @staticmethod
    async def follow_user_mapping(_session: AsyncSession, _obj: "User") -> list[int]:
        return []

    @ondemand
    @staticmethod
    async def is_deleted(_session: AsyncSession, _obj: "User") -> bool:
        return False

    @ondemand
    @staticmethod
    async def country(_session: AsyncSession, obj: "User") -> Country:
        return Country(code=obj.country_code, name=COUNTRIES.get(obj.country_code, "Unknown"))

    @ondemand
    @staticmethod
    async def favourite_beatmapset_count(session: AsyncSession, obj: "User") -> int:
        from .favourite_beatmapset import FavouriteBeatmapset

        return (
            await session.exec(
                select(func.count()).select_from(FavouriteBeatmapset).where(FavouriteBeatmapset.user_id == obj.id)
            )
        ).one()

    @ondemand
    @staticmethod
    async def follower_count(session: AsyncSession, obj: "User") -> int:
        from .relationship import Relationship, RelationshipType

        stmt = (
            select(func.count())
            .select_from(Relationship)
            .where(
                Relationship.target_id == obj.id,
                Relationship.type == RelationshipType.FOLLOW,
            )
        )
        return (await session.exec(stmt)).one()

    @ondemand
    @staticmethod
    async def scores_best_count(
        session: AsyncSession,
        obj: "User",
        ruleset: GameMode | None = None,
    ) -> int:
        from .best_scores import BestScore

        mode = ruleset or obj.playmode
        stmt = (
            select(func.count())
            .select_from(BestScore)
            .where(
                BestScore.user_id == obj.id,
                BestScore.gamemode == mode,
            )
            .limit(200)
        )
        return (await session.exec(stmt)).one()

    @ondemand
    @staticmethod
    async def scores_pinned_count(
        session: AsyncSession,
        obj: "User",
        ruleset: GameMode | None = None,
    ) -> int:
        from .score import Score

        mode = ruleset or obj.playmode
        stmt = (
            select(func.count())
            .select_from(Score)
            .where(
                Score.user_id == obj.id,
                Score.gamemode == mode,
                Score.pinned_order > 0,
                col(Score.passed).is_(True),
            )
        )
        return (await session.exec(stmt)).one()

    @ondemand
    @staticmethod
    async def scores_recent_count(
        session: AsyncSession,
        obj: "User",
        ruleset: GameMode | None = None,
    ) -> int:
        from .score import Score

        mode = ruleset or obj.playmode
        stmt = (
            select(func.count())
            .select_from(Score)
            .where(
                Score.user_id == obj.id,
                Score.gamemode == mode,
                col(Score.passed).is_(True),
                Score.ended_at > utcnow() - timedelta(hours=24),
            )
        )
        return (await session.exec(stmt)).one()

    @ondemand
    @staticmethod
    async def scores_first_count(
        session: AsyncSession,
        obj: "User",
        ruleset: GameMode | None = None,
    ) -> int:
        from .score import get_user_first_score_count

        mode = ruleset or obj.playmode
        return await get_user_first_score_count(session, obj.id, mode)

    @ondemand
    @staticmethod
    async def beatmap_playcounts_count(session: AsyncSession, obj: "User") -> int:
        stmt = select(func.count()).select_from(BeatmapPlaycounts).where(BeatmapPlaycounts.user_id == obj.id)
        return (await session.exec(stmt)).one()

    @ondemand
    @staticmethod
    async def cover_url(_session: AsyncSession, obj: "User") -> str:
        return obj.cover.get("url", "") if obj.cover else ""

    @ondemand
    @staticmethod
    async def profile_order(_session: AsyncSession, obj: "User") -> list[str]:
        await obj.awaitable_attrs.user_preference
        if obj.user_preference:
            return list(obj.user_preference.extras_order)
        return list(DEFAULT_ORDER)

    @ondemand
    @staticmethod
    async def user_preference(_session: AsyncSession, obj: "User") -> UserPreference | None:
        await obj.awaitable_attrs.user_preference
        return obj.user_preference

    @ondemand
    @staticmethod
    async def friends(session: AsyncSession, obj: "User") -> list["RelationshipDict"]:
        from .relationship import Relationship, RelationshipType

        relationships = (
            await session.exec(
                select(Relationship).where(
                    Relationship.user_id == obj.id,
                    Relationship.type == RelationshipType.FOLLOW,
                )
            )
        ).all()
        return [await RelationshipModel.transform(rel, ruleset=obj.playmode) for rel in relationships]

    @ondemand
    @staticmethod
    async def team(_session: AsyncSession, obj: "User") -> Team | None:
        membership = await obj.awaitable_attrs.team_membership
        return membership.team if membership else None

    @ondemand
    @staticmethod
    async def account_history(_session: AsyncSession, obj: "User") -> list[UserAccountHistoryResp]:
        await obj.awaitable_attrs.account_history
        return [UserAccountHistoryResp.from_db(ah) for ah in obj.account_history]

    @ondemand
    @staticmethod
    async def daily_challenge_user_stats(_session: AsyncSession, obj: "User") -> DailyChallengeStatsResp | None:
        stats = await obj.awaitable_attrs.daily_challenge_stats
        return DailyChallengeStatsResp.from_db(stats) if stats else None

    @ondemand
    @staticmethod
    async def statistics(
        _session: AsyncSession,
        obj: "User",
        ruleset: GameMode | None = None,
        includes: list[str] | None = None,
    ) -> "UserStatisticsDict | None":
        mode = ruleset or obj.playmode
        for stat in await obj.awaitable_attrs.statistics:
            if stat.mode == mode:
                return await UserStatisticsModel.transform(stat, user_country=obj.country_code, includes=includes)
        return None

    @ondemand
    @staticmethod
    async def statistics_rulesets(
        _session: AsyncSession,
        obj: "User",
        includes: list[str] | None = None,
    ) -> dict[str, "UserStatisticsDict"]:
        stats = await obj.awaitable_attrs.statistics
        result: dict[str, UserStatisticsDict] = {}
        for stat in stats:
            result[stat.mode.value] = await UserStatisticsModel.transform(
                stat, user_country=obj.country_code, includes=includes
            )
        return result

    @ondemand
    @staticmethod
    async def monthly_playcounts(_session: AsyncSession, obj: "User") -> list[CountResp]:
        playcounts = [CountResp.from_db(pc) for pc in await obj.awaitable_attrs.monthly_playcounts]
        if len(playcounts) == 1:
            d = playcounts[0].start_date
            playcounts.insert(0, CountResp(start_date=d - timedelta(days=20), count=0))
        return playcounts

    @ondemand
    @staticmethod
    async def replay_watched_counts(_session: AsyncSession, obj: "User") -> list[CountResp]:
        counts = [CountResp.from_db(rwc) for rwc in await obj.awaitable_attrs.replays_watched_counts]
        if len(counts) == 1:
            d = counts[0].start_date
            counts.insert(0, CountResp(start_date=d - timedelta(days=20), count=0))
        return counts

    @ondemand
    @staticmethod
    async def user_achievements(_session: AsyncSession, obj: "User") -> list[UserAchievementResp]:
        return [UserAchievementResp.from_db(ua) for ua in await obj.awaitable_attrs.achievement]

    @ondemand
    @staticmethod
    async def rank_history(
        session: AsyncSession,
        obj: "User",
        ruleset: GameMode | None = None,
    ) -> RankHistoryResp | None:
        mode = ruleset or obj.playmode
        rank_history = await RankHistoryResp.from_db(session, obj.id, mode)
        return rank_history if len(rank_history.data) != 0 else None

    @ondemand
    @staticmethod
    async def rank_highest(
        session: AsyncSession,
        obj: "User",
        ruleset: GameMode | None = None,
    ) -> RankHighest | None:
        mode = ruleset or obj.playmode
        rank_top = (await session.exec(select(RankTop).where(RankTop.user_id == obj.id, RankTop.mode == mode))).first()
        if not rank_top:
            return None
        return RankHighest(
            rank=rank_top.rank,
            updated_at=datetime.combine(rank_top.date, datetime.min.time()),
        )

    @ondemand
    @staticmethod
    async def is_restricted(session: AsyncSession, obj: "User") -> bool:
        return await obj.is_restricted(session)

    @ondemand
    @staticmethod
    async def kudosu(_session: AsyncSession, _obj: "User") -> Kudosu:
        return Kudosu(available=0, total=0)  # TODO

    @ondemand
    @staticmethod
    async def unread_pm_count(session: AsyncSession, obj: "User") -> int:
        return (
            await session.exec(
                select(func.count())
                .join(Notification, col(Notification.id) == UserNotification.notification_id)
                .select_from(UserNotification)
                .where(
                    col(UserNotification.is_read).is_(False),
                    UserNotification.user_id == obj.id,
                    Notification.name == NotificationName.CHANNEL_MESSAGE,
                    text("details->>'$.type' = 'pm'"),
                )
            )
        ).one()

    @included
    @staticmethod
    async def default_group(_session: AsyncSession, obj: "User") -> str:
        return "default" if not obj.is_bot else "bot"

    @included
    @staticmethod
    async def is_online(_session: AsyncSession, obj: "User") -> bool:
        from app.dependencies.database import get_redis

        redis = get_redis()
        return bool(await redis.exists(f"metadata:online:{obj.id}"))

    @ondemand
    @staticmethod
    async def session_verified(
        session: AsyncSession,
        obj: "User",
        token_id: int | None = None,
    ) -> bool:
        from app.service.verification_service import LoginSessionService

        return (
            not await LoginSessionService.check_is_need_verification(session, user_id=obj.id, token_id=token_id)
            if token_id
            else True
        )

    @ondemand
    @staticmethod
    async def session_verification_method(
        session: AsyncSession,
        obj: "User",
        token_id: int | None = None,
    ) -> Literal["totp", "mail"] | None:
        from app.dependencies.database import get_redis
        from app.service.verification_service import LoginSessionService

        if (settings.enable_totp_verification or settings.enable_email_verification) and token_id:
            redis = get_redis()
            if not await LoginSessionService.check_is_need_verification(session, user_id=obj.id, token_id=token_id):
                return None
            return await LoginSessionService.get_login_method(obj.id, token_id, redis)
        return None


class User(AsyncAttrs, UserModel, table=True):
    __tablename__: str = "lazer_users"

    email: str = Field(max_length=254, unique=True, index=True)
    priv: int = Field(default=1)
    pw_bcrypt: str = Field(max_length=60)
    silence_end_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    donor_end_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    account_history: list[UserAccountHistory] = Relationship(back_populates="user")
    statistics: list[UserStatistics] = Relationship(back_populates="user")
    achievement: list[UserAchievement] = Relationship(back_populates="user")
    team_membership: TeamMember | None = Relationship(back_populates="user")
    daily_challenge_stats: DailyChallengeStats | None = Relationship(back_populates="user")
    matchmaking_stats: list["MatchmakingUserStats"] = Relationship(back_populates="user")
    monthly_playcounts: list[MonthlyPlaycounts] = Relationship(back_populates="user")
    replays_watched_counts: list[ReplayWatchedCount] = Relationship(back_populates="user")
    favourite_beatmapsets: list["FavouriteBeatmapset"] = Relationship(back_populates="user")
    rank_history: list[RankHistory] = Relationship(
        back_populates="user",
    )
    events: list[Event] = Relationship(back_populates="user")
    totp_key: TotpKeys | None = Relationship(back_populates="user")
    user_preference: UserPreference | None = Relationship(back_populates="user")

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
        if await self.is_restricted(session):
            return False, "Target user is restricted"
        return True, ""

    @classmethod
    @overload
    def is_restricted_query(cls, user_id: int): ...

    @classmethod
    @overload
    def is_restricted_query(cls, user_id: Mapped[int]): ...

    @classmethod
    def is_restricted_query(cls, user_id: int | Mapped[int]):
        return exists().where(
            (col(UserAccountHistory.user_id) == user_id)
            & (col(UserAccountHistory.type) == UserAccountHistoryType.RESTRICTION)
            & (
                (col(UserAccountHistory.permanent).is_(True))
                | (
                    (
                        func.timestampadd(
                            text("SECOND"),
                            col(UserAccountHistory.length),
                            col(UserAccountHistory.timestamp),
                        )
                        > func.now()
                    )
                    & (func.now() > col(UserAccountHistory.timestamp))
                )
            ),
        )

    async def is_restricted(self, session: AsyncSession) -> bool:
        active_restrictions = (await session.exec(select(self.is_restricted_query(self.id)))).first()
        return active_restrictions or False


# 为了向后兼容，在 SQL 查询中使用 User
# 例如: select(User).where(User.id == 1)
# 但类型注解和返回值使用 User
# 例如: async def get_user() -> User | None:
#           return (await session.exec(select(User)...)).first()
