"""Room endpoints for osu! API v2.

This module provides endpoints for managing multiplayer rooms (playlist mode),
including room creation, participation, leaderboards, and events.
"""

from datetime import UTC
from typing import Annotated, Literal

from app.database.beatmap import (
    Beatmap,
    BeatmapModel,
)
from app.database.beatmapset import BeatmapsetModel
from app.database.item_attempts_count import ItemAttemptsCount, ItemAttemptsCountModel
from app.database.multiplayer_event import MultiplayerEvent, MultiplayerEventResp
from app.database.playlists import Playlist, PlaylistModel
from app.database.room import APIUploadedRoom, Room, RoomModel
from app.database.room_participated_user import RoomParticipatedUser
from app.database.score import Score
from app.database.user import User, UserModel
from app.dependencies.database import Database, Redis
from app.dependencies.user import ClientUser, get_current_user
from app.helpers import api_doc, utcnow
from app.models.error import ErrorType, RequestError
from app.models.room import MatchType, RoomCategory, RoomStatus
from app.service.room import create_playlist_room_from_api

from .router import router

from fastapi import Path, Query, Security
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession


@router.get(
    "/rooms",
    tags=["Rooms"],
    responses={
        200: api_doc(
            "Room list",
            list[RoomModel],
            [
                "current_playlist_item.beatmap.beatmapset",
                "difficulty_range",
                "host.country",
                "playlist_item_stats",
                "recent_participants",
            ],
        )
    },
    name="Get room list",
    description="Get room list. Supports filtering by status/mode",
)
async def get_all_rooms(
    db: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    mode: Annotated[
        Literal["open", "ended", "participated", "owned"] | None,
        Query(
            description=(
                "Room mode: open (currently active) / ended (finished) / "
                "participated (user joined) / owned (created by user)"
            ),
        ),
    ] = "open",
    category: Annotated[
        RoomCategory,
        Query(
            description=("Room category: NORMAL (playlist mode) / REALTIME (multiplayer) / DAILY_CHALLENGE"),
        ),
    ] = RoomCategory.NORMAL,
    status: Annotated[RoomStatus | None, Query(description="Room status (optional)")] = None,
):
    """Get all rooms matching the specified filters.

    Args:
        db: Database session dependency.
        current_user: The authenticated user.
        mode: Room mode filter (open/ended/participated/owned).
        category: Room category filter.
        status: Optional room status filter.

    Returns:
        list[RoomModel]: List of rooms matching the filters.
    """
    resp_list = []
    where_clauses: list[ColumnElement[bool]] = [col(Room.category) == category, col(Room.type) != MatchType.MATCHMAKING]
    now = utcnow()

    if status is not None:
        where_clauses.append(col(Room.status) == status)
    if mode == "open":
        where_clauses.extend(
            [
                col(Room.status).in_([RoomStatus.IDLE, RoomStatus.PLAYING]),
                col(Room.starts_at).is_not(None),
                col(Room.ends_at).is_(None) if category == RoomCategory.REALTIME else col(Room.ends_at) > now,
            ]
        )

    if mode == "participated":
        where_clauses.append(
            exists().where(
                col(RoomParticipatedUser.room_id) == Room.id,
                col(RoomParticipatedUser.user_id) == current_user.id,
            )
        )

    if mode == "owned":
        where_clauses.append(col(Room.host_id) == current_user.id)

    if mode == "ended":
        where_clauses.append((col(Room.ends_at).is_not(None)) & (col(Room.ends_at) < now.replace(tzinfo=UTC)))

    db_rooms = (
        (
            await db.exec(
                select(Room).where(
                    *where_clauses,
                )
            )
        )
        .unique()
        .all()
    )
    for room in db_rooms:
        resp = await RoomModel.transform(
            room,
            includes=[
                "current_playlist_item.beatmap.beatmapset",
                "difficulty_range",
                "host.country",
                "playlist_item_stats",
                "recent_participants",
            ],
        )
        if category == RoomCategory.REALTIME:
            resp["category"] = RoomCategory.NORMAL

        resp_list.append(resp)

    return resp_list


async def _participate_room(room_id: int, user_id: int, db_room: Room, session: AsyncSession, redis: Redis):
    """Add or update a user's participation in a room.

    Args:
        room_id: The room ID.
        user_id: The user ID.
        db_room: The room database object.
        session: Database session.
        redis: Redis connection for publishing events.
    """
    participated_user = (
        await session.exec(
            select(RoomParticipatedUser).where(
                RoomParticipatedUser.room_id == room_id,
                RoomParticipatedUser.user_id == user_id,
            )
        )
    ).first()
    if participated_user is None:
        participated_user = RoomParticipatedUser(
            room_id=room_id,
            user_id=user_id,
            joined_at=utcnow(),
        )
        session.add(participated_user)
    else:
        participated_user.left_at = None
        participated_user.joined_at = utcnow()
    db_room.participant_count += 1

    await redis.publish("chat:room:joined", f"{db_room.channel_id}:{user_id}")


@router.post(
    "/rooms",
    tags=["Rooms"],
    name="Create room",
    description="\nCreate a new room.",
    responses={
        200: api_doc(
            "Created room information",
            RoomModel,
            Room.SHOW_RESPONSE_INCLUDES,
        )
    },
)
async def create_room(
    db: Database,
    room: APIUploadedRoom,
    current_user: ClientUser,
    redis: Redis,
):
    """Create a new playlist mode room.

    Args:
        db: Database session dependency.
        room: Room creation data.
        current_user: The authenticated client user.
        redis: Redis connection.

    Returns:
        RoomModel: The created room information.

    Raises:
        RequestError: If the user account is restricted.
    """
    if await current_user.is_restricted(db):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)
    user_id = current_user.id
    db_room = await create_playlist_room_from_api(db, room, user_id)
    await _participate_room(db_room.id, user_id, db_room, db, redis)
    await db.commit()
    await db.refresh(db_room)
    created_room = await RoomModel.transform(db_room, includes=Room.SHOW_RESPONSE_INCLUDES)
    return created_room


@router.get(
    "/rooms/{room_id}",
    tags=["Rooms"],
    responses={
        200: api_doc(
            "Room details",
            RoomModel,
            Room.SHOW_RESPONSE_INCLUDES,
        )
    },
    name="Get room details",
    description="Get details for a specific room.",
)
async def get_room(
    db: Database,
    room_id: Annotated[int, Path(..., description="Room ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    category: Annotated[
        str,
        Query(
            description=("Room category: NORMAL (playlist mode) / REALTIME (multiplayer) / DAILY_CHALLENGE (optional)"),
        ),
    ] = "",
):
    """Get room details by ID.

    Args:
        db: Database session dependency.
        room_id: The room ID.
        current_user: The authenticated user.
        category: Optional room category hint.

    Returns:
        RoomModel: Room details.

    Raises:
        RequestError: If the room is not found.
    """
    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is None:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)
    resp = await RoomModel.transform(db_room, includes=Room.SHOW_RESPONSE_INCLUDES, user=current_user)
    return resp


@router.delete(
    "/rooms/{room_id}",
    tags=["Rooms"],
    name="End room",
    description="\nEnd a playlist mode room.",
)
async def delete_room(
    db: Database,
    room_id: Annotated[int, Path(..., description="Room ID")],
    current_user: ClientUser,
):
    """End a playlist mode room.

    Args:
        db: Database session dependency.
        room_id: The room ID.
        current_user: The authenticated client user.

    Returns:
        None

    Raises:
        RequestError: If user is restricted or room not found.
    """
    if await current_user.is_restricted(db):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is None:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)
    else:
        db_room.ends_at = utcnow()
        await db.commit()
        return None


@router.put(
    "/rooms/{room_id}/users/{user_id}",
    tags=["Rooms"],
    name="Join room",
    description="\nJoin a specified playlist mode room.",
)
async def add_user_to_room(
    db: Database,
    room_id: Annotated[int, Path(..., description="Room ID")],
    user_id: Annotated[int, Path(..., description="User ID")],
    redis: Redis,
    current_user: ClientUser,
):
    """Join a playlist mode room.

    Args:
        db: Database session dependency.
        room_id: The room ID.
        user_id: The user ID to add.
        redis: Redis connection.
        current_user: The authenticated client user.

    Returns:
        RoomModel: Updated room information.

    Raises:
        RequestError: If user is restricted or room not found.
    """
    if await current_user.is_restricted(db):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is not None:
        await _participate_room(room_id, user_id, db_room, db, redis)
        await db.commit()
        await db.refresh(db_room)
        resp = await RoomModel.transform(db_room, includes=Room.SHOW_RESPONSE_INCLUDES)
        return resp
    else:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)


@router.delete(
    "/rooms/{room_id}/users/{user_id}",
    tags=["Rooms"],
    name="Leave room",
    description="\nLeave a specified playlist mode room.",
)
async def remove_user_from_room(
    db: Database,
    room_id: Annotated[int, Path(..., description="Room ID")],
    user_id: Annotated[int, Path(..., description="User ID")],
    current_user: ClientUser,
    redis: Redis,
):
    """Leave a playlist mode room.

    Args:
        db: Database session dependency.
        room_id: The room ID.
        user_id: The user ID to remove.
        current_user: The authenticated client user.
        redis: Redis connection.

    Returns:
        None

    Raises:
        RequestError: If user is restricted or room not found.
    """
    if await current_user.is_restricted(db):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is not None:
        participated_user = (
            await db.exec(
                select(RoomParticipatedUser).where(
                    RoomParticipatedUser.room_id == room_id,
                    RoomParticipatedUser.user_id == user_id,
                )
            )
        ).first()
        if participated_user is not None:
            participated_user.left_at = utcnow()
        if db_room.participant_count > 0:
            db_room.participant_count -= 1
        await redis.publish("chat:room:left", f"{db_room.channel_id}:{user_id}")
        await db.commit()
        return None
    else:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)


@router.get(
    "/rooms/{room_id}/leaderboard",
    tags=["Rooms"],
    name="Get room leaderboard",
    description="Get cumulative score leaderboard for a room.",
    responses={
        200: api_doc(
            "Room leaderboard",
            {
                "leaderboard": list[ItemAttemptsCountModel],
                "user_score": ItemAttemptsCountModel | None,
            },
            ["user.country", "position"],
            name="RoomLeaderboardResponse",
        )
    },
)
async def get_room_leaderboard(
    db: Database,
    room_id: Annotated[int, Path(..., description="Room ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
):
    """Get the leaderboard for a room.

    Args:
        db: Database session dependency.
        room_id: The room ID.
        current_user: The authenticated user.

    Returns:
        dict: Leaderboard data with user scores.

    Raises:
        RequestError: If the room is not found.
    """
    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is None:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)
    aggs = await db.exec(
        select(ItemAttemptsCount)
        .where(ItemAttemptsCount.room_id == room_id)
        .order_by(col(ItemAttemptsCount.total_score).desc())
    )
    aggs_resp = []
    user_agg = None
    for i, agg in enumerate(aggs):
        includes = ["user.country"]
        if agg.user_id == current_user.id:
            includes.append("position")
        resp = await ItemAttemptsCountModel.transform(agg, includes=includes)
        aggs_resp.append(resp)
        if agg.user_id == current_user.id:
            user_agg = resp

    return {
        "leaderboard": aggs_resp,
        "user_score": user_agg,
    }


@router.get(
    "/rooms/{room_id}/events",
    tags=["Rooms"],
    name="Get room events",
    description="Get room event list (descending order, supports after/before range filtering).",
    responses={
        200: api_doc(
            "Room events",
            {
                "beatmaps": list[BeatmapModel],
                "beatmapsets": list[BeatmapsetModel],
                "current_playlist_item_id": int,
                "events": list[MultiplayerEventResp],
                "first_event_id": int,
                "last_event_id": int,
                "playlist_items": list[PlaylistModel],
                "room": RoomModel,
                "user": list[UserModel],
            },
            ["country", "details", "scores"],
            name="RoomEventsResponse",
        )
    },
)
async def get_room_events(
    db: Database,
    room_id: Annotated[int, Path(..., description="Room ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    limit: Annotated[int, Query(ge=1, le=1000, description="Number of results to return (1-1000)")] = 100,
    after: Annotated[int | None, Query(ge=0, description="Only include events with ID greater than this")] = None,
    before: Annotated[int | None, Query(ge=0, description="Only include events with ID less than this")] = None,
):
    """Get events for a room.

    Args:
        db: Database session dependency.
        room_id: The room ID.
        current_user: The authenticated user.
        limit: Maximum number of events to return.
        after: Only include events after this event ID.
        before: Only include events before this event ID.

    Returns:
        dict: Room events with related beatmaps, beatmapsets, users, and playlist items.

    Raises:
        RequestError: If the room is not found.
    """
    events = (
        await db.exec(
            select(MultiplayerEvent)
            .where(
                MultiplayerEvent.room_id == room_id,
                col(MultiplayerEvent.id) > after if after is not None else True,
                col(MultiplayerEvent.id) < before if before is not None else True,
            )
            .order_by(col(MultiplayerEvent.id).desc())
            .limit(limit)
        )
    ).all()

    user_ids = set()
    playlist_items = {}
    beatmap_ids = set()

    event_resps = []
    first_event_id = 0
    last_event_id = 0

    current_playlist_item_id = 0
    for event in events:
        event_resps.append(MultiplayerEventResp.from_db(event))
        if event.user_id:
            user_ids.add(event.user_id)
        if event.playlist_item_id is not None and (
            playitem := (
                await db.exec(
                    select(Playlist).where(
                        Playlist.id == event.playlist_item_id,
                        Playlist.room_id == room_id,
                    )
                )
            ).first()
        ):
            current_playlist_item_id = playitem.id
            playlist_items[event.playlist_item_id] = playitem
            beatmap_ids.add(playitem.beatmap_id)
            scores = await db.exec(
                select(Score).where(
                    Score.playlist_item_id == event.playlist_item_id,
                    Score.room_id == room_id,
                )
            )
            for score in scores:
                user_ids.add(score.user_id)
                beatmap_ids.add(score.beatmap_id)
        first_event_id = min(first_event_id, event.id)
        last_event_id = max(last_event_id, event.id)

    room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if room is None:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)
    room_resp = await RoomModel.transform(room, includes=["current_playlist_item"])
    if room.category == RoomCategory.REALTIME:
        current_playlist_item_id = (await Room.current_playlist_item(db, room))["id"]

    users = await db.exec(select(User).where(col(User.id).in_(user_ids)))
    user_resps = [await UserModel.transform(user, includes=["country"]) for user in users]

    beatmaps = await db.exec(select(Beatmap).where(col(Beatmap.id).in_(beatmap_ids)))
    beatmap_resps = [
        await BeatmapModel.transform(
            beatmap,
        )
        for beatmap in beatmaps
    ]

    beatmapsets = []
    for beatmap in beatmaps:
        if beatmap.beatmapset_id not in beatmapsets:
            beatmapsets.append(beatmap.beatmapset)
    beatmapset_resps = [
        await BeatmapsetModel.transform(
            beatmapset,
        )
        for beatmapset in beatmapsets
    ]

    playlist_items_resps = [
        await PlaylistModel.transform(item, includes=["details", "scores"]) for item in playlist_items.values()
    ]

    return {
        "beatmaps": beatmap_resps,
        "beatmapsets": beatmapset_resps,
        "current_playlist_item_id": current_playlist_item_id,
        "events": event_resps,
        "first_event_id": first_event_id,
        "last_event_id": last_event_id,
        "playlist_items": playlist_items_resps,
        "room": room_resp,
        "user": user_resps,
    }
