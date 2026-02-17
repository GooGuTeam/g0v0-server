"""LIO (Legacy IO) router for osu-server-spectator compatibility."""

import base64
import json
from typing import Any

from app.const import BANCHOBOT_ID
from app.database.chat import ChannelType, ChatChannel  # ChatChannel model & enum
from app.database.playlists import Playlist as DBPlaylist
from app.database.room import Room
from app.database.room_participated_user import RoomParticipatedUser
from app.database.user import User
from app.dependencies.database import Database, Redis
from app.dependencies.fetcher import Fetcher
from app.dependencies.storage import StorageService
from app.helpers import camel_to_snake, utcnow
from app.log import log
from app.models.error import ErrorType, FieldMissingError, RequestError
from app.models.events.score import ReplayUploadedEvent
from app.models.playlist import PlaylistItem
from app.models.room import MatchType, QueueMode, RoomCategory, RoomStatus
from app.models.score import RULESETS_VERSION_HASH, GameMode, VersionEntry
from app.plugins import hub

from .notification.server import server

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import update
from sqlmodel import col, select

router = APIRouter(prefix="/_lio", include_in_schema=False)
logger = log("LegacyIO")


async def _ensure_room_chat_channel(
    db: Database,
    room: Room,
    host_user_id: int,
) -> ChatChannel:
    """Create or ensure a chat channel exists for the room.

    Creates a chat channel with name mp_{room.id} for the multiplayer room.
    The channel_id is synchronized with room.channel_id.

    Args:
        db: Database session.
        room: Room object to create channel for.
        host_user_id: ID of the room host.

    Returns:
        The created or existing ChatChannel.
    """
    # 1) Check if channel already exists by channel_id
    try:
        # Use db.execute instead of db.exec for better async compatibility
        result = await db.exec(select(ChatChannel).where(ChatChannel.channel_id == room.channel_id))
        ch = result.first()
    except Exception as e:
        logger.debug(f"Error querying ChatChannel: {e}")
        ch = None

    if ch is None:
        ch = ChatChannel(
            channel_name=f"mp_{room.id}",  # Channel name can be customized (ensure uniqueness)
            description=f"Multiplayer room {room.id} chat",
            type=ChannelType.MULTIPLAYER,
        )
        db.add(ch)
        # Commit immediately to ensure the channel exists
        await db.commit()
        await db.refresh(ch)
        await db.refresh(room)
        if room.channel_id == 0:
            room.channel_id = ch.channel_id
    else:
        room.channel_id = ch.channel_id

    return ch


class RoomCreateRequest(BaseModel):
    """Request model for creating a multiplayer room."""

    name: str
    user_id: int
    password: str | None = None
    match_type: str = "HeadToHead"
    queue_mode: str = "HostOnly"
    initial_playlist: list[dict[str, Any]] = []
    playlist: list[dict[str, Any]] = []


async def _validate_user_exists(db: Database, user_id: int) -> User:
    """Validate that a user exists in the database."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise RequestError(ErrorType.USER_NOT_FOUND, {"user_id": user_id})

    return user


def _parse_room_enums(match_type: str, queue_mode: str) -> tuple[MatchType, QueueMode]:
    """Parse and validate room type enums."""
    try:
        match_type_enum = MatchType(camel_to_snake(match_type))
    except ValueError:
        match_type_enum = MatchType.HEAD_TO_HEAD

    try:
        queue_mode_enum = QueueMode(camel_to_snake(queue_mode))
    except ValueError:
        queue_mode_enum = QueueMode.HOST_ONLY

    return match_type_enum, queue_mode_enum


def _coerce_playlist_item(item_data: dict[str, Any], default_order: int, host_user_id: int) -> dict[str, Any]:
    """
    Normalize playlist item data with default values.

    Args:
        item_data: Raw playlist item data
        default_order: Default playlist order
        host_user_id: Host user ID for default owner

    Returns:
        Dict with normalized playlist item data
    """
    # Use host_user_id if owner_id is 0 or not provided
    owner_id = item_data.get("owner_id", host_user_id)
    if owner_id == 0:
        owner_id = host_user_id

    return {
        "owner_id": owner_id,
        "ruleset_id": item_data.get("ruleset_id", 0),
        "beatmap_id": item_data.get("beatmap_id"),
        "required_mods": item_data.get("required_mods", []),
        "allowed_mods": item_data.get("allowed_mods", []),
        "expired": bool(item_data.get("expired", False)),
        "playlist_order": item_data.get("playlist_order", default_order),
        "played_at": item_data.get("played_at"),
        "freestyle": bool(item_data.get("freestyle", True)),
        "beatmap_checksum": item_data.get("beatmap_checksum", ""),
        "star_rating": item_data.get("star_rating", 0.0),
    }


def _validate_playlist_items(items: list[dict[str, Any]]) -> None:
    """Validate playlist items data."""
    if not items:
        raise RequestError(ErrorType.PLAYLIST_EMPTY_ON_CREATION)

    for idx, item in enumerate(items):
        if item["beatmap_id"] is None:
            raise RequestError(
                ErrorType.MISSING_BEATMAP_ID_PLAYLIST,
                {"error": f"Playlist item at index {idx} missing beatmap_id"},
            )

        ruleset_id = item["ruleset_id"]
        if not isinstance(ruleset_id, int):
            raise RequestError(
                ErrorType.INVALID_RULESET_ID,
                {"error": f"Playlist item at index {idx} has invalid ruleset_id {ruleset_id}"},
            )


async def _create_room(db: Database, room_data: dict[str, Any]) -> tuple[Room, int]:
    host_user_id = room_data.get("user_id", BANCHOBOT_ID)
    match_type = room_data.get("match_type", "HeadToHead" if host_user_id != BANCHOBOT_ID else "Matchmaking")
    room_name = room_data.get("name", f"{match_type} room: {utcnow().isoformat()}")
    password = room_data.get("password")
    queue_mode = room_data.get("queue_mode", "HostOnly")

    if not host_user_id or not isinstance(host_user_id, int):
        raise FieldMissingError(["user_id"])

    await _validate_user_exists(db, host_user_id)

    match_type_enum, queue_mode_enum = _parse_room_enums(match_type, queue_mode)

    # Create room
    room = Room(
        name=room_name,
        category=RoomCategory.REALTIME,
        host_id=host_user_id,
        password=password or None,
        type=match_type_enum,
        queue_mode=queue_mode_enum,
        status=RoomStatus.IDLE,
        participant_count=1,
        auto_skip=False,
        auto_start_duration=0,
    )

    db.add(room)
    await db.commit()
    await db.refresh(room)

    return room, host_user_id


async def _add_playlist_items(db: Database, room_id: int, room_data: dict[str, Any], host_user_id: int) -> None:
    """Add playlist items to the room."""
    initial_playlist = room_data.get("initial_playlist", [])
    legacy_playlist = room_data.get("playlist", [])

    items_raw: list[dict[str, Any]] = []

    # Process initial playlist
    for i, item in enumerate(initial_playlist):
        if hasattr(item, "dict"):
            item = item.dict()
        items_raw.append(_coerce_playlist_item(item, i, host_user_id))

    # Process legacy playlist
    start_index = len(items_raw)
    for j, item in enumerate(legacy_playlist, start=start_index):
        items_raw.append(_coerce_playlist_item(item, j, host_user_id))

    # Validate playlist items
    _validate_playlist_items(items_raw)

    # Insert playlist items
    for item_data in items_raw:
        playlist_item = PlaylistItem(
            id=-1,  # Placeholder, will be assigned by add_to_db
            owner_id=item_data["owner_id"],
            ruleset_id=item_data["ruleset_id"],
            expired=item_data["expired"],
            playlist_order=item_data["playlist_order"],
            played_at=item_data["played_at"],
            allowed_mods=item_data["allowed_mods"],
            required_mods=item_data["required_mods"],
            beatmap_id=item_data["beatmap_id"],
            freestyle=item_data["freestyle"],
            beatmap_checksum=item_data["beatmap_checksum"],
            star_rating=item_data["star_rating"],
        )
        await DBPlaylist.add_to_db(playlist_item, room_id=room_id, session=db)


async def _add_host_as_participant(db: Database, room_id: int, host_user_id: int) -> None:
    """Add the host as a room participant and update participant count."""
    participant = RoomParticipatedUser(room_id=room_id, user_id=host_user_id)
    db.add(participant)

    await _update_room_participant_count(db, room_id)


async def _verify_room_password(db: Database, room_id: int, provided_password: str | None) -> None:
    """Verify room password if required."""
    room_result = await db.execute(select(Room).where(col(Room.id) == room_id))
    room = room_result.scalar_one_or_none()

    if room is None:
        logger.debug(f"Room {room_id} not found")
        raise RequestError(ErrorType.ROOM_NOT_FOUND)

    logger.debug(f"Room {room_id} has password: {bool(room.password)}, provided: {bool(provided_password)}")

    # If room has password but none provided
    if room.password and not provided_password:
        logger.debug(f"Room {room_id} requires password but none provided")
        raise RequestError(ErrorType.ROOM_PASSWORD_REQUIRED)

    # If room has password and provided password doesn't match
    if room.password and provided_password and provided_password != room.password:
        logger.debug(f"Room {room_id} password mismatch")
        raise RequestError(ErrorType.ROOM_INVALID_PASSWORD)

    logger.debug(f"Room {room_id} password verification passed")


async def _add_or_update_participant(db: Database, room_id: int, user_id: int) -> None:
    """Add user as participant or update existing participation record.

    Args:
        db: Database session.
        room_id: ID of the room.
        user_id: ID of the user to add.
    """
    # Check if user already has an active participation record
    existing_result = await db.execute(
        select(RoomParticipatedUser.id).where(
            RoomParticipatedUser.room_id == room_id,
            RoomParticipatedUser.user_id == user_id,
            col(RoomParticipatedUser.left_at).is_(None),
        )
    )
    existing_ids = existing_result.scalars().all()  # Get all matching IDs

    if existing_ids:
        # If multiple records exist, clean up duplicates, keep only the latest one
        if len(existing_ids) > 1:
            logger.debug(
                f"Warning: User {user_id} has {len(existing_ids)} active participation records in room {room_id}"
            )

            # Mark all records except the first as left (cleanup duplicates)
            for extra_id in existing_ids[1:]:
                await db.execute(
                    update(RoomParticipatedUser)
                    .where(col(RoomParticipatedUser.id) == extra_id)
                    .values(left_at=utcnow())
                )

        # Update the remaining active participation record (refresh join time)
        await db.execute(
            update(RoomParticipatedUser)
            .where(col(RoomParticipatedUser.id) == existing_ids[0])
            .values(joined_at=utcnow())
        )
    else:
        # Create new participation record
        participant = RoomParticipatedUser(room_id=room_id, user_id=user_id)
        db.add(participant)


class BeatmapEnsureRequest(BaseModel):
    """Request model for ensuring beatmap exists."""

    beatmap_id: int


async def _ensure_beatmap_exists(db: Database, fetcher, redis, beatmap_id: int) -> dict[str, Any]:
    """Ensure beatmap exists (including metadata and raw file cache).

    Args:
        db: Database session.
        fetcher: API fetcher service.
        redis: Redis connection.
        beatmap_id: Beatmap ID.

    Returns:
        Dict containing status information.
    """
    try:
        # 1. Ensure beatmap metadata exists in database
        from app.database.beatmap import Beatmap

        beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=beatmap_id)

        if not beatmap:
            return {"success": False, "error": f"Beatmap {beatmap_id} not found", "beatmap_id": beatmap_id}

        # 2. Pre-cache beatmap raw file
        cache_key = f"beatmap:{beatmap_id}:raw"
        cached = await redis.exists(cache_key)

        if not cached:
            # Asynchronously preload raw file to cache
            try:
                await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
                logger.debug(f"Successfully cached raw beatmap file for {beatmap_id}")
            except Exception as e:
                logger.debug(f"Warning: Failed to cache raw beatmap {beatmap_id}: {e}")
                # Even if raw file caching fails, consider ensure operation successful (metadata exists)

        return {
            "success": True,
            "beatmap_id": beatmap_id,
            "metadata_cached": True,
            "raw_file_cached": await redis.exists(cache_key),
            "beatmap_title": f"{beatmap.beatmapset.artist} - {beatmap.beatmapset.title} [{beatmap.version}]",
        }

    except Exception as e:
        logger.debug(f"Error ensuring beatmap {beatmap_id}: {e}")
        return {"success": False, "error": str(e), "beatmap_id": beatmap_id}


async def _update_room_participant_count(db: Database, room_id: int) -> int:
    """Update room participant count and return current count.

    Args:
        db: Database session.
        room_id: ID of the room.

    Returns:
        Current active participant count.
    """
    # Count active participants
    active_participants_result = await db.execute(
        select(RoomParticipatedUser.user_id).where(
            RoomParticipatedUser.room_id == room_id, col(RoomParticipatedUser.left_at).is_(None)
        )
    )
    active_participants = active_participants_result.all()
    count = len(active_participants)

    # Update room participant count
    await db.execute(update(Room).where(col(Room.id) == room_id).values(participant_count=count))

    return count


async def _end_room_if_empty(db: Database, room_id: int) -> bool:
    """Mark room as ended if empty. Returns whether the room was ended.

    Args:
        db: Database session.
        room_id: ID of the room.

    Returns:
        True if room was ended, False otherwise.
    """
    # Check if room still has active participants
    participant_count = await _update_room_participant_count(db, room_id)

    if participant_count == 0:
        # Room is empty, mark as ended
        now = utcnow()
        await db.execute(
            update(Room)
            .where(col(Room.id) == room_id)
            .values(
                ends_at=now,
                status=RoomStatus.IDLE,  # Or use RoomStatus.ENDED if available
                participant_count=0,
            )
        )
        logger.debug(f"Room {room_id} ended automatically (no participants remaining)")
        return True

    return False


async def _transfer_ownership_or_end_room(db: Database, room_id: int, leaving_user_id: int) -> bool:
    """Handle host leaving logic: transfer ownership or end room.

    Args:
        db: Database session.
        room_id: ID of the room.
        leaving_user_id: ID of the user leaving.

    Returns:
        True if room was ended, False if ownership was transferred.
    """
    # Find other active participants to transfer ownership
    remaining_result = await db.execute(
        select(RoomParticipatedUser.user_id)
        .where(
            col(RoomParticipatedUser.room_id) == room_id,
            col(RoomParticipatedUser.user_id) != leaving_user_id,
            col(RoomParticipatedUser.left_at).is_(None),
        )
        .order_by(col(RoomParticipatedUser.joined_at))  # Order by join time
    )
    remaining_participants = remaining_result.all()

    if remaining_participants:
        # Transfer ownership to the earliest joined user
        new_owner_id = remaining_participants[0][0]  # Get user_id
        await db.execute(update(Room).where(col(Room.id) == room_id).values(host_id=new_owner_id))
        logger.debug(f"Room {room_id} ownership transferred from {leaving_user_id} to {new_owner_id}")
        return False  # Room continues to exist
    else:
        # No other participants, end room
        return await _end_room_if_empty(db, room_id)


# ===== API ENDPOINTS =====


@router.post("/multiplayer/rooms")
async def create_multiplayer_room(
    room_data: dict[str, Any],
    db: Database,
) -> int:
    """Create a new multiplayer room with initial playlist."""
    try:
        # Parse room data if string
        if isinstance(room_data, str):
            room_data = json.loads(room_data)

        logger.debug(f"Creating room with data: {room_data}")

        # Create room
        room, host_user_id = await _create_room(db, room_data)
        room_id = room.id

        try:
            channel = await _ensure_room_chat_channel(db, room, host_user_id)

            # Have the host join the channel
            host_user = await db.get(User, host_user_id)
            if host_user:
                await server.batch_join_channel([host_user], channel)
            # Add playlist items
            await _add_playlist_items(db, room_id, room_data, host_user_id)

            # Add host as participant
            # await _add_host_as_participant(db, room_id, host_user_id)

            await db.commit()
            return room_id

        except RequestError:
            # Clean up room if playlist creation fails
            await db.delete(room)
            await db.commit()
            raise

    except RequestError:
        raise


@router.delete("/multiplayer/rooms/{room_id}/users/{user_id}")
async def remove_user_from_room(
    room_id: int,
    user_id: int,
    db: Database,
) -> dict[str, Any]:
    """Remove a user from a multiplayer room."""
    try:
        now = utcnow()

        # Check if room exists
        room_result = await db.execute(select(Room).where(col(Room.id) == room_id))
        room = room_result.scalar_one_or_none()

        if room is None:
            raise RequestError(ErrorType.ROOM_NOT_FOUND)

        room_owner_id = room.host_id
        ends_at = room.ends_at
        channel_id = room.channel_id

        # If room has already ended, return immediately
        if ends_at is not None:
            logger.debug(f"Room {room_id} is already ended")
            return {"success": True, "room_ended": True}

        # Check if user is in the room
        participant_result = await db.execute(
            select(RoomParticipatedUser.id).where(
                col(RoomParticipatedUser.room_id) == room_id,
                col(RoomParticipatedUser.user_id) == user_id,
                col(RoomParticipatedUser.left_at).is_(None),
            )
        )
        participant_query = participant_result.first()

        if not participant_query:
            # User is not in the room, check if room needs to end (idempotent operation)
            room_ended = await _end_room_if_empty(db, room_id)
            await db.commit()

            try:
                if channel_id:
                    await server.leave_room_channel(int(channel_id), int(user_id))
                    if room_ended:
                        server.channels.pop(int(channel_id), None)
            except Exception as e:
                logger.debug(f"[warn] failed to leave user {user_id} from channel {channel_id}: {e}")

            return {"success": True, "room_ended": room_ended}

        # Mark user as left
        await db.execute(
            update(RoomParticipatedUser)
            .where(
                col(RoomParticipatedUser.room_id) == room_id,
                col(RoomParticipatedUser.user_id) == user_id,
                col(RoomParticipatedUser.left_at).is_(None),
            )
            .values(left_at=now)
        )

        room_ended = False

        # Check if host is leaving
        if user_id == room_owner_id:
            logger.debug(f"Host {user_id} is leaving room {room_id}")
            room_ended = await _transfer_ownership_or_end_room(db, room_id, user_id)
        else:
            # Not the host leaving, only check if room is empty
            room_ended = await _end_room_if_empty(db, room_id)

        await db.commit()
        logger.debug(f"Successfully removed user {user_id} from room {room_id}, room_ended: {room_ended}")

        # ===== After commit: remove user from chat channel; if room ended, cleanup in-memory channel =====
        try:
            if channel_id:
                await server.leave_room_channel(int(channel_id), int(user_id))
                if room_ended:
                    server.channels.pop(int(channel_id), None)
        except Exception as e:
            logger.debug(f"[warn] failed to leave user {user_id} from channel {channel_id}: {e}")

        return {"success": True, "room_ended": room_ended}

    except RequestError:
        raise
    except Exception as e:
        await db.rollback()
        logger.debug(f"Error removing user from room: {e!s}")
        raise


@router.put("/multiplayer/rooms/{room_id}/users/{user_id}")
async def add_user_to_room(
    request: Request,
    room_id: int,
    user_id: int,
    db: Database,
) -> dict[str, Any]:
    """Add a user to a multiplayer room."""
    logger.debug(f"Adding user {user_id} to room {room_id}")

    # Get request body and parse user_data
    body = await request.body()
    user_data = None
    if body:
        try:
            user_data = json.loads(body.decode("utf-8"))
            logger.debug(f"Parsed user_data: {user_data}")
        except json.JSONDecodeError:
            logger.debug("Failed to parse user_data from request body")
            user_data = None

    # Check if room has ended
    room_result = await db.exec(select(Room.ends_at, Room.channel_id, Room.host_id).where(col(Room.id) == room_id))
    room_row = room_result.first()
    if not room_row:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)

    ends_at, channel_id, host_user_id = room_row
    if ends_at is not None:
        logger.debug(f"User {user_id} attempted to join ended room {room_id}")
        raise RequestError(ErrorType.ROOM_ENDED_CANNOT_ACCEPT_NEW)

    # Verify room password
    provided_password = user_data.get("password") if user_data else None
    logger.debug(f"Verifying room {room_id} with password: {provided_password}")
    await _verify_room_password(db, room_id, provided_password)

    # Add or update participant
    await _add_or_update_participant(db, room_id, user_id)
    # Update participant count
    await _update_room_participant_count(db, room_id)

    # Commit DB state first to ensure participation is effective
    await db.commit()
    logger.debug(f"Successfully added user {user_id} to room {room_id}")

    # ===== Ensure chat channel exists and add user =====
    try:
        # If room doesn't have a channel assigned/created yet, create and sync back
        if not channel_id:
            room = await db.get(Room, room_id)
            if room is None:
                raise RequestError(ErrorType.ROOM_NOT_FOUND)
            await _ensure_room_chat_channel(db, room, host_user_id)
            await db.refresh(room)
            channel_id = room.channel_id

        if channel_id:
            # Join chat channel -> register in memory + send chat.channel.join to online clients
            await server.join_room_channel(int(channel_id), int(user_id))
        else:
            # Theoretically should not happen; log for debugging
            logger.debug(f"[warn] Room {room_id} has no channel_id after ensure.")
    except Exception as e:
        # Does not affect the main room join flow, only log
        logger.debug(f"[warn] failed to join user {user_id} to channel of room {room_id}: {e}")

    return {"success": True}


@router.post("/beatmaps/ensure")
async def ensure_beatmap_present(
    beatmap_data: BeatmapEnsureRequest,
    db: Database,
    redis: Redis,
    fetcher: Fetcher,
) -> dict[str, Any]:
    """Ensure beatmap exists on server (including metadata and raw file cache).

    This endpoint is used by osu-server-spectator to ensure beatmap files
    are available on the server side, avoiding delays when fetching on demand.

    Args:
        beatmap_data: Request containing beatmap_id.
        db: Database session.
        redis: Redis connection.
        fetcher: Fetcher service.

    Returns:
        Dict with success status and beatmap info.
    """
    try:
        beatmap_id = beatmap_data.beatmap_id
        logger.debug(f"Ensuring beatmap {beatmap_id} is present")

        # Ensure beatmap exists
        result = await _ensure_beatmap_exists(db, fetcher, redis, beatmap_id)

        # Commit database changes
        await db.commit()

        logger.debug(f"Ensure beatmap {beatmap_id} result: {result}")
        return result

    except RequestError:
        raise
    except Exception as e:
        await db.rollback()
        logger.debug(f"Error ensuring beatmap: {e!s}")
        raise


class ReplayDataRequest(BaseModel):
    """Request model for saving replay data."""

    score_id: int
    user_id: int
    mreplay: str
    beatmap_id: int


@router.post("/scores/replay")
async def save_replay(
    req: ReplayDataRequest,
    storage_service: StorageService,
):
    """Save replay data for a score.

    Stores the base64-encoded replay data to the storage service.

    Args:
        req: Request containing score_id, user_id, beatmap_id, and replay data.
        storage_service: Storage service dependency.
    """
    replay_data = req.mreplay
    replay_path = f"replays/{req.score_id}_{req.beatmap_id}_{req.user_id}_lazer_replay.osr"
    replay_bytes = base64.b64decode(replay_data)
    await storage_service.write_file(replay_path, replay_bytes, "application/x-osu-replay")
    hub.emit(
        ReplayUploadedEvent(
            score_id=req.score_id,
            uploader_user_id=req.user_id,
            file_path=replay_path,
            replay_data=replay_bytes,
        )
    )


@router.get("/ruleset-hashes", response_model=dict[GameMode, VersionEntry])
async def get_ruleset_version():
    """Get ruleset version hashes.

    Returns the mapping of game modes to their ruleset version information,
    used by spectator server for compatibility checking.

    Returns:
        Dict mapping GameMode to VersionEntry with hash information.
    """
    return RULESETS_VERSION_HASH
