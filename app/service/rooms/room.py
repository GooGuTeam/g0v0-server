from __future__ import annotations

from datetime import timedelta

from app.database.beatmap import Beatmap
from app.database.chat import ChannelType, ChatChannel
from app.database.room import APIUploadedRoom, Room
from app.database.room.playlists import Playlist
from app.dependencies.fetcher import get_fetcher
from app.models.room import MatchType, QueueMode, RoomCategory, RoomStatus
from app.utils import utcnow

from sqlalchemy import exists
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession


async def create_playlist_room_from_api(session: AsyncSession, room: APIUploadedRoom, host_id: int) -> Room:
    db_room = room.to_room()
    db_room.host_id = host_id
    db_room.starts_at = utcnow()
    db_room.ends_at = db_room.starts_at + timedelta(minutes=db_room.duration if db_room.duration is not None else 0)
    session.add(db_room)
    await session.commit()
    await session.refresh(db_room)

    channel = ChatChannel(
        name=f"room_{db_room.id}",
        description="Playlist room",
        type=ChannelType.MULTIPLAYER,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    await session.refresh(db_room)
    db_room.channel_id = channel.channel_id

    await add_playlists_to_room(session, db_room.id, room.playlist, host_id)
    await session.refresh(db_room)
    return db_room


async def create_playlist_room(
    session: AsyncSession,
    name: str,
    host_id: int,
    category: RoomCategory = RoomCategory.NORMAL,
    duration: int = 30,
    max_attempts: int | None = None,
    playlist: list[Playlist] = [],
) -> Room:
    db_room = Room(
        name=name,
        category=category,
        duration=duration,
        starts_at=utcnow(),
        ends_at=utcnow() + timedelta(minutes=duration),
        participant_count=0,
        max_attempts=max_attempts,
        type=MatchType.PLAYLISTS,
        queue_mode=QueueMode.HOST_ONLY,
        auto_skip=False,
        auto_start_duration=0,
        status=RoomStatus.IDLE,
        host_id=host_id,
    )
    session.add(db_room)
    await session.commit()
    await session.refresh(db_room)

    channel = ChatChannel(
        name=f"room_{db_room.id}",
        description="Playlist room",
        type=ChannelType.MULTIPLAYER,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    await session.refresh(db_room)
    db_room.channel_id = channel.channel_id

    await add_playlists_to_room(session, db_room.id, playlist, host_id)
    await session.refresh(db_room)
    return db_room


async def add_playlists_to_room(session: AsyncSession, room_id: int, playlist: list[Playlist], owner_id: int):
    for item in playlist:
        if not (await session.exec(select(exists().where(col(Beatmap.id) == item.beatmap)))).first():
            fetcher = await get_fetcher()
            await Beatmap.get_or_fetch(session, fetcher, item.beatmap_id)
        item.id = await Playlist.get_next_id_for_room(room_id, session)
        item.room_id = room_id
        item.owner_id = owner_id
        session.add(item)
    await session.commit()
