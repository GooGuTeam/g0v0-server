from typing import TYPE_CHECKING

from app.config import settings
from app.database.room.events import Event, EventType
from app.utils import utcnow

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.database.beatmap import Beatmap, BeatmapResp
    from app.database.beatmap.beatmapset import BeatmapsetResp
    from app.database.user.lazer_user import User


class BeatmapPlaycounts(AsyncAttrs, SQLModel, table=True):
    __tablename__: str = "beatmap_playcounts"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    beatmap_id: int = Field(foreign_key="beatmaps.id", index=True)
    playcount: int = Field(default=0)

    user: "User" = Relationship()
    beatmap: "Beatmap" = Relationship()


class BeatmapPlaycountsResp(BaseModel):
    beatmap_id: int
    beatmap: "BeatmapResp | None" = None
    beatmapset: "BeatmapsetResp | None" = None
    count: int

    @classmethod
    async def from_db(cls, db_model: BeatmapPlaycounts) -> "BeatmapPlaycountsResp":
        from app.database.beatmap import BeatmapResp
        from app.database.beatmap.beatmapset import BeatmapsetResp

        await db_model.awaitable_attrs.beatmap
        return cls(
            beatmap_id=db_model.beatmap_id,
            count=db_model.playcount,
            beatmap=await BeatmapResp.from_db(db_model.beatmap),
            beatmapset=await BeatmapsetResp.from_db(db_model.beatmap.beatmapset),
        )


async def process_beatmap_playcount(session: AsyncSession, user_id: int, beatmap_id: int) -> None:
    existing_playcount = (
        await session.exec(
            select(BeatmapPlaycounts).where(
                BeatmapPlaycounts.user_id == user_id,
                BeatmapPlaycounts.beatmap_id == beatmap_id,
            )
        )
    ).first()

    if existing_playcount:
        existing_playcount.playcount += 1
        if existing_playcount.playcount % 100 == 0:
            playcount_event = Event(
                created_at=utcnow(),
                type=EventType.BEATMAP_PLAYCOUNT,
                user_id=user_id,
            )
            await existing_playcount.awaitable_attrs.beatmap
            playcount_event.event_payload = {
                "count": existing_playcount.playcount,
                "beatmap": {
                    "title": existing_playcount.beatmap.version,
                    "url": existing_playcount.beatmap.url.replace("https://osu.ppy.sh/", settings.web_url),
                },
            }
            session.add(playcount_event)
    else:
        new_playcount = BeatmapPlaycounts(user_id=user_id, beatmap_id=beatmap_id, playcount=1)
        session.add(new_playcount)
