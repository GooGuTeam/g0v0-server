from datetime import UTC
from typing import Annotated, Literal

from app.database.beatmap import Beatmap, BeatmapResp
from app.database.beatmapset import BeatmapsetResp
from app.database.item_attempts_count import ItemAttemptsCount, ItemAttemptsResp
from app.database.multiplayer_event import MultiplayerEvent, MultiplayerEventResp
from app.database.playlists import Playlist, PlaylistResp
from app.database.room import APIUploadedRoom, Room, RoomResp
from app.database.room_participated_user import RoomParticipatedUser
from app.database.score import Score
from app.database.user import User, UserResp
from app.dependencies.database import Database, Redis
from app.dependencies.user import ClientUser, get_current_user
from app.models.room import RoomCategory, RoomStatus
from app.service.room import create_playlist_room_from_api
from app.utils import utcnow

from .router import router

from fastapi import HTTPException, Path, Query, Security
from pydantic import BaseModel, Field
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession


@router.get(
    "/rooms",
    tags=["房间"],
    response_model=list[RoomResp],
    name="获取房间列表",
    description="获取房间列表。支持按状态/模式筛选",
)
async def get_all_rooms(
    db: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    mode: Annotated[
        Literal["open", "ended", "participated", "owned"] | None,
        Query(
            description=("房间模式：open 当前开放 / ended 已经结束 / participated 参与过 / owned 自己创建的房间"),
        ),
    ] = "open",
    category: Annotated[
        RoomCategory,
        Query(
            description=("房间分类：NORMAL 普通歌单模式房间 / REALTIME 多人游戏房间 / DAILY_CHALLENGE 每日挑战"),
        ),
    ] = RoomCategory.NORMAL,
    status: Annotated[RoomStatus | None, Query(description="房间状态（可选）")] = None,
):
    resp_list: list[RoomResp] = []
    where_clauses: list[ColumnElement[bool]] = [col(Room.category) == category]
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
        resp = await RoomResp.from_db(room, db)
        if category == RoomCategory.REALTIME:
            resp.category = RoomCategory.NORMAL

        resp_list.append(resp)

    return resp_list


class APICreatedRoom(RoomResp):
    """创建房间返回模型，继承 RoomResp。额外字段:
    - error: 错误信息（为空表示成功）。"""

    error: str = ""


async def _participate_room(room_id: int, user_id: int, db_room: Room, session: AsyncSession, redis: Redis):
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
    tags=["房间"],
    response_model=APICreatedRoom,
    name="创建房间",
    description="\n创建一个新的房间。",
)
async def create_room(
    db: Database,
    room: APIUploadedRoom,
    current_user: ClientUser,
    redis: Redis,
):
    if await current_user.is_restricted(db):
        raise HTTPException(status_code=403, detail="Your account is restricted from multiplayer.")

    user_id = current_user.id
    db_room = await create_playlist_room_from_api(db, room, user_id)
    await _participate_room(db_room.id, user_id, db_room, db, redis)
    await db.commit()
    await db.refresh(db_room)
    created_room = APICreatedRoom.model_validate(await RoomResp.from_db(db_room, db))
    created_room.error = ""
    return created_room


@router.get(
    "/rooms/{room_id}",
    tags=["房间"],
    response_model=RoomResp,
    name="获取房间详情",
    description="获取单个房间详情。",
)
async def get_room(
    db: Database,
    room_id: Annotated[int, Path(..., description="房间 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    category: Annotated[
        str,
        Query(
            description=("房间分类：NORMAL 普通歌单模式房间 / REALTIME 多人游戏房间 / DAILY_CHALLENGE 每日挑战 (可选)"),
        ),
    ] = "",
):
    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is None:
        raise HTTPException(404, "Room not found")
    resp = await RoomResp.from_db(db_room, include=["current_user_score"], session=db, user=current_user)
    return resp


@router.delete(
    "/rooms/{room_id}",
    tags=["房间"],
    name="结束房间",
    description="\n结束歌单模式房间。",
)
async def delete_room(
    db: Database,
    room_id: Annotated[int, Path(..., description="房间 ID")],
    current_user: ClientUser,
):
    if await current_user.is_restricted(db):
        raise HTTPException(status_code=403, detail="Your account is restricted from multiplayer.")

    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is None:
        raise HTTPException(404, "Room not found")
    else:
        db_room.ends_at = utcnow()
        await db.commit()
        return None


@router.put(
    "/rooms/{room_id}/users/{user_id}",
    tags=["房间"],
    name="加入房间",
    description="\n加入指定歌单模式房间。",
)
async def add_user_to_room(
    db: Database,
    room_id: Annotated[int, Path(..., description="房间 ID")],
    user_id: Annotated[int, Path(..., description="用户 ID")],
    redis: Redis,
    current_user: ClientUser,
):
    if await current_user.is_restricted(db):
        raise HTTPException(status_code=403, detail="Your account is restricted from multiplayer.")

    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is not None:
        await _participate_room(room_id, user_id, db_room, db, redis)
        await db.commit()
        await db.refresh(db_room)
        resp = await RoomResp.from_db(db_room, db)
        return resp
    else:
        raise HTTPException(404, "room not found0")


@router.delete(
    "/rooms/{room_id}/users/{user_id}",
    tags=["房间"],
    name="离开房间",
    description="\n离开指定歌单模式房间。",
)
async def remove_user_from_room(
    db: Database,
    room_id: Annotated[int, Path(..., description="房间 ID")],
    user_id: Annotated[int, Path(..., description="用户 ID")],
    current_user: ClientUser,
    redis: Redis,
):
    if await current_user.is_restricted(db):
        raise HTTPException(status_code=403, detail="Your account is restricted from multiplayer.")

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
        raise HTTPException(404, "Room not found")


class APILeaderboard(BaseModel):
    """房间全局排行榜返回模型。
    - leaderboard: 用户游玩统计（尝试次数/分数等）。
    - user_score: 当前用户对应统计。"""

    leaderboard: list[ItemAttemptsResp] = Field(default_factory=list)
    user_score: ItemAttemptsResp | None = None


@router.get(
    "/rooms/{room_id}/leaderboard",
    tags=["房间"],
    response_model=APILeaderboard,
    name="获取房间排行榜",
    description="获取房间内累计得分排行榜。",
)
async def get_room_leaderboard(
    db: Database,
    room_id: Annotated[int, Path(..., description="房间 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
):
    db_room = (await db.exec(select(Room).where(Room.id == room_id))).first()
    if db_room is None:
        raise HTTPException(404, "Room not found")
    aggs = await db.exec(
        select(ItemAttemptsCount)
        .where(ItemAttemptsCount.room_id == room_id)
        .order_by(col(ItemAttemptsCount.total_score).desc())
    )
    aggs_resp = []
    user_agg = None
    for i, agg in enumerate(aggs):
        resp = await ItemAttemptsResp.from_db(agg, db)
        resp.position = i + 1
        aggs_resp.append(resp)
        if agg.user_id == current_user.id:
            user_agg = resp
    return APILeaderboard(
        leaderboard=aggs_resp,
        user_score=user_agg,
    )


class RoomEvents(BaseModel):
    """房间事件流返回模型。
    - beatmaps: 本次结果涉及的谱面列表。
    - beatmapsets: 谱面集映射。
    - current_playlist_item_id: 当前游玩列表（项目）项 ID。
    - events: 事件列表。
    - first_event_id / last_event_id: 事件范围。
    - playlist_items: 房间游玩列表（项目）详情。
    - room: 房间详情。
    - user: 关联用户列表。"""

    beatmaps: list[BeatmapResp] = Field(default_factory=list)
    beatmapsets: dict[int, BeatmapsetResp] = Field(default_factory=dict)
    current_playlist_item_id: int = 0
    events: list[MultiplayerEventResp] = Field(default_factory=list)
    first_event_id: int = 0
    last_event_id: int = 0
    playlist_items: list[PlaylistResp] = Field(default_factory=list)
    room: RoomResp
    user: list[UserResp] = Field(default_factory=list)


@router.get(
    "/rooms/{room_id}/events",
    response_model=RoomEvents,
    tags=["房间"],
    name="获取房间事件",
    description="获取房间事件列表 （倒序，可按 after / before 进行范围截取）。",
)
async def get_room_events(
    db: Database,
    room_id: Annotated[int, Path(..., description="房间 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    limit: Annotated[int, Query(ge=1, le=1000, description="返回条数 (1-1000)")] = 100,
    after: Annotated[int | None, Query(ge=0, description="仅包含大于该事件 ID 的事件")] = None,
    before: Annotated[int | None, Query(ge=0, description="仅包含小于该事件 ID 的事件")] = None,
):
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
        raise HTTPException(404, "Room not found")
    room_resp = await RoomResp.from_db(room, db)
    if room.category == RoomCategory.REALTIME and room_resp.current_playlist_item:
        current_playlist_item_id = room_resp.current_playlist_item.id

    users = await db.exec(select(User).where(col(User.id).in_(user_ids)))
    user_resps = [await UserResp.from_db(user, db) for user in users]
    beatmaps = await db.exec(select(Beatmap).where(col(Beatmap.id).in_(beatmap_ids)))
    beatmap_resps = [await BeatmapResp.from_db(beatmap, session=db) for beatmap in beatmaps]
    beatmapset_resps = {}
    for beatmap_resp in beatmap_resps:
        beatmapset_resps[beatmap_resp.beatmapset_id] = beatmap_resp.beatmapset

    playlist_items_resps = [await PlaylistResp.from_db(item) for item in playlist_items.values()]

    return RoomEvents(
        beatmaps=beatmap_resps,
        beatmapsets=beatmapset_resps,
        current_playlist_item_id=current_playlist_item_id,
        events=event_resps,
        first_event_id=first_event_id,
        last_event_id=last_event_id,
        playlist_items=playlist_items_resps,
        room=room_resp,
        user=user_resps,
    )
