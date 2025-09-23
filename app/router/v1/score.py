from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from app.database import PPBestScore, Score
from app.database.score import get_leaderboard
from app.dependencies.database import Database
from app.models.beatmap.mods import int_to_mods, mod_to_save, mods_to_int
from app.models.score import GameMode, LeaderboardType
from app.utils import utcnow

from .router import AllStrModel, router

from fastapi import HTTPException, Query
from sqlalchemy.orm import joinedload
from sqlmodel import col, exists, select


class V1Score(AllStrModel):
    beatmap_id: int | None = None
    username: str | None = None
    score_id: int
    score: int
    maxcombo: int | None = None
    count50: int
    count100: int
    count300: int
    countmiss: int
    countkatu: int
    countgeki: int
    perfect: bool
    enabled_mods: int
    user_id: int
    date: datetime
    rank: str
    pp: float
    replay_available: bool

    @classmethod
    async def from_db(cls, score: Score):
        return cls(
            beatmap_id=score.beatmap_id,
            username=score.user.username,
            score_id=score.id,
            score=score.total_score,
            maxcombo=score.max_combo,
            count50=score.n50,
            count100=score.n100,
            count300=score.n300,
            countmiss=score.nmiss,
            countkatu=score.nkatu,
            countgeki=score.ngeki,
            perfect=score.is_perfect_combo,
            enabled_mods=mods_to_int(score.mods),
            user_id=score.user_id,
            date=score.ended_at,
            rank=score.rank,
            pp=score.pp,
            replay_available=score.has_replay,
        )


@router.get(
    "/get_user_best",
    response_model=list[V1Score],
    name="获取用户最好成绩",
    description="获取指定用户的最好成绩。",
)
async def get_user_best(
    session: Database,
    user: str = Query(..., alias="u", description="用户"),
    ruleset_id: int = Query(0, alias="m", description="Ruleset ID", ge=0),
    type: Literal["string", "id"] | None = Query(None, description="用户类型：string 用户名称 / id 用户 ID"),
    limit: int = Query(10, ge=1, le=100, description="返回的成绩数量"),
):
    try:
        scores = (
            await session.exec(
                select(Score)
                .where(
                    Score.user_id == user if type == "id" or user.isdigit() else col(Score.user).has(username=user),
                    Score.gamemode == GameMode.from_int_extra(ruleset_id),
                    exists().where(col(PPBestScore.score_id) == Score.id),
                )
                .order_by(col(Score.pp).desc())
                .options(joinedload(Score.beatmap))
                .limit(limit)
            )
        ).all()
        return [await V1Score.from_db(score) for score in scores]
    except KeyError:
        raise HTTPException(400, "Invalid request")


@router.get(
    "/get_user_recent",
    response_model=list[V1Score],
    name="获取用户最近成绩",
    description="获取指定用户的最近成绩。",
)
async def get_user_recent(
    session: Database,
    user: str = Query(..., alias="u", description="用户"),
    ruleset_id: int = Query(0, alias="m", description="Ruleset ID", ge=0),
    type: Literal["string", "id"] | None = Query(None, description="用户类型：string 用户名称 / id 用户 ID"),
    limit: int = Query(10, ge=1, le=100, description="返回的成绩数量"),
):
    try:
        scores = (
            await session.exec(
                select(Score)
                .where(
                    Score.user_id == user if type == "id" or user.isdigit() else col(Score.user).has(username=user),
                    Score.gamemode == GameMode.from_int_extra(ruleset_id),
                    Score.ended_at > utcnow() - timedelta(hours=24),
                )
                .order_by(col(Score.pp).desc())
                .options(joinedload(Score.beatmap))
                .limit(limit)
            )
        ).all()
        return [await V1Score.from_db(score) for score in scores]
    except KeyError:
        raise HTTPException(400, "Invalid request")


@router.get(
    "/get_scores",
    response_model=list[V1Score],
    name="获取成绩",
    description="获取指定谱面的成绩。",
)
async def get_scores(
    session: Database,
    user: str | None = Query(None, alias="u", description="用户"),
    beatmap_id: int = Query(alias="b", description="谱面 ID"),
    ruleset_id: int = Query(0, alias="m", description="Ruleset ID", ge=0),
    type: Literal["string", "id"] | None = Query(None, description="用户类型：string 用户名称 / id 用户 ID"),
    limit: int = Query(10, ge=1, le=100, description="返回的成绩数量"),
    mods: int = Query(0, description="成绩的 MOD"),
):
    try:
        if user is not None:
            scores = (
                await session.exec(
                    select(Score)
                    .where(
                        Score.gamemode == GameMode.from_int_extra(ruleset_id),
                        Score.beatmap_id == beatmap_id,
                        Score.user_id == user if type == "id" or user.isdigit() else col(Score.user).has(username=user),
                    )
                    .options(joinedload(Score.beatmap))
                    .order_by(col(Score.classic_total_score).desc())
                )
            ).all()
        else:
            scores, _, _ = await get_leaderboard(
                session,
                beatmap_id,
                GameMode.from_int_extra(ruleset_id),
                LeaderboardType.GLOBAL,
                mod_to_save(int_to_mods(mods)),
                limit=limit,
            )
        return [await V1Score.from_db(score) for score in scores]
    except KeyError:
        raise HTTPException(400, "Invalid request")
