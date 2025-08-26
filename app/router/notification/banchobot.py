from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from math import ceil
import random
import shlex

from app.calculator import calculate_weighted_pp
from app.const import BANCHOBOT_ID
from app.database import ChatMessageResp
from app.database.beatmap import Beatmap
from app.database.chat import ChannelType, ChatChannel, ChatMessage, MessageType
from app.database.lazer_user import User
from app.database.score import Score, get_best_id
from app.database.statistics import UserStatistics, get_rank
from app.dependencies.fetcher import get_fetcher
from app.exception import InvokeException
from app.models.mods import APIMod, get_available_mods, mod_to_save
from app.models.multiplayer_hub import (
    ChangeTeamRequest,
    ServerMultiplayerRoom,
    StartMatchCountdownRequest,
)
from app.models.room import MatchType, QueueMode, RoomStatus
from app.models.score import GameMode
from app.signalr.hub import MultiplayerHubs
from app.signalr.hub.hub import Client

from .server import server

from httpx import HTTPError
from sqlalchemy.orm import joinedload
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

HandlerResult = str | None | Awaitable[str | None]
Handler = Callable[[User, list[str], AsyncSession, ChatChannel], HandlerResult]


class Bot:
    def __init__(self, bot_user_id: int = BANCHOBOT_ID) -> None:
        self._handlers: dict[str, Handler] = {}
        self.bot_user_id = bot_user_id

    # decorator: @bot.command("ping")
    def command(self, name: str) -> Callable[[Handler], Handler]:
        def _decorator(func: Handler) -> Handler:
            self._handlers[name.lower()] = func
            return func

        return _decorator

    def parse(self, content: str) -> tuple[str, list[str]] | None:
        if not content or not content.startswith("!"):
            return None
        try:
            parts = shlex.split(content[1:])
        except ValueError:
            parts = content[1:].split()
        if not parts:
            return None
        cmd = parts[0].lower()
        args = parts[1:]
        return cmd, args

    async def try_handle(
        self,
        user: User,
        channel: ChatChannel,
        content: str,
        session: AsyncSession,
    ) -> None:
        parsed = self.parse(content)
        if not parsed:
            return
        cmd, args = parsed
        handler = self._handlers.get(cmd)

        reply: str | None = None
        if handler is None:
            return
        else:
            try:
                res = handler(user, args, session, channel)
                if asyncio.iscoroutine(res):
                    res = await res
                reply = res  # type: ignore[assignment]
            except Exception:
                reply = "Unknown error occured."
        if reply:
            await self._send_reply(user, channel, reply, session)

    async def _send_message(self, channel: ChatChannel, content: str, session: AsyncSession) -> None:
        bot = await session.get(User, self.bot_user_id)
        if bot is None:
            return
        channel_id = channel.channel_id
        if channel_id is None:
            return

        msg = ChatMessage(
            channel_id=channel_id,
            content=content,
            sender_id=bot.id,
            type=MessageType.PLAIN,
        )
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        await session.refresh(bot)
        resp = await ChatMessageResp.from_db(msg, session, bot)
        await server.send_message_to_channel(resp)

    async def _ensure_pm_channel(self, user: User, session: AsyncSession) -> ChatChannel | None:
        user_id = user.id
        if user_id is None:
            return None

        bot = await session.get(User, self.bot_user_id)
        if bot is None or bot.id is None:
            return None

        channel = await ChatChannel.get_pm_channel(user_id, bot.id, session)
        if channel is None:
            channel = ChatChannel(
                name=f"pm_{user_id}_{bot.id}",
                description="Private message channel",
                type=ChannelType.PM,
            )
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
            await session.refresh(user)
            await session.refresh(bot)
        await server.batch_join_channel([user, bot], channel, session)
        return channel

    async def _send_reply(
        self,
        user: User,
        src_channel: ChatChannel,
        content: str,
        session: AsyncSession,
    ) -> None:
        target_channel = src_channel
        if src_channel.type == ChannelType.PUBLIC:
            pm = await self._ensure_pm_channel(user, session)
            if pm is not None:
                target_channel = pm
        await self._send_message(target_channel, content, session)


bot = Bot()


@bot.command("help")
async def _help(user: User, args: list[str], _session: AsyncSession, channel: ChatChannel) -> str:
    cmds = sorted(bot._handlers.keys())
    if args:
        target = args[0].lower()
        if target in bot._handlers:
            return f"Usage: !{target} [args]"
        return f"No such command: {target}"
    if not cmds:
        return "No available commands"
    return "Available: " + ", ".join(f"!{c}" for c in cmds)


@bot.command("roll")
def _roll(user: User, args: list[str], _session: AsyncSession, channel: ChatChannel) -> str:
    if len(args) > 0 and args[0].isdigit():
        r = random.randint(1, int(args[0]))
    else:
        r = random.randint(1, 100)
    return f"{user.username} rolls {r} point(s)"


@bot.command("stats")
async def _stats(user: User, args: list[str], session: AsyncSession, channel: ChatChannel) -> str:
    if len(args) >= 1:
        target_user = (await session.exec(select(User).where(User.username == args[0]))).first()
        if not target_user:
            return f"User '{args[0]}' not found."
    else:
        target_user = user

    gamemode = None
    if len(args) >= 2:
        gamemode = GameMode.parse(args[1].upper())
    if gamemode is None:
        subquery = select(func.max(Score.id)).where(Score.user_id == target_user.id).scalar_subquery()
        last_score = (await session.exec(select(Score).where(Score.id == subquery))).first()
        if last_score is not None:
            gamemode = last_score.gamemode
        else:
            gamemode = target_user.playmode

    statistics = (
        await session.exec(
            select(UserStatistics).where(
                UserStatistics.user_id == target_user.id,
                UserStatistics.mode == gamemode,
            )
        )
    ).first()
    if not statistics:
        return f"User '{args[0]}' has no statistics."

    return f"""Stats for {target_user.username} ({gamemode.name.lower()}):
Score: {statistics.total_score} (#{await get_rank(session, statistics)})
Plays: {statistics.play_count} (lv{ceil(statistics.level_current)})
Accuracy: {statistics.hit_accuracy:.2%}
PP: {statistics.pp:.2f}
"""


async def _mp_name(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    if len(args) < 1:
        return "Usage: !mp name <name>"

    name = args[0]
    try:
        settings = room.room.settings.model_copy()
        settings.name = name
        await MultiplayerHubs.ChangeSettings(signalr_client, settings)
        return f"Room name has changed to {name}"
    except InvokeException as e:
        return e.message


async def _mp_set(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    if len(args) < 1:
        return "Usage: !mp set <teammode> [<queuemode>]"

    teammode = {"0": MatchType.HEAD_TO_HEAD, "2": MatchType.TEAM_VERSUS}.get(args[0])
    if not teammode:
        return "Invalid teammode. Use 0 for Head-to-Head or 2 for Team Versus."
    queuemode = (
        {
            "0": QueueMode.HOST_ONLY,
            "1": QueueMode.ALL_PLAYERS,
            "2": QueueMode.ALL_PLAYERS_ROUND_ROBIN,
        }.get(args[1])
        if len(args) >= 2
        else None
    )
    try:
        settings = room.room.settings.model_copy()
        settings.match_type = teammode
        if queuemode:
            settings.queue_mode = queuemode
        await MultiplayerHubs.ChangeSettings(signalr_client, settings)
        return f"Room setting 'teammode' has been changed to {teammode.name.lower()}"
    except InvokeException as e:
        return e.message


async def _mp_host(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    if len(args) < 1:
        return "Usage: !mp host <username>"

    username = args[0]
    user_id = (await session.exec(select(User.id).where(User.username == username))).first()
    if not user_id:
        return f"User '{username}' not found."

    try:
        await MultiplayerHubs.TransferHost(signalr_client, user_id)
        return f"User '{username}' has been hosted in the room."
    except InvokeException as e:
        return e.message


async def _mp_start(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    timer = None
    if len(args) >= 1 and args[0].isdigit():
        timer = int(args[0])

    try:
        if timer is not None:
            await MultiplayerHubs.SendMatchRequest(
                signalr_client,
                StartMatchCountdownRequest(duration=timedelta(seconds=timer)),
            )
            return ""
        else:
            await MultiplayerHubs.StartMatch(signalr_client)
            return "Good luck! Enjoy game!"
    except InvokeException as e:
        return e.message


async def _mp_abort(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    try:
        await MultiplayerHubs.AbortMatch(signalr_client)
        return "Match aborted."
    except InvokeException as e:
        return e.message


async def _mp_team(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
):
    if room.room.settings.match_type != MatchType.TEAM_VERSUS:
        return "This command is only available in Team Versus mode."

    if len(args) < 2:
        return "Usage: !mp team <username> <colour>"

    username = args[0]
    team = {"red": 0, "blue": 1}.get(args[1])
    if team is None:
        return "Invalid team colour. Use 'red' or 'blue'."

    user_id = (await session.exec(select(User.id).where(User.username == username))).first()
    if not user_id:
        return f"User '{username}' not found."
    user_client = MultiplayerHubs.get_client_by_id(str(user_id))
    if not user_client:
        return f"User '{username}' is not in the room."
    assert room.room.host
    if user_client.user_id != signalr_client.user_id and room.room.host.user_id != signalr_client.user_id:
        return "You are not allowed to change other users' teams."

    try:
        await MultiplayerHubs.SendMatchRequest(user_client, ChangeTeamRequest(team_id=team))
        return ""
    except InvokeException as e:
        return e.message


async def _mp_password(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    password = ""
    if len(args) >= 1:
        password = args[0]

    try:
        settings = room.room.settings.model_copy()
        settings.password = password
        await MultiplayerHubs.ChangeSettings(signalr_client, settings)
        return "Room password has been set."
    except InvokeException as e:
        return e.message


async def _mp_kick(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    if len(args) < 1:
        return "Usage: !mp kick <username>"

    username = args[0]
    user_id = (await session.exec(select(User.id).where(User.username == username))).first()
    if not user_id:
        return f"User '{username}' not found."

    try:
        await MultiplayerHubs.KickUser(signalr_client, user_id)
        return f"User '{username}' has been kicked from the room."
    except InvokeException as e:
        return e.message


async def _mp_map(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    if len(args) < 1:
        return "Usage: !mp map <mapid> [<playmode>]"

    if room.status != RoomStatus.IDLE:
        return "Cannot change map while the game is running."

    map_id = args[0]
    if not map_id.isdigit():
        return "Invalid map ID."
    map_id = int(map_id)
    playmode = GameMode.parse(args[1].upper()) if len(args) >= 2 else None
    if playmode not in (
        GameMode.OSU,
        GameMode.TAIKO,
        GameMode.FRUITS,
        GameMode.MANIA,
        None,
    ):
        return "Invalid playmode."

    try:
        beatmap = await Beatmap.get_or_fetch(session, await get_fetcher(), bid=map_id)
        if beatmap.mode != GameMode.OSU and playmode and playmode != beatmap.mode:
            return f"Cannot convert to {playmode.value}. Original mode is {beatmap.mode.value}."
    except HTTPError:
        return "Beatmap not found"

    try:
        current_item = room.queue.current_item
        item = current_item.model_copy(deep=True)
        item.owner_id = signalr_client.user_id
        item.beatmap_checksum = beatmap.checksum
        item.required_mods = []
        item.allowed_mods = []
        item.freestyle = False
        item.beatmap_id = map_id
        if playmode is not None:
            item.ruleset_id = int(playmode)
        if item.expired:
            item.id = 0
            item.expired = False
            item.played_at = None
            await MultiplayerHubs.AddPlaylistItem(signalr_client, item)
        else:
            await MultiplayerHubs.EditPlaylistItem(signalr_client, item)
        return ""
    except InvokeException as e:
        return e.message


async def _mp_mods(
    signalr_client: Client,
    room: ServerMultiplayerRoom,
    args: list[str],
    session: AsyncSession,
) -> str:
    if len(args) < 1:
        return "Usage: !mp mods <mod1> [<mod2> ...]"

    if room.status != RoomStatus.IDLE:
        return "Cannot change mods while the game is running."

    required_mods = []
    allowed_mods = []
    freestyle = False
    freemod = False
    for arg in args:
        arg = arg.upper()
        if arg == "NONE":
            required_mods.clear()
            allowed_mods.clear()
            break
        elif arg == "FREESTYLE":
            freestyle = True
        elif arg == "FREEMOD":
            freemod = True
        elif arg.startswith("+"):
            mod = arg.removeprefix("+")
            if len(mod) != 2:
                return f"Invalid mod: {mod}."
            allowed_mods.append(APIMod(acronym=mod))
        else:
            if len(arg) != 2:
                return f"Invalid mod: {arg}."
            required_mods.append(APIMod(acronym=arg))

    try:
        current_item = room.queue.current_item
        item = current_item.model_copy(deep=True)
        item.owner_id = signalr_client.user_id
        item.freestyle = freestyle
        if freestyle:
            item.allowed_mods = []
        elif freemod:
            item.allowed_mods = get_available_mods(current_item.ruleset_id, required_mods)
        else:
            item.allowed_mods = allowed_mods
        item.required_mods = required_mods
        if item.expired:
            item.id = 0
            item.expired = False
            item.played_at = None
            await MultiplayerHubs.AddPlaylistItem(signalr_client, item)
        else:
            await MultiplayerHubs.EditPlaylistItem(signalr_client, item)
        return ""
    except InvokeException as e:
        return e.message


_MP_COMMANDS = {
    "name": _mp_name,
    "set": _mp_set,
    "host": _mp_host,
    "start": _mp_start,
    "abort": _mp_abort,
    "map": _mp_map,
    "mods": _mp_mods,
    "kick": _mp_kick,
    "password": _mp_password,
    "team": _mp_team,
}
_MP_HELP = """!mp name <name>
!mp set <teammode> [<queuemode>]
!mp host <host>
!mp start [<timer>]
!mp abort
!mp map <map> [<playmode>]
!mp mods <mod1> [<mod2> ...]
!mp kick <user>
!mp password [<password>]
!mp team <user> <team:red|blue>"""


@bot.command("mp")
async def _mp(user: User, args: list[str], session: AsyncSession, channel: ChatChannel):
    if not channel.name.startswith("room_"):
        return

    room_id = int(channel.name[5:])
    room = MultiplayerHubs.rooms.get(room_id)
    if not room:
        return
    signalr_client = MultiplayerHubs.get_client_by_id(str(user.id))
    if not signalr_client:
        return

    if len(args) < 1:
        return f"Usage: !mp <{'|'.join(_MP_COMMANDS.keys())}> [args]"

    command = args[0].lower()
    if command not in _MP_COMMANDS:
        return f"No such command: {command}"

    return await _MP_COMMANDS[command](signalr_client, room, args[1:], session)


async def _score(
    user_id: int,
    session: AsyncSession,
    include_fail: bool = False,
    gamemode: GameMode | None = None,
) -> str:
    q = select(Score).where(Score.user_id == user_id).order_by(col(Score.id).desc()).options(joinedload(Score.beatmap))
    if not include_fail:
        q = q.where(col(Score.passed).is_(True))
    if gamemode is not None:
        q = q.where(Score.gamemode == gamemode)

    score = (await session.exec(q)).first()
    if score is None:
        return "You have no scores."
    best_id = await get_best_id(session, score.id)
    bp_pp = ""
    if best_id:
        bp_pp = f"(b{best_id} -> {calculate_weighted_pp(score.pp, best_id - 1):.2f}pp)"

    result = f"""{score.beatmap.beatmapset.title} [{score.beatmap.version}] ({score.gamemode.name.lower()})
Played at {score.started_at}
{score.pp:.2f}pp {bp_pp} {score.accuracy:.2%} {",".join(mod_to_save(score.mods))} {score.rank.name.upper()}
Great: {score.n300}, Good: {score.n100}, Meh: {score.n50}, Miss: {score.nmiss}"""
    if score.gamemode == GameMode.MANIA:
        keys = next((mod["acronym"] for mod in score.mods if mod["acronym"].endswith("K")), None)
        if keys is None:
            keys = f"{int(score.beatmap.cs)}K"
        p_d_g = f"{score.ngeki / score.n300:.2f}:1" if score.n300 > 0 else "inf:1"
        result += f"\nKeys: {keys}, Perfect: {score.ngeki}, Ok: {score.nkatu}, P/G: {p_d_g}"
    return result


@bot.command("re")
async def _re(user: User, args: list[str], session: AsyncSession, channel: ChatChannel):
    gamemode = None
    if len(args) >= 1:
        gamemode = GameMode.parse(args[0])
    return await _score(user.id, session, include_fail=True, gamemode=gamemode)


@bot.command("pr")
async def _pr(user: User, args: list[str], session: AsyncSession, channel: ChatChannel):
    gamemode = None
    if len(args) >= 1:
        gamemode = GameMode.parse(args[0])
    return await _score(user.id, session, include_fail=False, gamemode=gamemode)
