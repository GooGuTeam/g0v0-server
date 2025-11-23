from collections.abc import Sequence
from datetime import date, datetime
import json
import math
import sys
from typing import TYPE_CHECKING, Any, ClassVar, NotRequired, TypedDict

from app.calculator import (
    calculate_pp_weight,
    calculate_score_to_level,
    calculate_weighted_acc,
    calculate_weighted_pp,
    clamp,
    get_display_score,
    pre_fetch_and_calculate_pp,
)
from app.config import settings
from app.dependencies.database import get_redis
from app.log import log
from app.models.beatmap import BeatmapRankStatus
from app.models.model import (
    CurrentUserAttributes,
    PinAttributes,
    RespWithCursor,
    UTCBaseModel,
)
from app.models.mods import APIMod, get_speed_rate, mod_to_save, mods_can_get_pp
from app.models.score import (
    GameMode,
    HitResult,
    LeaderboardType,
    Rank,
    ScoreStatistics,
    SoloScoreSubmissionInfo,
)
from app.models.scoring_mode import ScoringMode
from app.storage import StorageService
from app.utils import utcnow

from ._base import DatabaseModel, OnDemand, included, ondemand
from .beatmap import Beatmap, BeatmapDict, BeatmapModel
from .beatmap_playcounts import BeatmapPlaycounts
from .beatmapset import BeatmapsetDict, BeatmapsetModel
from .best_scores import BestScore
from .counts import MonthlyPlaycounts
from .events import Event, EventType
from .playlist_best_score import PlaylistBestScore
from .relationship import (
    Relationship as DBRelationship,
    RelationshipType,
)
from .score_token import ScoreToken
from .team import TeamMember
from .total_score_best_scores import TotalScoreBestScore
from .user import User, UserDict, UserModel

from pydantic import BaseModel, field_serializer, field_validator
from redis.asyncio import Redis
from sqlalchemy import Boolean, Column, DateTime, TextClause
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import Mapped, joinedload
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import (
    JSON,
    BigInteger,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    col,
    func,
    select,
    text,
    true,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.fetcher import Fetcher

logger = log("Score")


class ScoreDict(TypedDict):
    beatmap_id: int
    id: int
    rank: Rank
    type: str
    user_id: int
    accuracy: float
    build_id: int | None
    ended_at: datetime
    has_replay: bool
    max_combo: int
    passed: bool
    pp: float
    started_at: datetime
    total_score: int
    maximum_statistics: ScoreStatistics
    mods: list[APIMod]
    classic_total_score: int | None
    preserve: bool
    processed: bool
    ranked: bool
    playlist_item_id: NotRequired[int | None]
    room_id: NotRequired[int | None]
    best_id: NotRequired[int | None]
    legacy_perfect: NotRequired[bool]
    is_perfect_combo: NotRequired[bool]
    ruleset_id: NotRequired[int]
    statistics: NotRequired[ScoreStatistics]
    beatmapset: NotRequired[BeatmapsetDict]
    beatmap: NotRequired[BeatmapDict]
    current_user_attributes: NotRequired[CurrentUserAttributes]
    position: NotRequired[int | None]
    scores_around: NotRequired["ScoreAround | None"]
    rank_country: NotRequired[int | None]
    rank_global: NotRequired[int | None]
    user: NotRequired[UserDict]
    weight: NotRequired[float | None]

    # ScoreResp 字段
    legacy_total_score: NotRequired[int]


class ScoreModel(AsyncAttrs, DatabaseModel[ScoreDict]):
    # https://github.com/ppy/osu-web/blob/master/app/Transformers/ScoreTransformer.php#L72-L84
    MULTIPLAYER_SCORE_INCLUDE: ClassVar[list[str]] = ["playlist_item_id", "room_id", "solo_score_id"]
    MULTIPLAYER_BASE_INCLUDES: ClassVar[list[str]] = [
        "user.country",
        "user.cover",
        "user.team",
        *MULTIPLAYER_SCORE_INCLUDE,
    ]
    USER_PROFILE_INCLUDES: ClassVar[list[str]] = ["beatmap", "beatmapset", "user"]

    # 基本字段
    beatmap_id: int = Field(index=True, foreign_key="beatmaps.id")
    id: int = Field(default=None, sa_column=Column(BigInteger, autoincrement=True, primary_key=True))
    rank: Rank
    type: str
    user_id: int = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("lazer_users.id"),
            index=True,
        ),
    )
    accuracy: float
    build_id: int | None = Field(default=None)
    ended_at: datetime = Field(sa_column=Column(DateTime))
    has_replay: bool = Field(sa_column=Column(Boolean))
    max_combo: int
    passed: bool = Field(sa_column=Column(Boolean))
    pp: float = Field(default=0.0)
    started_at: datetime = Field(sa_column=Column(DateTime))
    total_score: int = Field(default=0, sa_column=Column(BigInteger))
    maximum_statistics: ScoreStatistics = Field(sa_column=Column(JSON), default_factory=dict)
    mods: list[APIMod] = Field(sa_column=Column(JSON))
    total_score_without_mods: int = Field(default=0, sa_column=Column(BigInteger), exclude=True)

    # solo
    classic_total_score: int | None = Field(default=0, sa_column=Column(BigInteger))
    preserve: bool = Field(default=True, sa_column=Column(Boolean))
    processed: bool = Field(default=False)
    ranked: bool = Field(default=False)

    # multiplayer
    playlist_item_id: OnDemand[int | None] = Field(default=None)
    room_id: OnDemand[int | None] = Field(default=None)

    @included
    @staticmethod
    async def best_id(
        session: AsyncSession,
        score: "Score",
    ) -> int | None:
        return await get_best_id(session, score.id)

    @included
    @staticmethod
    async def legacy_perfect(
        _session: AsyncSession,
        score: "Score",
    ) -> bool:
        await score.awaitable_attrs.beatmap
        return score.max_combo == score.beatmap.max_combo

    @included
    @staticmethod
    async def is_perfect_combo(
        _session: AsyncSession,
        score: "Score",
    ) -> bool:
        await score.awaitable_attrs.beatmap
        return score.max_combo == score.beatmap.max_combo

    @included
    @staticmethod
    async def ruleset_id(
        _session: AsyncSession,
        score: "Score",
    ) -> int:
        return int(score.gamemode)

    @included
    @staticmethod
    async def statistics(
        _session: AsyncSession,
        score: "Score",
    ) -> ScoreStatistics:
        stats = {
            HitResult.MISS: score.nmiss,
            HitResult.MEH: score.n50,
            HitResult.OK: score.n100,
            HitResult.GREAT: score.n300,
            HitResult.PERFECT: score.ngeki,
            HitResult.GOOD: score.nkatu,
        }
        if score.nlarge_tick_miss is not None:
            stats[HitResult.LARGE_TICK_MISS] = score.nlarge_tick_miss
        if score.nslider_tail_hit is not None:
            stats[HitResult.SLIDER_TAIL_HIT] = score.nslider_tail_hit
        if score.nsmall_tick_hit is not None:
            stats[HitResult.SMALL_TICK_HIT] = score.nsmall_tick_hit
        if score.nlarge_tick_hit is not None:
            stats[HitResult.LARGE_TICK_HIT] = score.nlarge_tick_hit
        return stats

    @ondemand
    @staticmethod
    async def beatmapset(
        _session: AsyncSession,
        score: "Score",
        includes: list[str] | None = None,
    ) -> BeatmapsetDict:
        await score.awaitable_attrs.beatmap
        return await BeatmapsetModel.transform(score.beatmap.beatmapset, includes=includes)

    # reorder beatmapset and beatmap
    # https://github.com/ppy/osu/blob/d8900defd34690de92be3406003fb3839fc0df1d/osu.Game/Online/API/Requests/Responses/SoloScoreInfo.cs#L111-L112
    @ondemand
    @staticmethod
    async def beatmap(
        _session: AsyncSession,
        score: "Score",
        includes: list[str] | None = None,
    ) -> BeatmapDict:
        await score.awaitable_attrs.beatmap
        return await BeatmapModel.transform(score.beatmap, includes=includes)

    @ondemand
    @staticmethod
    async def current_user_attributes(
        _session: AsyncSession,
        score: "Score",
    ) -> CurrentUserAttributes:
        return CurrentUserAttributes(pin=PinAttributes(is_pinned=bool(score.pinned_order), score_id=score.id))

    @ondemand
    @staticmethod
    async def position(
        session: AsyncSession,
        score: "Score",
    ) -> int | None:
        return await get_score_position_by_id(
            session,
            score.beatmap_id,
            score.id,
            mode=score.gamemode,
            user=score.user,
        )

    @ondemand
    @staticmethod
    async def scores_around(
        session: AsyncSession, _score: "Score", playlist_id: int, room_id: int, is_playlist: bool
    ) -> "ScoreAround | None":
        scores = (
            await session.exec(
                select(PlaylistBestScore).where(
                    PlaylistBestScore.playlist_id == playlist_id,
                    PlaylistBestScore.room_id == room_id,
                    ~User.is_restricted_query(col(PlaylistBestScore.user_id)),
                    col(PlaylistBestScore.score).has(col(Score.passed).is_(True)) if not is_playlist else True,
                )
            )
        ).all()

        higher_scores = []
        lower_scores = []
        for score in scores:
            total_score = score.score.total_score
            resp = await ScoreModel.transform(score.score, includes=ScoreModel.MULTIPLAYER_BASE_INCLUDES)
            if score.total_score > total_score:
                higher_scores.append(resp)
            elif score.total_score < total_score:
                lower_scores.append(resp)

        return ScoreAround(
            higher=MultiplayerScores(scores=higher_scores),
            lower=MultiplayerScores(scores=lower_scores),
        )

    @ondemand
    @staticmethod
    async def rank_country(
        session: AsyncSession,
        score: "Score",
    ) -> int | None:
        return (
            await get_score_position_by_id(
                session,
                score.beatmap_id,
                score.id,
                score.gamemode,
                score.user,
                type=LeaderboardType.COUNTRY,
            )
            or None
        )

    @ondemand
    @staticmethod
    async def rank_global(
        session: AsyncSession,
        score: "Score",
    ) -> int | None:
        return (
            await get_score_position_by_id(
                session,
                score.beatmap_id,
                score.id,
                mode=score.gamemode,
                user=score.user,
            )
            or None
        )

    @ondemand
    @staticmethod
    async def user(
        _session: AsyncSession,
        score: "Score",
        includes: list[str] | None = None,
    ) -> UserDict:
        return await UserModel.transform(score.user, ruleset=score.gamemode, includes=includes or [])

    @ondemand
    @staticmethod
    async def weight(
        session: AsyncSession,
        score: "Score",
    ) -> float | None:
        best_id = await get_best_id(session, score.id)
        if best_id:
            return calculate_pp_weight(best_id - 1)
        return None

    @ondemand
    @staticmethod
    async def legacy_total_score(
        _session: AsyncSession,
        _score: "Score",
    ) -> int:
        return 0

    @field_validator("maximum_statistics", mode="before")
    @classmethod
    def validate_maximum_statistics(cls, v):
        """处理 maximum_statistics 字段中的字符串键，转换为 HitResult 枚举"""
        if isinstance(v, dict):
            converted = {}
            for key, value in v.items():
                if isinstance(key, str):
                    try:
                        # 尝试将字符串转换为 HitResult 枚举
                        enum_key = HitResult(key)
                        converted[enum_key] = value
                    except ValueError:
                        # 如果转换失败，跳过这个键值对
                        continue
                else:
                    converted[key] = value
            return converted
        return v

    @field_serializer("maximum_statistics", when_used="json")
    def serialize_maximum_statistics(self, v):
        """序列化 maximum_statistics 字段，确保枚举值正确转换为字符串"""
        if isinstance(v, dict):
            serialized = {}
            for key, value in v.items():
                if hasattr(key, "value"):
                    # 如果是枚举，使用其值
                    serialized[key.value] = value
                else:
                    # 否则直接使用键
                    serialized[str(key)] = value
            return serialized
        return v

    @field_serializer("rank", when_used="json")
    def serialize_rank(self, v):
        """序列化等级，确保枚举值正确转换为字符串"""
        if hasattr(v, "value"):
            return v.value
        return str(v)

    # optional
    # TODO: current_user_attributes


class Score(ScoreModel, table=True):
    __tablename__: str = "scores"

    # ScoreStatistics
    n300: int = Field(exclude=True)
    n100: int = Field(exclude=True)
    n50: int = Field(exclude=True)
    nmiss: int = Field(exclude=True)
    ngeki: int = Field(exclude=True)
    nkatu: int = Field(exclude=True)
    nlarge_tick_miss: int | None = Field(default=None, exclude=True)
    nlarge_tick_hit: int | None = Field(default=None, exclude=True)
    nslider_tail_hit: int | None = Field(default=None, exclude=True)
    nsmall_tick_hit: int | None = Field(default=None, exclude=True)
    gamemode: GameMode = Field(index=True)
    pinned_order: int = Field(default=0, exclude=True)
    map_md5: str = Field(max_length=32, index=True, exclude=True)

    @field_validator("gamemode", mode="before")
    @classmethod
    def validate_gamemode(cls, v):
        """将字符串转换为 GameMode 枚举"""
        if isinstance(v, str):
            try:
                return GameMode(v)
            except ValueError:
                # 如果转换失败，返回默认值
                return GameMode.OSU
        return v

    @field_serializer("gamemode", when_used="json")
    def serialize_gamemode(self, v):
        """序列化游戏模式，确保枚举值正确转换为字符串"""
        if hasattr(v, "value"):
            return v.value
        return str(v)

    # optional
    beatmap: Mapped[Beatmap] = Relationship()
    user: Mapped[User] = Relationship(sa_relationship_kwargs={"lazy": "joined"})
    best_score: Mapped[TotalScoreBestScore | None] = Relationship(
        back_populates="score",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
        },
    )
    ranked_score: Mapped[BestScore | None] = Relationship(
        back_populates="score",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
        },
    )
    playlist_item_score: Mapped[PlaylistBestScore | None] = Relationship(
        back_populates="score",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
        },
    )

    @property
    def is_perfect_combo(self) -> bool:
        return self.max_combo == self.beatmap.max_combo

    @property
    def replay_filename(self) -> str:
        return f"replays/{self.id}_{self.beatmap_id}_{self.user_id}_lazer_replay.osr"

    def get_display_score(self, mode: ScoringMode | None = None) -> int:
        """
        Get the display score for this score based on the scoring mode.

        Args:
            mode: The scoring mode to use. If None, uses the global setting.

        Returns:
            The display score in the requested scoring mode
        """
        if mode is None:
            mode = settings.scoring_mode

        return get_display_score(
            ruleset_id=int(self.gamemode),
            total_score=self.total_score,
            mode=mode,
            maximum_statistics=self.maximum_statistics,
        )

    async def to_resp(
        self, session: AsyncSession, api_version: int, includes: list[str] = []
    ) -> "ScoreDict | LegacyScoreResp":
        if api_version >= 20220705:
            return await ScoreModel.transform(self, includes=includes)
        return await LegacyScoreResp.from_db(session, self)

    async def delete(
        self,
        session: AsyncSession,
        storage_service: StorageService,
    ):
        if await self.awaitable_attrs.best_score:
            assert self.best_score is not None
            await self.best_score.delete(session)
            await session.refresh(self)
        if await self.awaitable_attrs.ranked_score:
            assert self.ranked_score is not None
            await self.ranked_score.delete(session)
            await session.refresh(self)
        if await self.awaitable_attrs.playlist_item_score:
            await session.delete(self.playlist_item_score)

        await storage_service.delete_file(self.replay_filename)
        await session.delete(self)


MultiplayScoreDict = ScoreModel.generate_typeddict(tuple(Score.MULTIPLAYER_BASE_INCLUDES))  # pyright: ignore[reportGeneralTypeIssues]


class LegacyStatistics(BaseModel):
    count_300: int
    count_100: int
    count_50: int
    count_miss: int
    count_geki: int | None = None
    count_katu: int | None = None


class LegacyScoreResp(UTCBaseModel):
    id: int
    best_id: int
    user_id: int
    accuracy: float
    mods: list[str]  # acronym
    score: int
    max_combo: int
    perfect: bool = False
    statistics: LegacyStatistics
    passed: bool
    pp: float
    rank: Rank
    created_at: datetime
    mode: GameMode
    mode_int: int
    replay: bool

    @classmethod
    async def from_db(cls, session: AsyncSession, score: "Score") -> "LegacyScoreResp":
        await score.awaitable_attrs.beatmap
        return cls(
            accuracy=score.accuracy,
            best_id=await get_best_id(session, score.id) or 0,
            created_at=score.started_at,
            id=score.id,
            max_combo=score.max_combo,
            mode=score.gamemode,
            mode_int=int(score.gamemode),
            mods=[m["acronym"] for m in score.mods],
            passed=score.passed,
            pp=score.pp,
            rank=score.rank,
            replay=score.has_replay,
            score=score.total_score,
            statistics=LegacyStatistics(
                count_300=score.n300,
                count_100=score.n100,
                count_50=score.n50,
                count_miss=score.nmiss,
                count_geki=score.ngeki or 0,
                count_katu=score.nkatu or 0,
            ),
            user_id=score.user_id,
            perfect=score.is_perfect_combo,
        )


class MultiplayerScores(RespWithCursor):
    scores: list[MultiplayScoreDict] = Field(default_factory=list)  # pyright: ignore[reportInvalidTypeForm]
    params: dict[str, Any] = Field(default_factory=dict)


class ScoreAround(SQLModel):
    higher: MultiplayerScores | None = None
    lower: MultiplayerScores | None = None


async def get_best_id(session: AsyncSession, score_id: int) -> int | None:
    rownum = (
        func.row_number()
        .over(partition_by=(col(BestScore.user_id), col(BestScore.gamemode)), order_by=col(BestScore.pp).desc())
        .label("rn")
    )
    subq = select(BestScore, rownum).subquery()
    stmt = select(subq.c.rn).where(subq.c.score_id == score_id)
    result = await session.exec(stmt)
    return result.one_or_none()


async def _score_where(
    type: LeaderboardType,
    beatmap: int,
    mode: GameMode,
    mods: list[str] | None = None,
    user: User | None = None,
) -> list[ColumnElement[bool] | TextClause] | None:
    wheres: list[ColumnElement[bool] | TextClause] = [
        col(TotalScoreBestScore.beatmap_id) == beatmap,
        col(TotalScoreBestScore.gamemode) == mode,
        ~User.is_restricted_query(col(TotalScoreBestScore.user_id)),
    ]

    if type == LeaderboardType.FRIENDS:
        if user and user.is_supporter:
            subq = (
                select(DBRelationship.target_id)
                .where(
                    DBRelationship.type == RelationshipType.FOLLOW,
                    DBRelationship.user_id == user.id,
                )
                .subquery()
            )
            wheres.append(col(TotalScoreBestScore.user_id).in_(select(subq.c.target_id)))
        else:
            return None
    elif type == LeaderboardType.COUNTRY:
        if user and user.is_supporter:
            wheres.append(col(TotalScoreBestScore.user).has(col(User.country_code) == user.country_code))
        else:
            return None
    elif type == LeaderboardType.TEAM and user:
        team_membership = await user.awaitable_attrs.team_membership
        if team_membership:
            team_id = team_membership.team_id
            wheres.append(
                col(TotalScoreBestScore.user).has(col(User.team_membership).has(TeamMember.team_id == team_id))
            )
    if mods:
        if user and user.is_supporter:
            wheres.append(
                text(
                    "JSON_CONTAINS(total_score_best_scores.mods, :w)"
                    " AND JSON_CONTAINS(:w, total_score_best_scores.mods)"
                ).params(w=json.dumps(mods))
            )
        else:
            return None
    return wheres


async def get_leaderboard(
    session: AsyncSession,
    beatmap: int,
    mode: GameMode,
    type: LeaderboardType = LeaderboardType.GLOBAL,
    mods: list[str] | None = None,
    user: User | None = None,
    limit: int = 50,
) -> tuple[list[Score], Score | None, int]:
    mods = mods or []
    mode = mode.to_special_mode(mods)

    wheres = await _score_where(type, beatmap, mode, mods, user)
    if wheres is None:
        return [], None, 0
    count = (await session.exec(select(func.count()).where(*wheres))).one()
    scores: dict[int, Score] = {}
    max_score = sys.maxsize
    while limit > 0:
        query = (
            select(TotalScoreBestScore)
            .where(*wheres, TotalScoreBestScore.total_score < max_score)
            .limit(limit)
            .order_by(col(TotalScoreBestScore.total_score).desc())
        )
        extra_need = 0
        for s in await session.exec(query):
            if s.user_id in scores:
                extra_need += 1
                count -= 1
                if s.total_score > scores[s.user_id].total_score:
                    scores[s.user_id] = s.score
            else:
                scores[s.user_id] = s.score
            if max_score > s.total_score:
                max_score = s.total_score
        limit = extra_need

    result_scores = sorted(scores.values(), key=lambda u: u.total_score, reverse=True)
    user_score = None
    if user:
        self_query = (
            select(TotalScoreBestScore)
            .where(TotalScoreBestScore.user_id == user.id)
            .where(
                col(TotalScoreBestScore.beatmap_id) == beatmap,
                col(TotalScoreBestScore.gamemode) == mode,
            )
            .order_by(col(TotalScoreBestScore.total_score).desc())
            .limit(1)
        )
        if mods:
            self_query = self_query.where(
                text(
                    "JSON_CONTAINS(total_score_best_scores.mods, :w)"
                    " AND JSON_CONTAINS(:w, total_score_best_scores.mods)"
                )
            ).params(w=json.dumps(mods))
        user_bs = (await session.exec(self_query)).first()
        if user_bs:
            user_score = user_bs.score
        if user_score and user_score not in result_scores:
            result_scores.append(user_score)
    return result_scores, user_score, count


async def get_score_position_by_user(
    session: AsyncSession,
    beatmap: int,
    user: User,
    mode: GameMode,
    type: LeaderboardType = LeaderboardType.GLOBAL,
    mods: list[str] | None = None,
) -> int:
    wheres = await _score_where(type, beatmap, mode, mods, user=user)
    if wheres is None:
        return 0
    rownum = (
        func.row_number()
        .over(
            partition_by=(
                col(TotalScoreBestScore.beatmap_id),
                col(TotalScoreBestScore.gamemode),
            ),
            order_by=col(TotalScoreBestScore.total_score).desc(),
        )
        .label("row_number")
    )
    subq = select(TotalScoreBestScore, rownum).join(Beatmap).where(*wheres).subquery()
    stmt = select(subq.c.row_number).where(subq.c.user_id == user.id)
    result = await session.exec(stmt)
    s = result.first()
    return s if s else 0


async def get_score_position_by_id(
    session: AsyncSession,
    beatmap: int,
    score_id: int,
    mode: GameMode,
    user: User | None = None,
    type: LeaderboardType = LeaderboardType.GLOBAL,
    mods: list[str] | None = None,
) -> int:
    wheres = await _score_where(type, beatmap, mode, mods, user=user)
    if wheres is None:
        return 0
    rownum = (
        func.row_number()
        .over(
            partition_by=(
                col(TotalScoreBestScore.beatmap_id),
                col(TotalScoreBestScore.gamemode),
            ),
            order_by=col(TotalScoreBestScore.total_score).desc(),
        )
        .label("row_number")
    )
    subq = select(TotalScoreBestScore, rownum).join(Beatmap).where(*wheres).subquery()
    stmt = select(subq.c.row_number).where(subq.c.score_id == score_id)
    result = await session.exec(stmt)
    s = result.one_or_none()
    return s if s else 0


async def get_user_best_score_in_beatmap(
    session: AsyncSession,
    beatmap: int,
    user: int,
    mode: GameMode | None = None,
) -> TotalScoreBestScore | None:
    return (
        await session.exec(
            select(TotalScoreBestScore)
            .where(
                TotalScoreBestScore.gamemode == mode if mode is not None else true(),
                TotalScoreBestScore.beatmap_id == beatmap,
                TotalScoreBestScore.user_id == user,
            )
            .order_by(col(TotalScoreBestScore.total_score).desc())
        )
    ).first()


async def get_user_best_score_with_mod_in_beatmap(
    session: AsyncSession,
    beatmap: int,
    user: int,
    mod: list[str],
    mode: GameMode | None = None,
) -> TotalScoreBestScore | None:
    return (
        await session.exec(
            select(TotalScoreBestScore)
            .where(
                TotalScoreBestScore.gamemode == mode if mode is not None else True,
                TotalScoreBestScore.beatmap_id == beatmap,
                TotalScoreBestScore.user_id == user,
                text(
                    "JSON_CONTAINS(total_score_best_scores.mods, :w)"
                    " AND JSON_CONTAINS(:w, total_score_best_scores.mods)"
                ).params(w=json.dumps(mod)),
            )
            .order_by(col(TotalScoreBestScore.total_score).desc())
        )
    ).first()


async def get_user_first_scores(
    session: AsyncSession, user_id: int, mode: GameMode, limit: int = 5, offset: int = 0
) -> list[TotalScoreBestScore]:
    rownum = (
        func.row_number()
        .over(
            partition_by=(col(TotalScoreBestScore.beatmap_id), col(TotalScoreBestScore.gamemode)),
            order_by=col(TotalScoreBestScore.total_score).desc(),
        )
        .label("rn")
    )

    # Step 1: Fetch top score_ids in Python
    subq = (
        select(
            col(TotalScoreBestScore.score_id).label("score_id"),
            col(TotalScoreBestScore.user_id).label("user_id"),
            rownum,
        )
        .where(col(TotalScoreBestScore.gamemode) == mode)
        .subquery()
    )

    top_ids_stmt = select(subq.c.score_id).where(subq.c.rn == 1, subq.c.user_id == user_id).limit(limit).offset(offset)

    top_ids = await session.exec(top_ids_stmt)
    top_ids = list(top_ids)

    stmt = (
        select(TotalScoreBestScore)
        .where(col(TotalScoreBestScore.score_id).in_(top_ids))
        .order_by(col(TotalScoreBestScore.total_score).desc())
    )

    result = await session.exec(stmt)
    return list(result.all())


async def get_user_first_score_count(session: AsyncSession, user_id: int, mode: GameMode) -> int:
    rownum = (
        func.row_number()
        .over(
            partition_by=(col(TotalScoreBestScore.beatmap_id), col(TotalScoreBestScore.gamemode)),
            order_by=col(TotalScoreBestScore.total_score).desc(),
        )
        .label("rn")
    )
    subq = (
        select(
            col(TotalScoreBestScore.score_id).label("score_id"),
            col(TotalScoreBestScore.user_id).label("user_id"),
            rownum,
        )
        .where(col(TotalScoreBestScore.gamemode) == mode)
        .subquery()
    )
    count_stmt = select(func.count()).where(subq.c.rn == 1, subq.c.user_id == user_id)

    result = await session.exec(count_stmt)
    return result.one()


async def get_user_best_pp_in_beatmap(
    session: AsyncSession,
    beatmap: int,
    user: int,
    mode: GameMode,
) -> BestScore | None:
    return (
        await session.exec(
            select(BestScore).where(
                BestScore.beatmap_id == beatmap,
                BestScore.user_id == user,
                BestScore.gamemode == mode,
            )
        )
    ).first()


async def calculate_user_pp(session: AsyncSession, user_id: int, mode: GameMode) -> tuple[float, float]:
    pp_sum = 0
    acc_sum = 0
    bps = await get_user_best_pp(session, user_id, mode)
    for i, s in enumerate(bps):
        pp_sum += calculate_weighted_pp(s.pp, i)
        acc_sum += calculate_weighted_acc(s.acc, i)
    if len(bps):
        # https://github.com/ppy/osu-queue-score-statistics/blob/c538ae/osu.Server.Queues.ScoreStatisticsProcessor/Helpers/UserTotalPerformanceAggregateHelper.cs#L41-L45
        acc_sum *= 100 / (20 * (1 - math.pow(0.95, len(bps))))
    acc_sum = clamp(acc_sum, 0.0, 100.0)
    return pp_sum, acc_sum


async def get_user_best_pp(
    session: AsyncSession,
    user: int,
    mode: GameMode,
    limit: int = 1000,
) -> Sequence[BestScore]:
    return (
        await session.exec(
            select(BestScore)
            .where(BestScore.user_id == user, BestScore.gamemode == mode)
            .order_by(col(BestScore.pp).desc())
            .limit(limit)
        )
    ).all()


# https://github.com/ppy/osu-queue-score-statistics/blob/master/osu.Server.Queues.ScoreStatisticsProcessor/Helpers/PlayValidityHelper.cs
def get_play_length(score: "Score", beatmap_length: int):
    speed_rate = get_speed_rate(score.mods)
    length = beatmap_length / speed_rate
    return int(min(length, (score.ended_at - score.started_at).total_seconds()))


def calculate_playtime(score: "Score", beatmap_length: int) -> tuple[int, bool]:
    total_length = get_play_length(score, beatmap_length)
    total_obj_hited = (
        score.n300
        + score.n100
        + score.n50
        + score.ngeki
        + score.nkatu
        + (score.nlarge_tick_hit or 0)
        + (score.nlarge_tick_miss or 0)
        + (score.nslider_tail_hit or 0)
        + (score.nsmall_tick_hit or 0)
    )
    total_obj = 0
    for statistics, count in score.maximum_statistics.items() if score.maximum_statistics else {}:
        if not isinstance(statistics, HitResult):
            statistics = HitResult(statistics)
        if statistics.is_scorable():
            total_obj += count

    return total_length, score.passed or (
        total_length > 8 and score.total_score >= 5000 and total_obj_hited >= min(0.1 * total_obj, 20)
    )


async def process_score(
    user: User,
    beatmap_id: int,
    ranked: bool,
    score_token: ScoreToken,
    info: SoloScoreSubmissionInfo,
    session: AsyncSession,
    item_id: int | None = None,
    room_id: int | None = None,
) -> Score:
    gamemode = GameMode.from_int(info.ruleset_id).to_special_mode(info.mods)
    logger.info(
        "Creating score for user {user_id} | beatmap={beatmap_id} ruleset={ruleset} passed={passed} total={total}",
        user_id=user.id,
        beatmap_id=beatmap_id,
        ruleset=gamemode,
        passed=info.passed,
        total=info.total_score,
    )
    score = Score(
        accuracy=info.accuracy,
        max_combo=info.max_combo,
        mods=info.mods,
        passed=info.passed,
        rank=info.rank,
        total_score=info.total_score,
        total_score_without_mods=info.total_score_without_mods,
        beatmap_id=beatmap_id,
        ended_at=utcnow(),
        gamemode=gamemode,
        started_at=score_token.created_at,
        user_id=user.id,
        preserve=info.passed,
        map_md5=score_token.beatmap.checksum,
        has_replay=False,
        type="solo",
        n300=info.statistics.get(HitResult.GREAT, 0),
        n100=info.statistics.get(HitResult.OK, 0),
        n50=info.statistics.get(HitResult.MEH, 0),
        nmiss=info.statistics.get(HitResult.MISS, 0),
        ngeki=info.statistics.get(HitResult.PERFECT, 0),
        nkatu=info.statistics.get(HitResult.GOOD, 0),
        nlarge_tick_miss=info.statistics.get(HitResult.LARGE_TICK_MISS, 0),
        nsmall_tick_hit=info.statistics.get(HitResult.SMALL_TICK_HIT, 0),
        nlarge_tick_hit=info.statistics.get(HitResult.LARGE_TICK_HIT, 0),
        nslider_tail_hit=info.statistics.get(HitResult.SLIDER_TAIL_HIT, 0),
        playlist_item_id=item_id,
        room_id=room_id,
        maximum_statistics=info.maximum_statistics,
        processed=True,
        ranked=ranked,
    )
    session.add(score)
    logger.debug(
        "Score staged for commit | token={token} mods={mods} total_hits={hits}",
        token=score_token.id,
        mods=info.mods,
        hits=sum(info.statistics.values()) if info.statistics else 0,
    )
    await session.commit()
    await session.refresh(score)
    return score


async def _process_score_pp(score: "Score", session: AsyncSession, redis: Redis, fetcher: "Fetcher"):
    if score.pp != 0:
        logger.debug(
            "Skipping PP calculation for score {score_id} | already set {pp:.2f}",
            score_id=score.id,
            pp=score.pp,
        )
        return
    can_get_pp = score.passed and score.ranked and mods_can_get_pp(int(score.gamemode), score.mods)
    if not can_get_pp:
        logger.debug(
            "Skipping PP calculation for score {score_id} | passed={passed} ranked={ranked} mods={mods}",
            score_id=score.id,
            passed=score.passed,
            ranked=score.ranked,
            mods=score.mods,
        )
        return
    pp, successed = await pre_fetch_and_calculate_pp(score, session, redis, fetcher)
    if not successed:
        await redis.rpush("score:need_recalculate", score.id)  # pyright: ignore[reportGeneralTypeIssues]
        logger.warning("Queued score {score_id} for PP recalculation", score_id=score.id)
        return
    score.pp = pp
    logger.info("Calculated PP for score {score_id} | pp={pp:.2f}", score_id=score.id, pp=pp)
    user_id = score.user_id
    beatmap_id = score.beatmap_id
    previous_pp_best = await get_user_best_pp_in_beatmap(session, beatmap_id, user_id, score.gamemode)
    if previous_pp_best is None or score.pp > previous_pp_best.pp:
        best_score = BestScore(
            user_id=user_id,
            score_id=score.id,
            beatmap_id=beatmap_id,
            gamemode=score.gamemode,
            pp=score.pp,
            acc=score.accuracy,
        )
        session.add(best_score)
        await session.delete(previous_pp_best) if previous_pp_best else None
        logger.info(
            "Updated PP best for user {user_id} | score_id={score_id} pp={pp:.2f}",
            user_id=user_id,
            score_id=score.id,
            pp=score.pp,
        )


async def _process_score_events(score: "Score", session: AsyncSession):
    total_users = (await session.exec(select(func.count()).select_from(User))).one()
    rank_global = await get_score_position_by_id(
        session,
        score.beatmap_id,
        score.id,
        mode=score.gamemode,
        user=score.user,
    )

    if rank_global == 0 or total_users == 0:
        logger.debug(
            "Skipping event creation for score {score_id} | rank_global={rank_global} total_users={total_users}",
            score_id=score.id,
            rank_global=rank_global,
            total_users=total_users,
        )
        return
    logger.debug(
        "Processing events for score {score_id} | rank_global={rank_global} total_users={total_users}",
        score_id=score.id,
        rank_global=rank_global,
        total_users=total_users,
    )
    if rank_global <= min(math.ceil(float(total_users) * 0.01), 50):
        rank_event = Event(
            created_at=utcnow(),
            type=EventType.RANK,
            user_id=score.user_id,
            user=score.user,
        )
        rank_event.event_payload = {
            "scorerank": score.rank.value,
            "rank": rank_global,
            "mode": score.gamemode.readable(),
            "beatmap": {
                "title": (
                    f"{score.beatmap.beatmapset.artist} - {score.beatmap.beatmapset.title} [{score.beatmap.version}]"
                ),
                "url": score.beatmap.url.replace("https://osu.ppy.sh/", settings.web_url),
            },
            "user": {
                "username": score.user.username,
                "url": settings.web_url + "users/" + str(score.user.id),
            },
        }
        session.add(rank_event)
        logger.info(
            "Registered rank event for user {user_id} | score_id={score_id} rank={rank}",
            user_id=score.user_id,
            score_id=score.id,
            rank=rank_global,
        )
    if rank_global == 1:
        displaced_score = (
            await session.exec(
                select(TotalScoreBestScore)
                .where(
                    TotalScoreBestScore.beatmap_id == score.beatmap_id,
                    TotalScoreBestScore.gamemode == score.gamemode,
                )
                .order_by(col(TotalScoreBestScore.total_score).desc())
                .limit(1)
                .offset(1)
            )
        ).first()
        if displaced_score and displaced_score.user_id != score.user_id:
            username = (await session.exec(select(User.username).where(User.id == displaced_score.user_id))).one()

            rank_lost_event = Event(
                created_at=utcnow(),
                type=EventType.RANK_LOST,
                user_id=displaced_score.user_id,
            )
            rank_lost_event.event_payload = {
                "mode": score.gamemode.readable(),
                "beatmap": {
                    "title": (
                        f"{score.beatmap.beatmapset.artist} - {score.beatmap.beatmapset.title} "
                        f"[{score.beatmap.version}]"
                    ),
                    "url": score.beatmap.url.replace("https://osu.ppy.sh/", settings.web_url),
                },
                "user": {
                    "username": username,
                    "url": settings.web_url + "users/" + str(displaced_score.user.id),
                },
            }
            session.add(rank_lost_event)
            logger.info(
                "Registered rank lost event | displaced_user={user_id} new_score_id={score_id}",
                user_id=displaced_score.user_id,
                score_id=score.id,
            )
    logger.debug(
        "Event processing committed for score {score_id}",
        score_id=score.id,
    )


async def _process_statistics(
    session: AsyncSession,
    redis: Redis,
    user: User,
    score: "Score",
    score_token: int,
    beatmap_length: int,
    beatmap_status: BeatmapRankStatus,
):
    has_pp = beatmap_status.has_pp() or settings.enable_all_beatmap_pp
    ranked = beatmap_status.ranked() or settings.enable_all_beatmap_pp
    has_leaderboard = beatmap_status.has_leaderboard() or settings.enable_all_beatmap_leaderboard

    mod_for_save = mod_to_save(score.mods)
    previous_score_best = await get_user_best_score_in_beatmap(session, score.beatmap_id, user.id, score.gamemode)
    previous_score_best_mod = await get_user_best_score_with_mod_in_beatmap(
        session, score.beatmap_id, user.id, mod_for_save, score.gamemode
    )
    logger.debug(
        "Existing best scores for user {user_id} | global={global_id} mod={mod_id}",
        user_id=user.id,
        global_id=previous_score_best.score_id if previous_score_best else None,
        mod_id=previous_score_best_mod.score_id if previous_score_best_mod else None,
    )
    add_to_db = False
    mouthly_playcount = (
        await session.exec(
            select(MonthlyPlaycounts).where(
                MonthlyPlaycounts.user_id == user.id,
                MonthlyPlaycounts.year == date.today().year,
                MonthlyPlaycounts.month == date.today().month,
            )
        )
    ).first()
    if mouthly_playcount is None:
        mouthly_playcount = MonthlyPlaycounts(user_id=user.id, year=date.today().year, month=date.today().month)
        add_to_db = True
    statistics = None
    for i in await user.awaitable_attrs.statistics:
        if i.mode == score.gamemode.value:
            statistics = i
            break
    if statistics is None:
        raise ValueError(f"User {user.id} does not have statistics for mode {score.gamemode.value}")

    # pc, pt, tth, tts
    # Get display scores based on configured scoring mode
    current_display_score = score.get_display_score()
    previous_display_score = previous_score_best.score.get_display_score() if previous_score_best else 0

    statistics.total_score += current_display_score
    difference = current_display_score - previous_display_score
    logger.debug(
        "Score delta computed for {score_id}: {difference} (display score in {mode} mode)",
        score_id=score.id,
        difference=difference,
        mode=settings.scoring_mode,
    )
    if difference > 0 and score.passed and ranked:
        match score.rank:
            case Rank.X:
                statistics.grade_ss += 1
            case Rank.XH:
                statistics.grade_ssh += 1
            case Rank.S:
                statistics.grade_s += 1
            case Rank.SH:
                statistics.grade_sh += 1
            case Rank.A:
                statistics.grade_a += 1
        if previous_score_best is not None:
            match previous_score_best.rank:
                case Rank.X:
                    statistics.grade_ss -= 1
                case Rank.XH:
                    statistics.grade_ssh -= 1
                case Rank.S:
                    statistics.grade_s -= 1
                case Rank.SH:
                    statistics.grade_sh -= 1
                case Rank.A:
                    statistics.grade_a -= 1
        statistics.ranked_score += difference
        statistics.level_current = calculate_score_to_level(statistics.total_score)
        statistics.maximum_combo = max(statistics.maximum_combo, score.max_combo)
    if score.passed and has_leaderboard:
        # 情况1: 没有最佳分数记录，直接添加
        # 情况2: 有最佳分数记录但没有该mod组合的记录，添加新记录
        if previous_score_best is None or previous_score_best_mod is None:
            session.add(
                TotalScoreBestScore(
                    user_id=user.id,
                    beatmap_id=score.beatmap_id,
                    gamemode=score.gamemode,
                    score_id=score.id,
                    total_score=score.total_score,
                    rank=score.rank,
                    mods=mod_for_save,
                )
            )
            logger.info(
                "Created new best score entry for user {user_id} | score_id={score_id} mods={mods}",
                user_id=user.id,
                score_id=score.id,
                mods=mod_for_save,
            )

        # 情况3: 有最佳分数记录和该mod组合的记录，且是同一个记录，更新得分更高的情况
        elif previous_score_best.score_id == previous_score_best_mod.score_id and difference > 0:
            previous_score_best.total_score = score.total_score
            previous_score_best.rank = score.rank
            previous_score_best.score_id = score.id
            logger.info(
                "Updated existing best score for user {user_id} | score_id={score_id} total={total}",
                user_id=user.id,
                score_id=score.id,
                total=score.total_score,
            )

        # 情况4: 有最佳分数记录和该mod组合的记录，但不是同一个记录
        elif previous_score_best.score_id != previous_score_best_mod.score_id:
            # 更新全局最佳记录（如果新分数更高）
            if difference > 0:
                # 下方的 if 一定会触发。将高分设置为此分数，删除自己防止重复的 score_id
                logger.info(
                    "Replacing global best score for user {user_id} | old_score_id={old_score_id}",
                    user_id=user.id,
                    old_score_id=previous_score_best.score_id,
                )
                await session.delete(previous_score_best)

            # 更新mod特定最佳记录（如果新分数更高）
            mod_diff = score.total_score - previous_score_best_mod.total_score
            if mod_diff > 0:
                previous_score_best_mod.total_score = score.total_score
                previous_score_best_mod.rank = score.rank
                previous_score_best_mod.score_id = score.id
                logger.info(
                    "Replaced mod-specific best for user {user_id} | mods={mods} score_id={score_id}",
                    user_id=user.id,
                    mods=mod_for_save,
                    score_id=score.id,
                )

    playtime, is_valid = calculate_playtime(score, beatmap_length)
    if is_valid:
        redis = get_redis()
        await redis.xadd(f"score:existed_time:{score_token}", {"time": playtime})
        statistics.play_count += 1
        mouthly_playcount.count += 1
        statistics.play_time += playtime

        await _process_beatmap_playcount(session, score.beatmap_id, user.id)

        logger.debug(
            "Recorded playtime {playtime}s for score {score_id} (user {user_id})",
            playtime=playtime,
            score_id=score.id,
            user_id=user.id,
        )
    else:
        logger.debug(
            "Playtime {playtime}s for score {score_id} did not meet validity checks",
            playtime=playtime,
            score_id=score.id,
        )
    nlarge_tick_miss = score.nlarge_tick_miss or 0
    nsmall_tick_hit = score.nsmall_tick_hit or 0
    nlarge_tick_hit = score.nlarge_tick_hit or 0
    statistics.count_100 += score.n100 + score.nkatu
    statistics.count_300 += score.n300 + score.ngeki
    statistics.count_50 += score.n50
    statistics.count_miss += score.nmiss
    statistics.total_hits += (
        score.n300
        + score.n100
        + score.n50
        + score.ngeki
        + score.nkatu
        + nlarge_tick_hit
        + nlarge_tick_miss
        + nsmall_tick_hit
    )

    if score.gamemode in {GameMode.FRUITS, GameMode.FRUITSRX}:
        statistics.count_miss += nlarge_tick_miss
        statistics.count_50 += nsmall_tick_hit
        statistics.count_100 += nlarge_tick_hit

    if score.passed and has_pp:
        statistics.pp, statistics.hit_accuracy = await calculate_user_pp(session, statistics.user_id, score.gamemode)

    if add_to_db:
        session.add(mouthly_playcount)
        logger.debug(
            "Created monthly playcount record for user {user_id} ({year}-{month})",
            user_id=user.id,
            year=mouthly_playcount.year,
            month=mouthly_playcount.month,
        )


async def _process_beatmap_playcount(session: AsyncSession, beatmap_id: int, user_id: int):
    beatmap_playcount = (
        await session.exec(
            select(BeatmapPlaycounts).where(
                BeatmapPlaycounts.beatmap_id == beatmap_id,
                BeatmapPlaycounts.user_id == user_id,
            )
        )
    ).first()
    if beatmap_playcount is None:
        beatmap_playcount = BeatmapPlaycounts(beatmap_id=beatmap_id, user_id=user_id, playcount=1)
        session.add(beatmap_playcount)
        logger.debug(
            "Created beatmap playcount record for user {user_id} on beatmap {beatmap_id}",
            user_id=user_id,
            beatmap_id=beatmap_id,
        )
    else:
        beatmap_playcount.playcount += 1
        logger.debug(
            "Incremented beatmap playcount for user {user_id} on beatmap {beatmap_id} to {count}",
            user_id=user_id,
            beatmap_id=beatmap_id,
            count=beatmap_playcount.playcount,
        )


async def process_user(
    session: AsyncSession,
    redis: Redis,
    fetcher: "Fetcher",
    user: User,
    score: "Score",
    score_token: int,
    beatmap_length: int,
    beatmap_status: BeatmapRankStatus,
):
    score_id = score.id
    user_id = user.id
    logger.info(
        "Processing score {score_id} for user {user_id} on beatmap {beatmap_id}",
        score_id=score_id,
        user_id=user_id,
        beatmap_id=score.beatmap_id,
    )
    await _process_score_pp(score, session, redis, fetcher)
    await session.commit()
    await session.refresh(score)
    await session.refresh(user)

    await _process_statistics(
        session,
        redis,
        user,
        score,
        score_token,
        beatmap_length,
        beatmap_status,
    )
    await redis.publish("osu-channel:score:processed", f'{{"ScoreId": {score_id}}}')
    await session.commit()

    score_ = (await session.exec(select(Score).where(Score.id == score_id).options(joinedload(Score.beatmap)))).first()
    if score_ is None:
        logger.warning(
            "Score {score_id} disappeared after commit, skipping event processing",
            score_id=score_id,
        )
        return
    await _process_score_events(score_, session)
    await session.commit()
    logger.info(
        "Finished processing score {score_id} for user {user_id}",
        score_id=score_id,
        user_id=user_id,
    )
