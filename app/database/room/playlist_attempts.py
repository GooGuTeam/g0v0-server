from app.database.score.playlist_best_score import PlaylistBestScore
from app.database.user.lazer_user import User, UserResp

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncAttrs
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


class ItemAttemptsCountBase(SQLModel):
    room_id: int = Field(foreign_key="rooms.id", index=True)
    attempts: int = Field(default=0)
    completed: int = Field(default=0)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    accuracy: float = 0.0
    pp: float = 0
    total_score: int = 0


class ItemAttemptsCount(AsyncAttrs, ItemAttemptsCountBase, table=True):
    __tablename__: str = "item_attempts_count"
    id: int | None = Field(default=None, primary_key=True)

    user: User = Relationship()

    async def get_position(self, session: AsyncSession) -> int:
        rownum = (
            func.row_number()
            .over(
                partition_by=col(ItemAttemptsCountBase.room_id),
                order_by=col(ItemAttemptsCountBase.total_score).desc(),
            )
            .label("rn")
        )
        subq = select(ItemAttemptsCountBase, rownum).subquery()
        stmt = select(subq.c.rn).where(subq.c.user_id == self.user_id)
        result = await session.exec(stmt)
        return result.one()

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


class ItemAttemptsResp(ItemAttemptsCountBase):
    user: UserResp | None = None
    position: int | None = None

    @classmethod
    async def from_db(
        cls,
        item_attempts: ItemAttemptsCount,
        session: AsyncSession,
        include: list[str] = [],
    ) -> "ItemAttemptsResp":
        resp = cls.model_validate(item_attempts.model_dump())
        resp.user = await UserResp.from_db(
            await item_attempts.awaitable_attrs.user,
            session=session,
            include=["statistics", "team", "daily_challenge_user_stats"],
        )
        if "position" in include:
            resp.position = await item_attempts.get_position(session)
        # resp.accuracy *= 100
        return resp


class ItemAttemptsCountForItem(BaseModel):
    id: int
    attempts: int
    passed: bool


class PlaylistAggregateScore(BaseModel):
    playlist_item_attempts: list[ItemAttemptsCountForItem] = Field(default_factory=list)

    @classmethod
    async def from_db(
        cls,
        room_id: int,
        user_id: int,
        session: AsyncSession,
    ) -> "PlaylistAggregateScore":
        playlist_scores = (
            await session.exec(
                select(PlaylistBestScore).where(
                    PlaylistBestScore.room_id == room_id,
                    PlaylistBestScore.user_id == user_id,
                )
            )
        ).all()
        playlist_item_attempts = []
        for score in playlist_scores:
            playlist_item_attempts.append(
                ItemAttemptsCountForItem(
                    id=score.playlist_id,
                    attempts=score.attempts,
                    passed=score.score.passed,
                )
            )
        return cls(playlist_item_attempts=playlist_item_attempts)
