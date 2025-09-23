from __future__ import annotations

import base64
from datetime import date
from typing import Literal

from app.database import ReplayWatchedCount
from app.database.score import Score
from app.dependencies.database import Database
from app.dependencies.storage import get_storage_service
from app.models.beatmap.mods import int_to_mods
from app.models.score import GameMode
from app.storage import StorageService

from .router import router

from fastapi import Depends, HTTPException, Query
from fastapi_limiter.depends import RateLimiter
from pydantic import BaseModel
from sqlmodel import col, select


class ReplayModel(BaseModel):
    content: str
    encoding: Literal["base64"] = "base64"


@router.get(
    "/get_replay",
    response_model=ReplayModel,
    name="获取回放文件",
    description="获取指定谱面的回放文件。",
    dependencies=[Depends(RateLimiter(times=10, minutes=1))],
)
async def download_replay(
    session: Database,
    beatmap: int = Query(..., alias="b", description="谱面 ID"),
    user: str = Query(..., alias="u", description="用户"),
    ruleset_id: int | None = Query(
        None,
        alias="m",
        description="Ruleset ID",
        ge=0,
    ),
    score_id: int | None = Query(None, alias="s", description="成绩 ID"),
    type: Literal["string", "id"] | None = Query(None, description="用户类型：string 用户名称 / id 用户 ID"),
    mods: int = Query(0, description="成绩的 MOD"),
    storage_service: StorageService = Depends(get_storage_service),
):
    mods_ = int_to_mods(mods)
    if score_id is not None:
        score_record = await session.get(Score, score_id)
        if score_record is None:
            raise HTTPException(status_code=404, detail="Score not found")
    else:
        try:
            score_record = (
                await session.exec(
                    select(Score).where(
                        Score.beatmap_id == beatmap,
                        Score.user_id == user if type == "id" or user.isdigit() else col(Score.user).has(username=user),
                        Score.mods == mods_,
                        Score.gamemode == GameMode.from_int_extra(ruleset_id) if ruleset_id is not None else True,
                    )
                )
            ).first()
            if score_record is None:
                raise HTTPException(status_code=404, detail="Score not found")
        except KeyError:
            raise HTTPException(status_code=400, detail="Invalid request")

    filepath = f"replays/{score_record.id}_{score_record.beatmap_id}_{score_record.user_id}_lazer_replay.osr"
    if not await storage_service.is_exists(filepath):
        raise HTTPException(status_code=404, detail="Replay file not found")

    replay_watched_count = (
        await session.exec(
            select(ReplayWatchedCount).where(
                ReplayWatchedCount.user_id == score_record.user_id,
                ReplayWatchedCount.year == date.today().year,
                ReplayWatchedCount.month == date.today().month,
            )
        )
    ).first()
    if replay_watched_count is None:
        replay_watched_count = ReplayWatchedCount(
            user_id=score_record.user_id,
            year=date.today().year,
            month=date.today().month,
        )
        session.add(replay_watched_count)
    replay_watched_count.count += 1
    await session.commit()

    data = await storage_service.read_file(filepath)
    return ReplayModel(content=base64.b64encode(data).decode("utf-8"), encoding="base64")
