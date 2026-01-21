import base64
from datetime import date
from typing import Annotated, Literal

from app.database.counts import ReplayWatchedCount
from app.database.score import Score
from app.dependencies.database import Database
from app.dependencies.storage import StorageService
from app.models.mods import int_to_mods
from app.models.score import GameMode

from .router import router

from app.models.error import ErrorType, RequestError
from fastapi import Depends, Query
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
    beatmap: Annotated[int, Query(..., alias="b", description="谱面 ID")],
    user: Annotated[str, Query(..., alias="u", description="用户")],
    storage_service: StorageService,
    ruleset_id: Annotated[
        int | None,
        Query(
            alias="m",
            description="Ruleset ID",
            ge=0,
        ),
    ] = None,
    score_id: Annotated[int | None, Query(alias="s", description="成绩 ID")] = None,
    type: Annotated[Literal["string", "id"] | None, Query(description="用户类型：string 用户名称 / id 用户 ID")] = None,
    mods: Annotated[int, Query(description="成绩的 MOD")] = 0,
):
    mods_ = int_to_mods(mods)
    if score_id is not None:
        score_record = await session.get(Score, score_id)
        if score_record is None:
            raise RequestError(ErrorType.SCORE_NOT_FOUND)
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
                raise RequestError(ErrorType.SCORE_NOT_FOUND)
        except KeyError:
            raise RequestError(ErrorType.INVALID_REQUEST)

    filepath = score_record.replay_filename
    if not await storage_service.is_exists(filepath):
        raise RequestError(ErrorType.REPLAY_FILE_NOT_FOUND)

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
