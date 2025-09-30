from collections.abc import Sequence
from datetime import date, datetime
import json
import math
import sys
from typing import TYPE_CHECKING, Any

from app.calculator import (
    calculate_pp_weight,
    calculate_score_to_level,
    calculate_weighted_acc,
    calculate_weighted_pp,
    clamp,
    pre_fetch_and_calculate_pp,
)
from app.config import settings
from app.database.team import TeamMember
from app.dependencies.database import get_redis
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
from app.storage import StorageService
from app.utils import utcnow

from .beatmap import Beatmap, BeatmapResp
from .beatmap_playcounts import process_beatmap_playcount
from .beatmapset import BeatmapsetResp
from .best_score import BestScore
from .counts import MonthlyPlaycounts
from .lazer_user import User, UserResp
from .playlist_best_score import PlaylistBestScore
from .pp_best_score import PPBestScore
from .relationship import (
    Relationship as DBRelationship,
    RelationshipType,
)
from .score_token import ScoreToken

from pydantic import BaseModel, field_serializer, field_validator
from redis.asyncio import Redis
from sqlalchemy import Boolean, Column, DateTime, TextClause
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import Mapped
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


class ScoreBase(AsyncAttrs, SQLModel, UTCBaseModel):
    # 基本字段
    accuracy: float
    map_md5: str = Field(max_length=32, index=True)
    build_id: int | None = Field(default=None)
    classic_total_score: int | None = Field(default=0, sa_column=Column(BigInteger))  # solo_score
    ended_at: datetime = Field(sa_column=Column(DateTime))
    has_replay: bool = Field(sa_column=Column(Boolean))
    max_combo: int
    mods: list[APIMod] = Field(sa_column=Column(JSON))
    passed: bool = Field(sa_column=Column(Boolean))
    playlist_item_id: int | None = Field(default=None)  # multiplayer
    pp: float = Field(default=0.0)
    preserve: bool = Field(default=True, sa_column=Column(Boolean))
    rank: Rank
    room_id: int | None = Field(default=None)  # multiplayer
    started_at: datetime = Field(sa_column=Column(DateTime))
    total_score: int = Field(default=0, sa_column=Column(BigInteger))
    total_score_without_mods: int = Field(default=0, sa_column=Column(BigInteger), exclude=True)
    type: str
    beatmap_id: int = Field(index=True, foreign_key="beatmaps.id")
    maximum_statistics: ScoreStatistics = Field(sa_column=Column(JSON), default_factory=dict)
    processed: bool = False  # solo_score
    ranked: bool = False

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


class Score(ScoreBase, table=True):
    __tablename__: str = "scores"
    id: int = Field(default=None, sa_column=Column(BigInteger, autoincrement=True, primary_key=True))
    user_id: int = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("lazer_users.id"),
            index=True,
        ),
    )
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
    best_score: Mapped[BestScore | None] = Relationship(
        back_populates="score",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
        },
    )
    ranked_score: Mapped[PPBestScore | None] = Relationship(
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

    async def to_resp(self, session: AsyncSession, api_version: int) -> "ScoreResp | LegacyScoreResp":
        if api_version >= 20220705:
            return await ScoreResp.from_db(session, self)
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

        await storage_service.delete_file(self.replay_filename)
        await session.delete(self)
        await session.commit()


class ScoreResp(ScoreBase):
    id: int
    user_id: int
    is_perfect_combo: bool = False
    legacy_perfect: bool = False
    legacy_total_score: int = 0  # FIXME
    weight: float = 0.0
    best_id: int | None = None
    ruleset_id: int | None = None
    beatmap: BeatmapResp | None = None
    beatmapset: BeatmapsetResp | None = None
    user: UserResp | None = None
    statistics: ScoreStatistics | None = None
    maximum_statistics: ScoreStatistics | None = None
    rank_global: int | None = None
    rank_country: int | None = None
    position: int | None = None
    scores_around: "ScoreAround | None" = None
    current_user_attributes: CurrentUserAttributes | None = None

    @field_validator(
        "has_replay",
        "passed",
        "preserve",
        "is_perfect_combo",
        "legacy_perfect",
        "processed",
        "ranked",
        mode="before",
    )
    @classmethod
    def validate_bool_fields(cls, v):
        """将整数 0/1 转换为布尔值，处理数据库中的布尔字段"""
        if isinstance(v, int):
            return bool(v)
        return v

    @field_validator("statistics", "maximum_statistics", mode="before")
    @classmethod
    def validate_statistics_fields(cls, v):
        """处理统计字段中的字符串键，转换为 HitResult 枚举"""
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

    @field_serializer("statistics", when_used="json")
    def serialize_statistics_fields(self, v):
        """序列化统计字段，确保枚举值正确转换为字符串"""
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

    @classmethod
    async def from_db(cls, session: AsyncSession, score: Score) -> "ScoreResp":
        # 确保 score 对象完全加载，避免懒加载问题
        await session.refresh(score)

        s = cls.model_validate(score.model_dump())
        await score.awaitable_attrs.beatmap
        s.beatmap = await BeatmapResp.from_db(score.beatmap)
        s.beatmapset = await BeatmapsetResp.from_db(score.beatmap.beatmapset, session=session, user=score.user)
        s.is_perfect_combo = s.max_combo == s.beatmap.max_combo
        s.legacy_perfect = s.max_combo == s.beatmap.max_combo
        s.ruleset_id = int(score.gamemode)
        best_id = await get_best_id(session, score.id)
        if best_id:
            s.best_id = best_id
            s.weight = calculate_pp_weight(best_id - 1)
        s.statistics = {
            HitResult.MISS: score.nmiss,
            HitResult.MEH: score.n50,
            HitResult.OK: score.n100,
            HitResult.GREAT: score.n300,
            HitResult.PERFECT: score.ngeki,
            HitResult.GOOD: score.nkatu,
        }
        if score.nlarge_tick_miss is not None:
            s.statistics[HitResult.LARGE_TICK_MISS] = score.nlarge_tick_miss
        if score.nslider_tail_hit is not None:
            s.statistics[HitResult.SLIDER_TAIL_HIT] = score.nslider_tail_hit
        if score.nsmall_tick_hit is not None:
            s.statistics[HitResult.SMALL_TICK_HIT] = score.nsmall_tick_hit
        if score.nlarge_tick_hit is not None:
            s.statistics[HitResult.LARGE_TICK_HIT] = score.nlarge_tick_hit
        s.user = await UserResp.from_db(
            score.user,
            session,
            include=["statistics", "team", "daily_challenge_user_stats"],
            ruleset=score.gamemode,
        )
        s.rank_global = (
            await get_score_position_by_id(
                session,
                score.beatmap_id,
                score.id,
                mode=score.gamemode,
                user=score.user,
            )
            or None
        )
        s.rank_country = (
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
        s.current_user_attributes = CurrentUserAttributes(
            pin=PinAttributes(is_pinned=bool(score.pinned_order), score_id=score.id)
        )
        return s


class LegacyStatistics(BaseModel):
    count_300: int
    count_100: int
    count_50: int
    count_miss: int
    count_geki: int | None = None
    count_katu: int | None = None


class LegacyScoreResp(UTCBaseModel):
    accuracy: float
    best_id: int
    created_at: datetime
    id: int
    max_combo: int
    mode: GameMode
    mode_int: int
    mods: list[str]  # acronym
    passed: bool
    perfect: bool = False
    pp: float
    rank: Rank
    replay: bool
    score: int
    statistics: LegacyStatistics
    type: str
    user_id: int
    current_user_attributes: CurrentUserAttributes
    user: UserResp
    beatmap: BeatmapResp
    rank_global: int | None = Field(default=None, exclude=True)

    @classmethod
    async def from_db(cls, session: AsyncSession, score: Score) -> "LegacyScoreResp":
        await session.refresh(score)
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
            type=score.type,
            user_id=score.user_id,
            current_user_attributes=CurrentUserAttributes(
                pin=PinAttributes(is_pinned=bool(score.pinned_order), score_id=score.id)
            ),
            user=await UserResp.from_db(
                score.user,
                session,
                include=["statistics", "team", "daily_challenge_user_stats"],
                ruleset=score.gamemode,
            ),
            beatmap=await BeatmapResp.from_db(score.beatmap),
            perfect=score.is_perfect_combo,
            rank_global=(
                await get_score_position_by_id(
                    session,
                    score.beatmap_id,
                    score.id,
                    mode=score.gamemode,
                    user=score.user,
                )
                or None
            ),
        )


class MultiplayerScores(RespWithCursor):
    scores: list[ScoreResp] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class ScoreAround(SQLModel):
    higher: MultiplayerScores | None = None
    lower: MultiplayerScores | None = None


async def get_best_id(session: AsyncSession, score_id: int) -> int | None:
    rownum = (
        func.row_number()
        .over(partition_by=(col(PPBestScore.user_id), col(PPBestScore.gamemode)), order_by=col(PPBestScore.pp).desc())
        .label("rn")
    )
    subq = select(PPBestScore, rownum).subquery()
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
        col(BestScore.beatmap_id) == beatmap,
        col(BestScore.gamemode) == mode,
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
            wheres.append(col(BestScore.user_id).in_(select(subq.c.target_id)))
        else:
            return None
    elif type == LeaderboardType.COUNTRY:
        if user and user.is_supporter:
            wheres.append(col(BestScore.user).has(col(User.country_code) == user.country_code))
        else:
            return None
    elif type == LeaderboardType.TEAM:
        if user:
            team_membership = await user.awaitable_attrs.team_membership
            if team_membership:
                team_id = team_membership.team_id
                wheres.append(col(BestScore.user).has(col(User.team_membership).has(TeamMember.team_id == team_id)))
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
            select(BestScore)
            .where(*wheres, BestScore.total_score < max_score)
            .limit(limit)
            .order_by(col(BestScore.total_score).desc())
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
            select(BestScore)
            .where(BestScore.user_id == user.id)
            .where(
                col(BestScore.beatmap_id) == beatmap,
                col(BestScore.gamemode) == mode,
            )
            .order_by(col(BestScore.total_score).desc())
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
                col(BestScore.beatmap_id),
                col(BestScore.gamemode),
            ),
            order_by=col(BestScore.total_score).desc(),
        )
        .label("row_number")
    )
    subq = select(BestScore, rownum).join(Beatmap).where(*wheres).subquery()
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
                col(BestScore.beatmap_id),
                col(BestScore.gamemode),
            ),
            order_by=col(BestScore.total_score).desc(),
        )
        .label("row_number")
    )
    subq = select(BestScore, rownum).join(Beatmap).where(*wheres).subquery()
    stmt = select(subq.c.row_number).where(subq.c.score_id == score_id)
    result = await session.exec(stmt)
    s = result.one_or_none()
    return s if s else 0


async def get_user_best_score_in_beatmap(
    session: AsyncSession,
    beatmap: int,
    user: int,
    mode: GameMode | None = None,
) -> BestScore | None:
    return (
        await session.exec(
            select(BestScore)
            .where(
                BestScore.gamemode == mode if mode is not None else true(),
                BestScore.beatmap_id == beatmap,
                BestScore.user_id == user,
            )
            .order_by(col(BestScore.total_score).desc())
        )
    ).first()


async def get_user_best_score_with_mod_in_beatmap(
    session: AsyncSession,
    beatmap: int,
    user: int,
    mod: list[str],
    mode: GameMode | None = None,
) -> BestScore | None:
    return (
        await session.exec(
            select(BestScore)
            .where(
                BestScore.gamemode == mode if mode is not None else True,
                BestScore.beatmap_id == beatmap,
                BestScore.user_id == user,
                text(
                    "JSON_CONTAINS(total_score_best_scores.mods, :w)"
                    " AND JSON_CONTAINS(:w, total_score_best_scores.mods)"
                ).params(w=json.dumps(mod)),
            )
            .order_by(col(BestScore.total_score).desc())
        )
    ).first()


async def get_user_first_scores(
    session: AsyncSession, user_id: int, mode: GameMode, limit: int = 5, offset: int = 0
) -> list[BestScore]:
    rownum = (
        func.row_number()
        .over(
            partition_by=(col(BestScore.beatmap_id), col(BestScore.gamemode)),
            order_by=col(BestScore.total_score).desc(),
        )
        .label("rn")
    )

    # Step 1: Fetch top score_ids in Python
    subq = (
        select(
            col(BestScore.score_id).label("score_id"),
            col(BestScore.user_id).label("user_id"),
            rownum,
        )
        .where(col(BestScore.gamemode) == mode)
        .subquery()
    )

    top_ids_stmt = select(subq.c.score_id).where(subq.c.rn == 1, subq.c.user_id == user_id).limit(limit).offset(offset)

    top_ids = await session.exec(top_ids_stmt)
    top_ids = list(top_ids)

    stmt = select(BestScore).where(col(BestScore.score_id).in_(top_ids)).order_by(col(BestScore.total_score).desc())

    result = await session.exec(stmt)
    return list(result.all())


async def get_user_first_score_count(session: AsyncSession, user_id: int, mode: GameMode) -> int:
    rownum = (
        func.row_number()
        .over(
            partition_by=(col(BestScore.beatmap_id), col(BestScore.gamemode)),
            order_by=col(BestScore.total_score).desc(),
        )
        .label("rn")
    )
    subq = (
        select(
            col(BestScore.score_id).label("score_id"),
            col(BestScore.user_id).label("user_id"),
            rownum,
        )
        .where(col(BestScore.gamemode) == mode)
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
) -> PPBestScore | None:
    return (
        await session.exec(
            select(PPBestScore).where(
                PPBestScore.beatmap_id == beatmap,
                PPBestScore.user_id == user,
                PPBestScore.gamemode == mode,
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
) -> Sequence[PPBestScore]:
    return (
        await session.exec(
            select(PPBestScore)
            .where(PPBestScore.user_id == user, PPBestScore.gamemode == mode)
            .order_by(col(PPBestScore.pp).desc())
            .limit(limit)
        )
    ).all()


# https://github.com/ppy/osu-queue-score-statistics/blob/master/osu.Server.Queues.ScoreStatisticsProcessor/Helpers/PlayValidityHelper.cs
def get_play_length(score: Score, beatmap_length: int):
    speed_rate = get_speed_rate(score.mods)
    length = beatmap_length / speed_rate
    return int(min(length, (score.ended_at - score.started_at).total_seconds()))


def calculate_playtime(score: Score, beatmap_length: int) -> tuple[int, bool]:
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


async def process_user(
    session: AsyncSession,
    user: User,
    score: Score,
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
    statistics.total_score += score.total_score
    difference = score.total_score - previous_score_best.total_score if previous_score_best else score.total_score
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
                BestScore(
                    user_id=user.id,
                    beatmap_id=score.beatmap_id,
                    gamemode=score.gamemode,
                    score_id=score.id,
                    total_score=score.total_score,
                    rank=score.rank,
                    mods=mod_for_save,
                )
            )

        # 情况3: 有最佳分数记录和该mod组合的记录，且是同一个记录，更新得分更高的情况
        elif previous_score_best.score_id == previous_score_best_mod.score_id and difference > 0:
            previous_score_best.total_score = score.total_score
            previous_score_best.rank = score.rank
            previous_score_best.score_id = score.id

        # 情况4: 有最佳分数记录和该mod组合的记录，但不是同一个记录
        elif previous_score_best.score_id != previous_score_best_mod.score_id:
            # 更新全局最佳记录（如果新分数更高）
            if difference > 0:
                # 下方的 if 一定会触发。将高分设置为此分数，删除自己防止重复的 score_id
                await session.delete(previous_score_best)

            # 更新mod特定最佳记录（如果新分数更高）
            mod_diff = score.total_score - previous_score_best_mod.total_score
            if mod_diff > 0:
                previous_score_best_mod.total_score = score.total_score
                previous_score_best_mod.rank = score.rank
                previous_score_best_mod.score_id = score.id

    playtime, is_valid = calculate_playtime(score, beatmap_length)
    if is_valid:
        redis = get_redis()
        await redis.xadd(f"score:existed_time:{score_token}", {"time": playtime})
        statistics.play_count += 1
        mouthly_playcount.count += 1
        statistics.play_time += playtime
        with session.no_autoflush:
            await process_beatmap_playcount(session, user.id, score.beatmap_id)

    statistics.count_100 += score.n100 + score.nkatu
    statistics.count_300 += score.n300 + score.ngeki
    statistics.count_50 += score.n50
    statistics.count_miss += score.nmiss
    statistics.total_hits += score.n300 + score.n100 + score.n50 + score.ngeki + score.nkatu

    if score.passed and has_pp:
        with session.no_autoflush:
            statistics.pp, statistics.hit_accuracy = await calculate_user_pp(
                session, statistics.user_id, score.gamemode
            )

    if add_to_db:
        session.add(mouthly_playcount)

    await session.commit()
    await session.refresh(user)


async def process_score(
    user: User,
    beatmap_id: int,
    ranked: bool,
    score_token: ScoreToken,
    info: SoloScoreSubmissionInfo,
    fetcher: "Fetcher",
    session: AsyncSession,
    redis: Redis,
    item_id: int | None = None,
    room_id: int | None = None,
) -> Score:
    can_get_pp = info.passed and ranked and mods_can_get_pp(info.ruleset_id, info.mods)
    gamemode = GameMode.from_int(info.ruleset_id).to_special_mode(info.mods)
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
    successed = True
    if can_get_pp:
        pp, successed = await pre_fetch_and_calculate_pp(score, session, redis, fetcher)
        score.pp = pp
    session.add(score)
    user_id = user.id
    await session.commit()
    await session.refresh(score)
    if not successed:
        await redis.rpush("score:need_recalculate", score.id)  # pyright: ignore[reportGeneralTypeIssues]
    if can_get_pp and score.pp != 0:
        previous_pp_best = await get_user_best_pp_in_beatmap(session, beatmap_id, user_id, score.gamemode)
        if previous_pp_best is None or score.pp > previous_pp_best.pp:
            best_score = PPBestScore(
                user_id=user_id,
                score_id=score.id,
                beatmap_id=beatmap_id,
                gamemode=score.gamemode,
                pp=score.pp,
                acc=score.accuracy,
            )
            session.add(best_score)
            await session.delete(previous_pp_best) if previous_pp_best else None
            await session.commit()
            await session.refresh(score)
    await session.refresh(score_token)
    await session.refresh(user)
    await redis.publish("osu-channel:score:processed", f'{{"ScoreId": {score.id}}}')
    return score
