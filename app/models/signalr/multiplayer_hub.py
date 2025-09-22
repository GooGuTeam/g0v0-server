from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    TypedDict,
    cast,
    override,
)

from app.database.beatmap import Beatmap
from app.dependencies.database import with_db
from app.dependencies.fetcher import get_fetcher
from app.exception import InvokeException
from app.models.beatmap.mods import API_MODS, APIMod
from app.models.room import (
    DownloadState,
    MatchType,
    MultiplayerRoomState,
    MultiplayerUserState,
    QueueMode,
    RoomCategory,
    RoomStatus,
)
from app.models.signalr import SignalRMeta, SignalRUnionMessage, UserState
from app.utils import utcnow

from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlmodel import col

if TYPE_CHECKING:
    from app.database.room import Room
    from app.signalr.hub import MultiplayerHub

HOST_LIMIT = 50
PER_USER_LIMIT = 3


class MultiplayerClientState(UserState):
    room_id: int = 0


class MultiplayerRoomSettings(BaseModel):
    name: str = "Unnamed Room"
    playlist_item_id: Annotated[int, Field(default=0), SignalRMeta(use_abbr=False)]
    password: str = ""
    match_type: MatchType = MatchType.HEAD_TO_HEAD
    queue_mode: QueueMode = QueueMode.HOST_ONLY
    auto_start_duration: timedelta = timedelta(seconds=0)
    auto_skip: bool = False

    @property
    def auto_start_enabled(self) -> bool:
        return self.auto_start_duration != timedelta(seconds=0)


class BeatmapAvailability(BaseModel):
    state: DownloadState = DownloadState.UNKNOWN
    download_progress: float | None = None


class _MatchUserState(SignalRUnionMessage): ...


class TeamVersusUserState(_MatchUserState):
    team_id: int

    union_type: ClassVar[Literal[0]] = 0


MatchUserState = TeamVersusUserState


class _MatchRoomState(SignalRUnionMessage): ...


class MultiplayerTeam(BaseModel):
    id: int
    name: str


class TeamVersusRoomState(_MatchRoomState):
    teams: list[MultiplayerTeam] = Field(
        default_factory=lambda: [
            MultiplayerTeam(id=0, name="Team Red"),
            MultiplayerTeam(id=1, name="Team Blue"),
        ]
    )

    union_type: ClassVar[Literal[0]] = 0


MatchRoomState = TeamVersusRoomState


class PlaylistItem(BaseModel):
    id: Annotated[int, Field(default=0), SignalRMeta(use_abbr=False)]
    owner_id: int
    beatmap_id: int
    beatmap_checksum: str
    ruleset_id: int
    required_mods: list[APIMod] = Field(default_factory=list)
    allowed_mods: list[APIMod] = Field(default_factory=list)
    expired: bool
    playlist_order: int
    played_at: datetime | None = None
    star_rating: float
    freestyle: bool

    def _validate_mod_for_ruleset(self, mod: APIMod, ruleset_key: int, context: str = "mod") -> None:
        typed_ruleset_key = cast(Literal[0, 1, 2, 3], ruleset_key)

        # Check if mod is valid for ruleset
        if typed_ruleset_key not in API_MODS or mod["acronym"] not in API_MODS[typed_ruleset_key]:
            raise InvokeException(f"{context} {mod['acronym']} is invalid for this ruleset")

        mod_settings = API_MODS[typed_ruleset_key][mod["acronym"]]

        # Check if mod is unplayable in multiplayer
        if mod_settings.get("UserPlayable", True) is False:
            raise InvokeException(f"{context} {mod['acronym']} is not playable by users")

        if mod_settings.get("ValidForMultiplayer", True) is False:
            raise InvokeException(f"{context} {mod['acronym']} is not valid for multiplayer")

    def _check_mod_compatibility(self, mods: list[APIMod], ruleset_key: int) -> None:
        from typing import Literal, cast

        typed_ruleset_key = cast(Literal[0, 1, 2, 3], ruleset_key)

        for i, mod1 in enumerate(mods):
            mod1_settings = API_MODS[typed_ruleset_key].get(mod1["acronym"])
            if mod1_settings:
                incompatible = set(mod1_settings.get("IncompatibleMods", []))
                for mod2 in mods[i + 1 :]:
                    if mod2["acronym"] in incompatible:
                        raise InvokeException(f"Mods {mod1['acronym']} and {mod2['acronym']} are incompatible")

    def _check_required_allowed_compatibility(self, ruleset_key: int) -> None:
        from typing import Literal, cast

        typed_ruleset_key = cast(Literal[0, 1, 2, 3], ruleset_key)
        allowed_acronyms = {mod["acronym"] for mod in self.allowed_mods}

        for req_mod in self.required_mods:
            req_acronym = req_mod["acronym"]
            req_settings = API_MODS[typed_ruleset_key].get(req_acronym)
            if req_settings:
                incompatible = set(req_settings.get("IncompatibleMods", []))
                conflicting_allowed = allowed_acronyms & incompatible
                if conflicting_allowed:
                    conflict_list = ", ".join(conflicting_allowed)
                    raise InvokeException(f"Required mod {req_acronym} conflicts with allowed mods: {conflict_list}")

    def validate_playlist_item_mods(self) -> None:
        ruleset_key = cast(Literal[0, 1, 2, 3], self.ruleset_id)

        # Validate required mods
        for mod in self.required_mods:
            self._validate_mod_for_ruleset(mod, ruleset_key, "Required mod")

        # Validate allowed mods
        for mod in self.allowed_mods:
            self._validate_mod_for_ruleset(mod, ruleset_key, "Allowed mod")

        # Check internal compatibility of required mods
        self._check_mod_compatibility(self.required_mods, ruleset_key)

        # Check compatibility between required and allowed mods
        self._check_required_allowed_compatibility(ruleset_key)

    def validate_user_mods(
        self,
        user: "MultiplayerRoomUser",
        proposed_mods: list[APIMod],
    ) -> tuple[bool, list[APIMod]]:
        """
        Validates user mods against playlist item rules and returns valid mods.
        Returns (is_valid, valid_mods).
        """
        from typing import Literal, cast

        ruleset_id = user.ruleset_id if user.ruleset_id is not None else self.ruleset_id
        ruleset_key = cast(Literal[0, 1, 2, 3], ruleset_id)

        valid_mods = []
        all_proposed_valid = True

        # Check if mods are valid for the ruleset
        for mod in proposed_mods:
            if ruleset_key not in API_MODS or mod["acronym"] not in API_MODS[ruleset_key]:
                all_proposed_valid = False
                continue
            valid_mods.append(mod)

        # Check mod compatibility within user mods
        incompatible_mods = set()
        final_valid_mods = []
        for mod in valid_mods:
            if mod["acronym"] in incompatible_mods:
                all_proposed_valid = False
                continue
            setting_mods = API_MODS[ruleset_key].get(mod["acronym"])
            if setting_mods:
                incompatible_mods.update(setting_mods["IncompatibleMods"])
            final_valid_mods.append(mod)

        # If not freestyle, check against allowed mods
        if not self.freestyle:
            allowed_acronyms = {mod["acronym"] for mod in self.allowed_mods}
            filtered_valid_mods = []
            for mod in final_valid_mods:
                if mod["acronym"] not in allowed_acronyms:
                    all_proposed_valid = False
                else:
                    filtered_valid_mods.append(mod)
            final_valid_mods = filtered_valid_mods

        # Check compatibility with required mods
        required_mod_acronyms = {mod["acronym"] for mod in self.required_mods}
        all_mod_acronyms = {mod["acronym"] for mod in final_valid_mods} | required_mod_acronyms

        # Check for incompatibility between required and user mods
        filtered_valid_mods = []
        for mod in final_valid_mods:
            mod_acronym = mod["acronym"]
            is_compatible = True

            for other_acronym in all_mod_acronyms:
                if other_acronym == mod_acronym:
                    continue
                setting_mods = API_MODS[ruleset_key].get(mod_acronym)
                if setting_mods and other_acronym in setting_mods["IncompatibleMods"]:
                    is_compatible = False
                    all_proposed_valid = False
                    break

            if is_compatible:
                filtered_valid_mods.append(mod)

        return all_proposed_valid, filtered_valid_mods

    def clone(self) -> "PlaylistItem":
        copy = self.model_copy()
        copy.required_mods = list(self.required_mods)
        copy.allowed_mods = list(self.allowed_mods)
        copy.expired = False
        copy.played_at = None
        return copy


class _MultiplayerCountdown(SignalRUnionMessage):
    id: int = 0
    time_remaining: timedelta
    is_exclusive: Annotated[bool, Field(default=True), SignalRMeta(member_ignore=True)] = True


class MatchStartCountdown(_MultiplayerCountdown):
    union_type: ClassVar[Literal[0]] = 0


class ForceGameplayStartCountdown(_MultiplayerCountdown):
    union_type: ClassVar[Literal[1]] = 1


class ServerShuttingDownCountdown(_MultiplayerCountdown):
    union_type: ClassVar[Literal[2]] = 2


MultiplayerCountdown = MatchStartCountdown | ForceGameplayStartCountdown | ServerShuttingDownCountdown


class MultiplayerRoomUser(BaseModel):
    user_id: int
    state: MultiplayerUserState = MultiplayerUserState.IDLE
    availability: BeatmapAvailability = BeatmapAvailability(state=DownloadState.UNKNOWN, download_progress=None)
    mods: list[APIMod] = Field(default_factory=list)
    match_state: MatchUserState | None = None
    ruleset_id: int | None = None  # freestyle
    beatmap_id: int | None = None  # freestyle


class MultiplayerRoom(BaseModel):
    room_id: int
    state: MultiplayerRoomState
    settings: MultiplayerRoomSettings
    users: list[MultiplayerRoomUser] = Field(default_factory=list)
    host: MultiplayerRoomUser | None = None
    match_state: MatchRoomState | None = None
    playlist: list[PlaylistItem] = Field(default_factory=list)
    active_countdowns: list[MultiplayerCountdown] = Field(default_factory=list)
    channel_id: int

    @classmethod
    def from_db(cls, room: "Room") -> "MultiplayerRoom":
        """
        将 Room (数据库模型) 转换为 MultiplayerRoom (业务模型)
        """

        # 用户列表
        users = [MultiplayerRoomUser(user_id=room.host_id)]
        host_user = MultiplayerRoomUser(user_id=room.host_id)
        # playlist 转换
        playlist = []
        if room.playlist:
            for item in room.playlist:
                playlist.append(
                    PlaylistItem(
                        id=item.id,
                        owner_id=item.owner_id,
                        beatmap_id=item.beatmap_id,
                        beatmap_checksum=item.beatmap.checksum if item.beatmap else "",
                        ruleset_id=item.ruleset_id,
                        required_mods=item.required_mods,
                        allowed_mods=item.allowed_mods,
                        expired=item.expired,
                        playlist_order=item.playlist_order,
                        played_at=item.played_at,
                        star_rating=item.beatmap.difficulty_rating if item.beatmap is not None else 0.0,
                        freestyle=item.freestyle,
                    )
                )

        return cls(
            room_id=room.id,
            state=getattr(room, "state", MultiplayerRoomState.OPEN),
            settings=MultiplayerRoomSettings(
                name=room.name,
                playlist_item_id=playlist[0].id if playlist else 0,
                password=getattr(room, "password", ""),
                match_type=room.type,
                queue_mode=room.queue_mode,
                auto_start_duration=timedelta(seconds=room.auto_start_duration),
                auto_skip=room.auto_skip,
            ),
            users=users,
            host=host_user,
            match_state=None,
            playlist=playlist,
            active_countdowns=[],
            channel_id=room.channel_id or 0,
        )


class MultiplayerQueue:
    def __init__(self, room: "ServerMultiplayerRoom"):
        self.server_room = room
        self.current_index = 0

    @property
    def hub(self) -> "MultiplayerHub":
        return self.server_room.hub

    @property
    def upcoming_items(self):
        return sorted(
            (item for item in self.room.playlist if not item.expired),
            key=lambda i: i.playlist_order,
        )

    @property
    def room(self):
        return self.server_room.room

    async def update_order(self):
        from app.database import Playlist

        match self.room.settings.queue_mode:
            case QueueMode.ALL_PLAYERS_ROUND_ROBIN:
                ordered_active_items = []

                is_first_set = True
                first_set_order_by_user_id = {}

                active_items = [item for item in self.room.playlist if not item.expired]
                active_items.sort(key=lambda x: x.id)

                user_item_groups = {}
                for item in active_items:
                    if item.owner_id not in user_item_groups:
                        user_item_groups[item.owner_id] = []
                    user_item_groups[item.owner_id].append(item)

                max_items = max((len(items) for items in user_item_groups.values()), default=0)

                for i in range(max_items):
                    current_set = []
                    for user_id, items in user_item_groups.items():
                        if i < len(items):
                            current_set.append(items[i])

                    if is_first_set:
                        current_set.sort(key=lambda item: (item.playlist_order, item.id))
                        ordered_active_items.extend(current_set)
                        first_set_order_by_user_id = {
                            item.owner_id: idx for idx, item in enumerate(ordered_active_items)
                        }
                    else:
                        current_set.sort(key=lambda item: first_set_order_by_user_id.get(item.owner_id, 0))
                        ordered_active_items.extend(current_set)

                    is_first_set = False
            case _:
                ordered_active_items = sorted(
                    (item for item in self.room.playlist if not item.expired),
                    key=lambda x: x.id,
                )
        async with with_db() as session:
            for idx, item in enumerate(ordered_active_items):
                if item.playlist_order == idx:
                    continue
                item.playlist_order = idx
                await Playlist.update(item, self.room.room_id, session)
                await self.hub.playlist_changed(self.server_room, item, beatmap_changed=False)

    async def update_current_item(self):
        upcoming_items = self.upcoming_items
        if upcoming_items:
            # 优先选择未过期的项目
            next_item = upcoming_items[0]
        else:
            # 如果所有项目都过期了，选择最近添加的项目（played_at 为 None 或最新的）
            # 优先选择 expired=False 的项目，然后是 played_at 最晚的
            next_item = max(
                self.room.playlist,
                key=lambda i: (not i.expired, i.played_at or datetime.min),
            )
        self.current_index = self.room.playlist.index(next_item)
        last_id = self.room.settings.playlist_item_id
        self.room.settings.playlist_item_id = next_item.id
        if last_id != next_item.id:
            await self.hub.setting_changed(self.server_room, True)

    async def add_item(self, item: PlaylistItem, user: MultiplayerRoomUser):
        from app.database import Playlist

        is_host = self.room.host and self.room.host.user_id == user.user_id
        if self.room.settings.queue_mode == QueueMode.HOST_ONLY and not is_host:
            raise InvokeException("You are not the host")

        limit = HOST_LIMIT if is_host else PER_USER_LIMIT
        if len([True for u in self.room.playlist if u.owner_id == user.user_id and not u.expired]) >= limit:
            raise InvokeException(f"You can only have {limit} items in the queue")

        if item.freestyle and len(item.allowed_mods) > 0:
            raise InvokeException("Freestyle items cannot have allowed mods")

        async with with_db() as session:
            fetcher = await get_fetcher()
            async with session:
                beatmap = await Beatmap.get_or_fetch(session, fetcher, bid=item.beatmap_id)
                if beatmap is None:
                    raise InvokeException("Beatmap not found")
                if item.beatmap_checksum != beatmap.checksum:
                    raise InvokeException("Checksum mismatch")

                item.validate_playlist_item_mods()
                item.owner_id = user.user_id
                item.star_rating = beatmap.difficulty_rating
                await Playlist.add_to_db(item, self.room.room_id, session)
                self.room.playlist.append(item)
        await self.hub.playlist_added(self.server_room, item)
        await self.update_order()
        await self.update_current_item()

    async def edit_item(self, item: PlaylistItem, user: MultiplayerRoomUser):
        from app.database import Playlist

        if item.freestyle and len(item.allowed_mods) > 0:
            raise InvokeException("Freestyle items cannot have allowed mods")

        async with with_db() as session:
            fetcher = await get_fetcher()
            async with session:
                beatmap = await Beatmap.get_or_fetch(session, fetcher, bid=item.beatmap_id)
                if item.beatmap_checksum != beatmap.checksum:
                    raise InvokeException("Checksum mismatch")

                existing_item = next((i for i in self.room.playlist if i.id == item.id), None)
                if existing_item is None:
                    raise InvokeException("Attempted to change an item that doesn't exist")

                if existing_item.owner_id != user.user_id and self.room.host != user:
                    raise InvokeException("Attempted to change an item which is not owned by the user")

                if existing_item.expired:
                    raise InvokeException("Attempted to change an item which has already been played")

                item.validate_playlist_item_mods()
                item.owner_id = user.user_id
                item.star_rating = float(beatmap.difficulty_rating)
                item.playlist_order = existing_item.playlist_order

                await Playlist.update(item, self.room.room_id, session)

                # Update item in playlist
                for idx, playlist_item in enumerate(self.room.playlist):
                    if playlist_item.id == item.id:
                        self.room.playlist[idx] = item
                        break

                await self.hub.playlist_changed(
                    self.server_room,
                    item,
                    beatmap_changed=item.beatmap_checksum != existing_item.beatmap_checksum,
                )

    async def remove_item(self, playlist_item_id: int, user: MultiplayerRoomUser):
        from app.database import Playlist

        item = next(
            (i for i in self.room.playlist if i.id == playlist_item_id),
            None,
        )

        if item is None:
            raise InvokeException("Item does not exist in the room")

        # Check if it's the only item and current item
        if item == self.current_item:
            upcoming_items = [i for i in self.room.playlist if not i.expired]
            if len(upcoming_items) == 1:
                raise InvokeException("The only item in the room cannot be removed")

        if item.owner_id != user.user_id and self.room.host != user:
            raise InvokeException("Attempted to remove an item which is not owned by the user")

        if item.expired:
            raise InvokeException("Attempted to remove an item which has already been played")

        async with with_db() as session:
            await Playlist.delete_item(item.id, self.room.room_id, session)

        found_item = next((i for i in self.room.playlist if i.id == item.id), None)
        if found_item:
            self.room.playlist.remove(found_item)
        self.current_index = self.room.playlist.index(self.upcoming_items[0])

        await self.update_order()
        await self.update_current_item()
        await self.hub.playlist_removed(self.server_room, item.id)

    async def finish_current_item(self):
        from app.database import Playlist

        async with with_db() as session:
            played_at = utcnow()
            await session.execute(
                update(Playlist)
                .where(
                    col(Playlist.id) == self.current_item.id,
                    col(Playlist.room_id) == self.room.room_id,
                )
                .values(expired=True, played_at=played_at)
            )
            self.room.playlist[self.current_index].expired = True
            self.room.playlist[self.current_index].played_at = played_at
        await self.hub.playlist_changed(self.server_room, self.current_item, True)
        await self.update_order()
        if self.room.settings.queue_mode == QueueMode.HOST_ONLY and all(
            playitem.expired for playitem in self.room.playlist
        ):
            assert self.room.host
            await self.add_item(self.current_item.clone(), self.room.host)
        await self.update_current_item()

    async def update_queue_mode(self):
        if self.room.settings.queue_mode == QueueMode.HOST_ONLY and all(
            playitem.expired for playitem in self.room.playlist
        ):
            assert self.room.host
            await self.add_item(self.current_item.clone(), self.room.host)
        await self.update_order()
        await self.update_current_item()

    @property
    def current_item(self):
        return self.room.playlist[self.current_index]


@dataclass
class CountdownInfo:
    countdown: MultiplayerCountdown
    duration: timedelta
    task: asyncio.Task | None = None

    def __init__(self, countdown: MultiplayerCountdown):
        self.countdown = countdown
        self.duration = (
            countdown.time_remaining if countdown.time_remaining > timedelta(seconds=0) else timedelta(seconds=0)
        )


class _MatchRequest(SignalRUnionMessage): ...


class ChangeTeamRequest(_MatchRequest):
    union_type: ClassVar[Literal[0]] = 0
    team_id: int


class StartMatchCountdownRequest(_MatchRequest):
    union_type: ClassVar[Literal[1]] = 1
    duration: timedelta


class StopCountdownRequest(_MatchRequest):
    union_type: ClassVar[Literal[2]] = 2
    id: int


MatchRequest = ChangeTeamRequest | StartMatchCountdownRequest | StopCountdownRequest


class MatchTypeHandler(ABC):
    def __init__(self, room: "ServerMultiplayerRoom"):
        self.room = room
        self.hub = room.hub

    @abstractmethod
    async def handle_join(self, user: MultiplayerRoomUser): ...

    @abstractmethod
    async def handle_request(self, user: MultiplayerRoomUser, request: MatchRequest): ...

    @abstractmethod
    async def handle_leave(self, user: MultiplayerRoomUser): ...

    @abstractmethod
    def get_details(self) -> MatchStartedEventDetail: ...


class HeadToHeadHandler(MatchTypeHandler):
    @override
    async def handle_join(self, user: MultiplayerRoomUser):
        if user.match_state is not None:
            user.match_state = None
            await self.hub.change_user_match_state(self.room, user)

    @override
    async def handle_request(self, user: MultiplayerRoomUser, request: MatchRequest): ...

    @override
    async def handle_leave(self, user: MultiplayerRoomUser): ...

    @override
    def get_details(self) -> MatchStartedEventDetail:
        detail = MatchStartedEventDetail(room_type="head_to_head", team=None)
        return detail


class TeamVersusHandler(MatchTypeHandler):
    @override
    def __init__(self, room: "ServerMultiplayerRoom"):
        super().__init__(room)
        self.state = TeamVersusRoomState()
        room.room.match_state = self.state
        task = asyncio.create_task(self.hub.change_room_match_state(self.room))
        self.hub.tasks.add(task)
        task.add_done_callback(self.hub.tasks.discard)

    def _get_best_available_team(self) -> int:
        for team in self.state.teams:
            if all(
                (
                    user.match_state is None
                    or not isinstance(user.match_state, TeamVersusUserState)
                    or user.match_state.team_id != team.id
                )
                for user in self.room.room.users
            ):
                return team.id

        from collections import defaultdict

        team_counts = defaultdict(int)
        for user in self.room.room.users:
            if user.match_state is not None and isinstance(user.match_state, TeamVersusUserState):
                team_counts[user.match_state.team_id] += 1

        if team_counts:
            min_count = min(team_counts.values())
            for team_id, count in team_counts.items():
                if count == min_count:
                    return team_id
        return self.state.teams[0].id if self.state.teams else 0

    @override
    async def handle_join(self, user: MultiplayerRoomUser):
        best_team_id = self._get_best_available_team()
        user.match_state = TeamVersusUserState(team_id=best_team_id)
        await self.hub.change_user_match_state(self.room, user)

    @override
    async def handle_request(self, user: MultiplayerRoomUser, request: MatchRequest):
        if not isinstance(request, ChangeTeamRequest):
            return

        if request.team_id not in [team.id for team in self.state.teams]:
            raise InvokeException("Invalid team ID")

        user.match_state = TeamVersusUserState(team_id=request.team_id)
        await self.hub.change_user_match_state(self.room, user)

    @override
    async def handle_leave(self, user: MultiplayerRoomUser): ...

    @override
    def get_details(self) -> MatchStartedEventDetail:
        teams: dict[int, Literal["blue", "red"]] = {}
        for user in self.room.room.users:
            if user.match_state is not None and isinstance(user.match_state, TeamVersusUserState):
                teams[user.user_id] = "blue" if user.match_state.team_id == 1 else "red"
        detail = MatchStartedEventDetail(room_type="team_versus", team=teams)
        return detail


MATCH_TYPE_HANDLERS = {
    MatchType.HEAD_TO_HEAD: HeadToHeadHandler,
    MatchType.TEAM_VERSUS: TeamVersusHandler,
}


@dataclass
class ServerMultiplayerRoom:
    room: MultiplayerRoom
    category: RoomCategory
    status: RoomStatus
    start_at: datetime
    hub: "MultiplayerHub"
    match_type_handler: MatchTypeHandler
    queue: MultiplayerQueue
    _next_countdown_id: int
    _countdown_id_lock: asyncio.Lock
    _tracked_countdown: dict[int, CountdownInfo]

    def __init__(
        self,
        room: MultiplayerRoom,
        category: RoomCategory,
        start_at: datetime,
        hub: "MultiplayerHub",
    ):
        self.room = room
        self.category = category
        self.status = RoomStatus.IDLE
        self.start_at = start_at
        self.hub = hub
        self.queue = MultiplayerQueue(self)
        self._next_countdown_id = 0
        self._countdown_id_lock = asyncio.Lock()
        self._tracked_countdown = {}

    async def set_handler(self):
        self.match_type_handler = MATCH_TYPE_HANDLERS[self.room.settings.match_type](self)
        for i in self.room.users:
            await self.match_type_handler.handle_join(i)

    async def get_next_countdown_id(self) -> int:
        async with self._countdown_id_lock:
            self._next_countdown_id += 1
            return self._next_countdown_id

    async def start_countdown(
        self,
        countdown: MultiplayerCountdown,
        on_complete: Callable[["ServerMultiplayerRoom"], Awaitable[Any]] | None = None,
    ):
        async def _countdown_task(self: "ServerMultiplayerRoom"):
            await asyncio.sleep(info.duration.total_seconds())
            if on_complete is not None:
                await on_complete(self)
            await self.stop_countdown(countdown)

        if countdown.is_exclusive:
            await self.stop_all_countdowns(countdown.__class__)
        countdown.id = await self.get_next_countdown_id()
        info = CountdownInfo(countdown)
        self.room.active_countdowns.append(info.countdown)
        self._tracked_countdown[countdown.id] = info
        await self.hub.send_match_event(self, CountdownStartedEvent(countdown=info.countdown))
        info.task = asyncio.create_task(_countdown_task(self))

    async def stop_countdown(self, countdown: MultiplayerCountdown):
        info = self._tracked_countdown.get(countdown.id)
        if info is None:
            return
        del self._tracked_countdown[countdown.id]
        self.room.active_countdowns.remove(countdown)
        await self.hub.send_match_event(self, CountdownStoppedEvent(id=countdown.id))
        if info.task is not None and not info.task.done():
            info.task.cancel()

    async def stop_all_countdowns(self, typ: type[MultiplayerCountdown]):
        for countdown in list(self._tracked_countdown.values()):
            if isinstance(countdown.countdown, typ):
                await self.stop_countdown(countdown.countdown)


class _MatchServerEvent(SignalRUnionMessage): ...


class CountdownStartedEvent(_MatchServerEvent):
    countdown: MultiplayerCountdown

    union_type: ClassVar[Literal[0]] = 0


class CountdownStoppedEvent(_MatchServerEvent):
    id: int

    union_type: ClassVar[Literal[1]] = 1


MatchServerEvent = CountdownStartedEvent | CountdownStoppedEvent


class GameplayAbortReason(IntEnum):
    LOAD_TOOK_TOO_LONG = 0
    HOST_ABORTED = 1


class MatchStartedEventDetail(TypedDict):
    room_type: Literal["playlists", "head_to_head", "team_versus"]
    team: dict[int, Literal["blue", "red"]] | None
