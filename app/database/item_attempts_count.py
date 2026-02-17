"""Multiplayer room attempt count database models.

This module tracks user attempts and scores in multiplayer rooms.
"""

from typing import Any, NotRequired, TypedDict

from ._base import DatabaseModel, ondemand
from .playlist_best_score import PlaylistBestScore
from .user import User, UserDict, UserModel

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Relationship,
    col,
    func,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession


class ItemAttemptsCountDict(TypedDict):
    """TypedDict representation of room attempt counts."""

    accuracy: float
    attempts: int
    completed: int
    pp: float
    room_id: int
    total_score: int
    user_id: int
    user: NotRequired[UserDict]
    position: NotRequired[int]
    playlist_item_attempts: NotRequired[list[dict[str, Any]]]


class ItemAttemptsCountModel(DatabaseModel[ItemAttemptsCountDict]):
    """Base model for room attempt counts with transformation support."""

    accuracy: float = 0.0
    attempts: int = Field(default=0)
    completed: int = Field(default=0)
    pp: float = 0
    room_id: int = Field(foreign_key="rooms.id", index=True)
    total_score: int = 0
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))

    @ondemand
    @staticmethod
    async def user(_session: AsyncSession, item_attempts: "ItemAttemptsCount") -> UserDict:
        user_instance = await item_attempts.awaitable_attrs.user
        return await UserModel.transform(user_instance)

    @ondemand
    @staticmethod
    async def position(session: AsyncSession, item_attempts: "ItemAttemptsCount") -> int:
        return await item_attempts.get_position(session)

    @ondemand
    @staticmethod
    async def playlist_item_attempts(
        session: AsyncSession,
        item_attempts: "ItemAttemptsCount",
    ) -> list[dict[str, Any]]:
        playlist_scores = (
            await session.exec(
                select(PlaylistBestScore).where(
                    PlaylistBestScore.room_id == item_attempts.room_id,
                    PlaylistBestScore.user_id == item_attempts.user_id,
                )
            )
        ).all()
        result: list[dict[str, Any]] = []
        for score in playlist_scores:
            result.append(
                {
                    "id": score.playlist_id,
                    "attempts": score.attempts,
                    "passed": score.score.passed,
                }
            )
        return result


class ItemAttemptsCount(AsyncAttrs, ItemAttemptsCountModel, table=True):
    """Database table for tracking user attempts in multiplayer rooms."""

    __tablename__: str = "item_attempts_count"
    id: int | None = Field(default=None, primary_key=True)

    user: User = Relationship()

    async def get_position(self, session: AsyncSession) -> int:
        rownum = (
            func.row_number()
            .over(
                partition_by=col(ItemAttemptsCount.room_id),
                order_by=col(ItemAttemptsCount.total_score).desc(),
            )
            .label("rn")
        )
        subq = select(ItemAttemptsCount, rownum).subquery()
        stmt = select(subq.c.rn).where(subq.c.user_id == self.user_id)
        result = await session.exec(stmt)
        return result.first() or 0

    async def update(self, session: AsyncSession):
        playlist_scores = (
            await session.exec(
                select(PlaylistBestScore).where(
                    PlaylistBestScore.room_id == self.room_id,
                    PlaylistBestScore.user_id == self.user_id,
                )
            )
        ).all()
        self.attempts = sum(score.attempts for score in playlist_scores)
        self.total_score = sum(score.total_score for score in playlist_scores)
        self.pp = sum(score.score.pp for score in playlist_scores)
        passed_scores = [score for score in playlist_scores if score.score.passed]
        self.completed = len(passed_scores)
        self.accuracy = (
            sum(score.score.accuracy for score in passed_scores) / self.completed if self.completed > 0 else 0.0
        )
        await session.commit()
        await session.refresh(self)

    @classmethod
    async def get_or_create(
        cls,
        room_id: int,
        user_id: int,
        session: AsyncSession,
    ) -> "ItemAttemptsCount":
        item_attempts = await session.exec(
            select(cls).where(
                cls.room_id == room_id,
                cls.user_id == user_id,
            )
        )
        item_attempts = item_attempts.first()
        if item_attempts is None:
            item_attempts = cls(room_id=room_id, user_id=user_id)
            session.add(item_attempts)
            await session.commit()
            await session.refresh(item_attempts)
        await item_attempts.update(session)
        return item_attempts
