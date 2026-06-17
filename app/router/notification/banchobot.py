"""BanchoBot module for handling chat bot commands.

This module implements a simple command-based chat bot that responds
to user commands prefixed with '!' in chat channels.
"""

import asyncio
from collections.abc import Awaitable, Callable
from math import ceil
import random
import re
import secrets
import shlex
from typing import TYPE_CHECKING, cast

from app.calculating import calculate_weighted_pp
from app.const import BANCHOBOT_ID
from app.database.chat import ChannelType, ChatChannel, ChatMessage, ChatMessageModel, MessageType
from app.database.room import Room
from app.database.score import Score, get_best_id
from app.database.statistics import UserStatistics, get_rank
from app.database.user import User
from app.log import log
from app.models.mods import mod_to_save
from app.models.room import MatchType, RoomCategory
from app.models.score import GameMode
from app.service.multiplayer_event_dispatcher import multiplayer_event_dispatcher
from app.service.room import create_playlist_room

from .server import server

from sqlalchemy.ext.asyncio import async_object_session
from sqlalchemy.orm import joinedload
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    pass

HandlerResult = str | None | Awaitable[str | None]
Handler = Callable[[User, list[str], AsyncSession, ChatChannel], HandlerResult]

# According to osu!wiki
MAXIMUM_ACTIVE_ROOMS: int = 4

logger = log("Bot")


class Bot:
    """Chat bot handler for processing user commands.

    Handles commands prefixed with '!' and routes them to registered handlers.

    Attributes:
        bot_user_id: The user ID of the bot account.
    """

    def __init__(self, bot_user_id: int = BANCHOBOT_ID) -> None:
        """Initialize the bot with a user ID.

        Args:
            bot_user_id: The database user ID for the bot.
        """
        self._handlers: dict[str, Handler] = {}
        self.bot_user_id = bot_user_id

    def command(self, name: str) -> Callable[[Handler], Handler]:
        """Decorator to register a command handler.

        Args:
            name: The command name (without the '!' prefix).

        Returns:
            Decorator function that registers the handler.
        """

        def _decorator(func: Handler) -> Handler:
            self._handlers[name.lower()] = func
            return func

        return _decorator

    def parse(self, content: str) -> tuple[str, list[str]] | None:
        """Parse a message for a command.

        Args:
            content: The message content to parse.

        Returns:
            Tuple of (command_name, arguments) if valid command, None otherwise.
        """
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

    @staticmethod
    def make_link(url: str, text: str = "") -> str:
        """Format a message with a link.

        Args:
            url: The URL of the link.
            text: The display text for the link.

        Returns:
            Formatted string with the link.
        """
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        if not text:
            return f"{url}"
        return f"[{url} {text}]"

    async def try_handle(
        self,
        user: User,
        channel: ChatChannel,
        content: str,
        session: AsyncSession,
    ) -> None:
        """Attempt to handle a message as a bot command.

        Args:
            user: The user who sent the message.
            channel: The chat channel where the message was sent.
            content: The message content.
            session: Database session for queries.
        """
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
            await self.send_reply(user, reply, session, src_channel=channel)

    async def send_message(self, channel: ChatChannel, content: str, session: AsyncSession | None = None) -> None:
        """Send a message from the bot to a channel.

        Args:
            channel: Target chat channel.
            content: Message content to send.
            session: Database session.
        """
        if session is None:
            session = cast(AsyncSession, async_object_session(channel))

        channel_id = channel.channel_id
        if channel_id is None:
            return

        msg = ChatMessage(
            channel_id=channel_id,
            content=content,
            sender_id=self.bot_user_id,
            type=MessageType.PLAIN,
        )
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        resp = await ChatMessageModel.transform(msg, includes=["sender"])
        await server.send_message_to_channel(resp)

    async def _ensure_pm_channel(self, user: User, session: AsyncSession) -> ChatChannel | None:
        """Ensure a PM channel exists between the bot and a user.

        Args:
            user: The user to create/get PM channel with.
            session: Database session.

        Returns:
            The PM channel if successful, None otherwise.
        """
        user_id = user.id
        if user_id is None:
            return None

        bot = await session.get(User, self.bot_user_id)
        if bot is None or bot.id is None:
            return None

        channel = await ChatChannel.get_pm_channel(user_id, bot.id, session)
        if channel is None:
            channel = ChatChannel(
                channel_name=f"pm_{user_id}_{bot.id}",
                description="Private message channel",
                type=ChannelType.PM,
            )
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
            await session.refresh(user)
            await session.refresh(bot)
        await server.batch_join_channel([user, bot], channel)
        return channel

    async def send_reply(
        self,
        user: User | int,
        content: str,
        session: AsyncSession | None = None,
        *,
        src_channel: ChatChannel | None = None,
    ) -> None:
        """Send a reply to a user, using PM for public channels.

        Args:
            user: The user to reply to.
            content: Reply message content.
            session: Database session.
            src_channel: The source channel of the original message.
        """
        if isinstance(user, int):
            if session is None:
                raise ValueError("Session is required when user is an ID")
            target = await session.get(User, user)
            if target is None:
                raise ValueError(f"User with ID {user} not found for bot reply")
        else:
            target = user

        if session is None:
            session = cast(AsyncSession, async_object_session(target))

        if src_channel is None or src_channel.type == ChannelType.PUBLIC:
            pm = await self._ensure_pm_channel(target, session)
            if pm is not None:
                target_channel = pm
            else:
                raise RuntimeError("Failed to get or create PM channel for bot reply")
        else:
            target_channel = src_channel
        await self.send_message(target_channel, content, session)


bot = Bot()


@bot.command("help")
async def _help(user: User, args: list[str], _session: AsyncSession, channel: ChatChannel) -> str:
    """Show available commands or usage for a specific command."""
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
    """Roll a random number between 1 and the specified max (default 100)."""
    r = random.randint(1, int(args[0])) if len(args) > 0 and args[0].isdigit() else random.randint(1, 100)
    return f"{user.username} rolls {r} point(s)"


@bot.command("stats")
async def _stats(user: User, args: list[str], session: AsyncSession, channel: ChatChannel) -> str:
    """Show statistics for a user in a specific game mode."""
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
        gamemode = last_score.gamemode if last_score is not None else target_user.playmode

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


async def _score(
    user_id: int,
    session: AsyncSession,
    include_fail: bool = False,
    gamemode: GameMode | None = None,
) -> str:
    """Get the most recent score for a user.

    Args:
        user_id: The user's database ID.
        session: Database session.
        include_fail: Whether to include failed scores.
        gamemode: Optional game mode filter.

    Returns:
        Formatted string with score details.
    """
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
    """Show the user's most recent score (including failed attempts)."""
    gamemode = None
    if len(args) >= 1:
        gamemode = GameMode.parse(args[0])
    return await _score(user.id, session, include_fail=True, gamemode=gamemode)


@bot.command("pr")
async def _pr(user: User, args: list[str], session: AsyncSession, channel: ChatChannel):
    """Show the user's most recent passed score."""
    gamemode = None
    if len(args) >= 1:
        gamemode = GameMode.parse(args[0])
    return await _score(user.id, session, include_fail=False, gamemode=gamemode)


@bot.command("mp")
async def _mp(user: User, args: list[str], session: AsyncSession, channel: ChatChannel) -> str | None:
    """Multiplayer command support.

    Reference:
        https://osu.ppy.sh/wiki/osu%21_tournament_client/osu%21tourney/Tournament_management_commands
    """

    unsupported_subs = {"makeprivate", "clearhost"}

    if await user.is_restricted(session):
        return "You are restricted from using messaging commands."

    if not args:
        return "Usage: !mp <subcommand> [args]. Try !mp help"

    sub = args[0].lower()

    async def _reply_via_pm(content: str) -> str | None:
        pm = await bot._ensure_pm_channel(user, session)
        if pm is not None:
            await bot.send_message(pm, content, session)
            return None
        return content

    if sub in unsupported_subs:
        return await _reply_via_pm(f"!mp {sub} is currently not supported.")

    # Outside multiplayer channels, support only !mp help / make
    if channel.type != ChannelType.MULTIPLAYER and sub not in ("help", "make"):
        return await _reply_via_pm(
            f"!mp {sub} can only be used in multiplayer rooms. "
            "Outside multiplayer rooms, only !mp help and !mp make are available."
        )

    # TODO: Support makeprivate
    if sub == "make":
        if len(args) < 2:
            return "Usage: !mp make <name>"

        active_rooms_count = (
            await session.exec(
                select(func.count())
                .select_from(Room)
                .where(Room.host_id == user.id, Room.category == RoomCategory.REALTIME, col(Room.ends_at).is_(None))
            )
        ).one()

        if active_rooms_count >= MAXIMUM_ACTIVE_ROOMS:
            return await _reply_via_pm("You've reached the maximum active tournament rooms limit.")

        name = " ".join(args[1:])
        category = RoomCategory.REALTIME
        db_room = await create_playlist_room(session, name=name, host_id=user.id, category=category)
        db_room.ends_at = None
        db_room.password = secrets.token_hex(4)
        session.add(db_room)
        await session.commit()
        return f"Created room {db_room.id} (channel: room_{db_room.id})"

    if sub == "help":
        help_text = (
            "Available !mp commands:\n"
            "!mp make <name>\n"
            "!mp lock, !mp unlock\n"
            "!mp set <team-mode>\n"
            "!mp name <room-name>\n"
            "For team-mode: 0: Head to Head, 2: Team VS\n"
            "!mp invite <username>\n"
            "!mp settings\n"
            "!mp team <username> <red|blue>\n"
            "!mp size <size>\n"
            "!mp move <username> <slot>\n"
            "!mp start [time=10], !mp abort\n"
            "!mp timer [time=30], !mp aborttimer\n"
            "!mp kick <username>\n"
            "!mp host <username>\n"
            "!mp listrefs\n"
            "!mp addref <username>, !mp removeref <username>\n"
            "!mp map <map-id> <ruleset-id>\n"
            "!mp mods [acronym,...]\n"
            "!mp password [password]\n"
            "!mp close\n"
            "View the server documentation for details."
        )

        if channel.type == ChannelType.MULTIPLAYER:
            return help_text
        else:
            return await _reply_via_pm(help_text)

    # Extract room id from channel name (room_123 or mp_123)
    chan_name = channel.channel_name or ""
    m = re.search(r"(?:room|mp)_(\d+)", chan_name)
    if not m:
        return "Could not determine room id from channel."
    room_id = int(m.group(1))

    room = await session.get(Room, room_id)
    if not room:
        return "Room not found."

    def _normalize_username(raw: str) -> str:
        """Returns a username with all underscores replaced by spaces."""
        return raw.replace("_", " ")

    async def _resolve_user(raw: str) -> User | None:
        if raw.startswith("#") and raw[1:].isdigit():
            return await session.get(User, int(raw[1:]))
        return (
            await session.exec(select(User).where(User.username == raw or User.username == _normalize_username(raw)))
        ).first()

    # Permissions are checked spectator-side

    # TODO: Refactor, and move to spec-side if possible
    if sub == "settings":
        host = await room.awaitable_attrs.host
        playlist = await room.awaitable_attrs.playlist
        has_pw = bool(room.password) if hasattr(room, "password") else False
        return (
            f"Room {room.id}: {room.name} | Host: {getattr(host, 'username', room.host_id)} | "
            f"Status: {room.status} | Participants: {room.participant_count} | "
            f"Playlist: {len(playlist)} | {'Password protected' if has_pw else 'No password'}"
        )

    if sub == "name":
        if len(args) < 2:
            return "Usage: !mp name <title>"
        new_name = " ".join(args[1:]).strip()

        res = await multiplayer_event_dispatcher.post_change_room_settings(
            room_id,
            user.id,
            name=new_name,
        )
        return res.message or f'Room name updated to "{new_name}".'

    if sub == "close":
        res = await multiplayer_event_dispatcher.post_close_room(room.id, user.id)
        return res.message or "This room is going to be closed."

    if sub == "invite":
        if len(args) < 2:
            return "Usage: !mp invite <username|#<userid>>"
        target = await _resolve_user(args[1])
        if not target:
            return "Target user not found."

        res = await multiplayer_event_dispatcher.post_invite_user(room_id, target.id, user.id)
        return res.message or f"Invitation sent to {target.username}."

    if sub in ("lock", "unlock"):
        res = await multiplayer_event_dispatcher.post_set_lock_state(room_id, sub == "lock", user.id)
        return res.message or f"Room is now {'locked' if sub == 'lock' else 'unlocked'}."

    if sub == "host":
        if len(args) < 2:
            return "Usage: !mp host <username|#<userid>>"
        target = await _resolve_user(args[1])
        if not target:
            return "Target user not found."

        res = await multiplayer_event_dispatcher.post_transfer_host(room_id, target.id, user.id)
        return res.message or f"Host transferred to {target.username}."

    if sub == "password":
        # Bancho behavior: no argument clears password.
        password = args[1] if len(args) >= 2 else ""

        res = await multiplayer_event_dispatcher.post_change_room_settings(
            room_id,
            user.id,
            password=password,
        )
        return res.message or ("Room password removed." if password == "" else "Room password updated.")

    if sub == "size":
        if len(args) < 2 or not args[1].isdigit():
            return "Usage: !mp size <size>"

        new_size = int(args[1])
        if new_size <= 2:
            return "Invalid room size."

        res = await multiplayer_event_dispatcher.post_change_room_settings(room_id, user.id, max_participants=new_size)
        return res.message or f"Changed room size to {new_size}."

    if sub == "set":
        if len(args) != 2:
            return "Only single-argument teammode is supported: !mp set <0|2>"
        teammode_raw = args[1]
        if teammode_raw not in ("0", "2"):
            return "Unsupported teammode. Currently supported: 0 (Head to Head), 2 (Team VS)."

        res = await multiplayer_event_dispatcher.post_change_room_settings(
            room_id,
            user.id,
            match_type=int(teammode_raw),
        )
        return res.message or f"Team mode updated to {teammode_raw}."

    if sub == "move":
        if len(args) != 3 or not args[2].isdigit():
            return "!mp move <username> <slot>"
        target = await _resolve_user(args[1])
        if not target:
            return "Target user not found."

        new_slot = int(args[2])
        if new_slot < 0:
            return "Invalid slot number."

        res = await multiplayer_event_dispatcher.post_set_slot(room_id, target.id, user.id, new_slot)
        return res.message or f"Moved user {target.username} to slot #{new_slot}."

    if sub == "team":
        if len(args) != 3:
            return "Usage: !mp team <username|#<userid>> <red|blue>"
        if room.type == MatchType.HEAD_TO_HEAD:
            return "!mp team is not supported in Head to Head mode."
        if room.type != MatchType.TEAM_VERSUS:
            return "!mp team is only supported in Team VS mode."

        target = await _resolve_user(args[1])
        if not target:
            return f"User {args[1]} not found."

        colour = args[2].lower()
        if colour not in ("red", "blue"):
            return "Unsupported team colour. Use 'red' or 'blue'."

        team_id = 0 if colour == "red" else 1

        res = await multiplayer_event_dispatcher.post_change_team(room_id, target.id, user.id, team_id)
        return res.message or f"Moved user {target.username} to team {colour}."

    if sub in ("start", "abort"):
        # start [time] -> request spectator to start match (with optional countdown)
        if sub == "start":
            delay = None
            if len(args) >= 2:
                if not args[1].isdigit():
                    return "Usage: !mp start [<time_in_seconds>]"
                delay = int(args[1])

            res = await multiplayer_event_dispatcher.post_start_match(room_id, delay, user.id)
            msg = "Match is starting. Good luck!" if delay is None else f"Match would start in {delay} seconds."
            return res.message or msg

        res = await multiplayer_event_dispatcher.post_abort_match(room_id, user.id)
        return res.message or "Aborted the match."

    if sub in ("timer", "aborttimer"):
        if sub == "aborttimer":
            res = await multiplayer_event_dispatcher.post_stop_all_countdowns(room_id, user.id)
            return res.message or "All timers stopped."

        # Start a reminder-only countdown (no match start). Default 30s, mutually exclusive with match start countdown.
        seconds = 30
        if len(args) >= 2:
            if not args[1].isdigit():
                return "Usage: !mp timer [<time_in_seconds>]"
            seconds = int(args[1])

        res = await multiplayer_event_dispatcher.post_start_reminder_timer(room_id, seconds, user.id)
        return res.message or f"Started a countdown of {seconds} {'second' if seconds == 1 else 'seconds'}."

    if sub in ("kick", "ban"):
        if len(args) < 2:
            return f"Usage: !mp {sub} <username|#<userid>>"
        target = args[1]
        tgt_user = await _resolve_user(target)
        if not tgt_user:
            return f"User {target} not found."

        if sub == "kick":
            res = await multiplayer_event_dispatcher.post_kick_player(room_id, tgt_user.id, user.id)
        else:
            if tgt_user.id == user.id:
                return "You cannot ban yourself."
            res = await multiplayer_event_dispatcher.post_ban_user(room_id, tgt_user.id, user.id)

        return res.message or f"{sub.title()} request sent for {tgt_user.username}."

    if sub in ("addref", "removeref"):
        if len(args) < 2:
            return f"Usage: !mp {sub} <username|#<userid>>"

        target = await _resolve_user(args[1])
        if not target:
            return f"User {args[1]} not found."

        if target.id == user.id:
            return f"You cannot {sub.removesuffix('ref')} yourself as a referee."

        is_add = sub == "addref"

        if is_add:
            res = await multiplayer_event_dispatcher.post_add_referee(room_id, target.id, user.id)
        else:
            res = await multiplayer_event_dispatcher.post_remove_referee(room_id, target.id, user.id)

        prompt = f"Added {target.username} to referees." if is_add else f"Removed {target.username} from referees."
        return res.message or prompt

    if sub == "listrefs":
        res = await multiplayer_event_dispatcher.post_get_referees(room_id, user.id)

        if not res.details.referee_ids:
            return "Unable to get referees."

        if not res.success:
            return res.message

        ref_ids: set[int] = res.details.referee_ids

        if not ref_ids:
            return "No referees found for this room."

        # We want usernames as output.
        ref_users = (await session.exec(select(User).where(col(User.id).in_(sorted(ref_ids))))).all()
        if not ref_users:
            return "No referees found for this room."

        names = ", ".join(sorted(u.username for u in ref_users))
        return f"Referees ({len(ref_users)}): {names}"

    return "Unknown mp subcommand. Send !mp help for usage."
