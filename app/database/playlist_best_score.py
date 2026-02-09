from typing import TYPE_CHECKING

from .user import User

from redis.asyncio import Redis
from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    col,
    func,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .score import Score


class PlaylistBestScore(SQLModel, table=True):
    __tablename__: str = "playlist_best_scores"

    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    score_id: int = Field(sa_column=Column(BigInteger, ForeignKey("scores.id"), primary_key=True))
    room_id: int = Field(foreign_key="rooms.id", index=True)
    playlist_id: int = Field(index=True)
    total_score: int = Field(default=0, sa_column=Column(BigInteger))
    attempts: int = Field(default=0)  # playlist

    user: User = Relationship()
    score: "Score" = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[PlaylistBestScore.score_id]",
            "lazy": "joined",
        }
    )


async def process_playlist_best_score(
    room_id: int,
    playlist_id: int,
    user_id: int,
    score_id: int,
    total_score: int,
    session: AsyncSession,
    redis: Redis,
):
    previous = (
        await session.exec(
            select(PlaylistBestScore).where(
                PlaylistBestScore.room_id == room_id,
                PlaylistBestScore.playlist_id == playlist_id,
                PlaylistBestScore.user_id == user_id,
            )
        )
    ).first()
    if previous is None:
        previous = PlaylistBestScore(
            user_id=user_id,
            score_id=score_id,
            room_id=room_id,
            playlist_id=playlist_id,
            total_score=total_score,
        )
        session.add(previous)
    elif not previous.score.passed or previous.total_score < total_score:
        previous.score_id = score_id
        previous.total_score = total_score
    previous.attempts += 1
    await session.commit()
    if await redis.exists(f"multiplayer:{room_id}:gameplay:players"):
        await redis.decr(f"multiplayer:{room_id}:gameplay:players")


async def get_position(
    room_id: int,
    playlist_id: int,
    score_id: int,
    session: AsyncSession,
) -> int:
    rownum = (
        func.row_number()
        .over(
            partition_by=(
                col(PlaylistBestScore.playlist_id),
                col(PlaylistBestScore.room_id),
            ),
            order_by=col(PlaylistBestScore.total_score).desc(),
        )
        .label("row_number")
    )
    subq = (
        select(PlaylistBestScore, rownum)
        .where(
            PlaylistBestScore.playlist_id == playlist_id,
            PlaylistBestScore.room_id == room_id,
        )
        .subquery()
    )
    stmt = select(subq.c.row_number).where(subq.c.score_id == score_id)
    result = await session.exec(stmt)
    s = result.one_or_none()
    return s or 0
