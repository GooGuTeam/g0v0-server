from __future__ import annotations

from abc import abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Self

from app.utils import truncate

from .achievement import Achievement
from .score import GameMode

from pydantic import BaseModel, PrivateAttr
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

CONTENT_TRUNCATE = 36

if TYPE_CHECKING:
    from app.database import ChannelType, ChatMessage, TeamRequest, User


# https://github.com/ppy/osu-web/blob/master/app/Models/Notification.php
class NotificationName(str, Enum):
    BEATMAP_OWNER_CHANGE = "beatmap_owner_change"
    BEATMAPSET_DISCUSSION_LOCK = "beatmapset_discussion_lock"
    BEATMAPSET_DISCUSSION_POST_NEW = "beatmapset_discussion_post_new"
    BEATMAPSET_DISCUSSION_QUALIFIED_PROBLEM = "beatmapset_discussion_qualified_problem"
    BEATMAPSET_DISCUSSION_REVIEW_NEW = "beatmapset_discussion_review_new"
    BEATMAPSET_DISCUSSION_UNLOCK = "beatmapset_discussion_unlock"
    BEATMAPSET_DISQUALIFY = "beatmapset_disqualify"
    BEATMAPSET_LOVE = "beatmapset_love"
    BEATMAPSET_NOMINATE = "beatmapset_nominate"
    BEATMAPSET_QUALIFY = "beatmapset_qualify"
    BEATMAPSET_RANK = "beatmapset_rank"
    BEATMAPSET_REMOVE_FROM_LOVED = "beatmapset_remove_from_loved"
    BEATMAPSET_RESET_NOMINATIONS = "beatmapset_reset_nominations"
    CHANNEL_ANNOUNCEMENT = "channel_announcement"
    CHANNEL_MESSAGE = "channel_message"
    CHANNEL_TEAM = "channel_team"
    COMMENT_NEW = "comment_new"
    FORUM_TOPIC_REPLY = "forum_topic_reply"
    TEAM_APPLICATION_ACCEPT = "team_application_accept"
    TEAM_APPLICATION_REJECT = "team_application_reject"
    TEAM_APPLICATION_STORE = "team_application_store"
    USER_ACHIEVEMENT_UNLOCK = "user_achievement_unlock"
    USER_BEATMAPSET_NEW = "user_beatmapset_new"
    USER_BEATMAPSET_REVIVE = "user_beatmapset_revive"

    # NAME_TO_CATEGORY
    @property
    def category(self) -> str:
        return {
            NotificationName.BEATMAP_OWNER_CHANGE: "beatmap_owner_change",
            NotificationName.BEATMAPSET_DISCUSSION_LOCK: "beatmapset_discussion",
            NotificationName.BEATMAPSET_DISCUSSION_POST_NEW: "beatmapset_discussion",
            NotificationName.BEATMAPSET_DISCUSSION_QUALIFIED_PROBLEM: "beatmapset_problem",
            NotificationName.BEATMAPSET_DISCUSSION_REVIEW_NEW: "beatmapset_discussion",
            NotificationName.BEATMAPSET_DISCUSSION_UNLOCK: "beatmapset_discussion",
            NotificationName.BEATMAPSET_DISQUALIFY: "beatmapset_state",
            NotificationName.BEATMAPSET_LOVE: "beatmapset_state",
            NotificationName.BEATMAPSET_NOMINATE: "beatmapset_state",
            NotificationName.BEATMAPSET_QUALIFY: "beatmapset_state",
            NotificationName.BEATMAPSET_RANK: "beatmapset_state",
            NotificationName.BEATMAPSET_REMOVE_FROM_LOVED: "beatmapset_state",
            NotificationName.BEATMAPSET_RESET_NOMINATIONS: "beatmapset_state",
            NotificationName.CHANNEL_ANNOUNCEMENT: "announcement",
            NotificationName.CHANNEL_MESSAGE: "channel",
            NotificationName.CHANNEL_TEAM: "channel_team",
            NotificationName.COMMENT_NEW: "comment",
            NotificationName.FORUM_TOPIC_REPLY: "forum_topic_reply",
            NotificationName.TEAM_APPLICATION_ACCEPT: "team_application",
            NotificationName.TEAM_APPLICATION_REJECT: "team_application",
            NotificationName.TEAM_APPLICATION_STORE: "team_application",
            NotificationName.USER_ACHIEVEMENT_UNLOCK: "user_achievement_unlock",
            NotificationName.USER_BEATMAPSET_NEW: "user_beatmapset_new",
            NotificationName.USER_BEATMAPSET_REVIVE: "user_beatmapset_new",
        }[self]


class NotificationDetail(BaseModel):
    name: ClassVar[NotificationName]

    @property
    @abstractmethod
    def object_type(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def object_id(self) -> int:
        raise NotImplementedError

    @property
    @abstractmethod
    def source_user_id(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def get_receivers(self, session: AsyncSession) -> list[int]:
        raise NotImplementedError


class ChannelMessageBase(NotificationDetail):
    title: str
    type: str
    cover_url: str

    _message: "ChatMessage" = PrivateAttr()
    _user: "User" = PrivateAttr()
    _receiver: list[int] = PrivateAttr()

    @classmethod
    def init(
        cls,
        message: "ChatMessage",
        user: "User",
        receiver: list[int],
        channel_type: "ChannelType",
    ) -> Self:
        try:
            avatar_url = (
                getattr(user, "avatar_url", "https://lazer-data.g0v0.top/default.jpg")
                or "https://lazer-data.g0v0.top/default.jpg"
            )
        except Exception:
            avatar_url = "https://lazer-data.g0v0.top/default.jpg"
        instance = cls(
            title=truncate(message.content, CONTENT_TRUNCATE),
            type=channel_type.value.lower(),
            cover_url=avatar_url,
        )
        instance._message = message
        instance._user = user
        instance._receiver = receiver
        return instance

    async def get_receivers(self, session: AsyncSession) -> list[int]:
        return self._receiver

    @property
    def source_user_id(self) -> int:
        return self._user.id

    @property
    def object_type(self) -> str:
        return "channel"

    @property
    def object_id(self) -> int:
        return self._message.channel_id


class ChannelMessage(ChannelMessageBase):
    name: ClassVar[NotificationName] = NotificationName.CHANNEL_MESSAGE


class ChannelMessageTeam(ChannelMessageBase):
    @classmethod
    def init(
        cls,
        message: "ChatMessage",
        user: "User",
    ) -> ChannelMessageTeam:
        from app.database import ChannelType

        return super().init(message, user, [], ChannelType.TEAM)

    name: ClassVar[NotificationName] = NotificationName.CHANNEL_TEAM

    async def get_receivers(self, session: AsyncSession) -> list[int]:
        from app.database import TeamMember

        user_team_id = (
            await session.exec(select(TeamMember.team_id).where(TeamMember.user_id == self._user.id))
        ).first()
        if not user_team_id:
            return []
        user_ids = (await session.exec(select(TeamMember.user_id).where(TeamMember.team_id == user_team_id))).all()
        return list(user_ids)


class UserAchievementUnlock(NotificationDetail):
    achievement_id: int
    achievement_mode: str
    cover_url: str
    slug: str
    title: str
    description: str
    user_id: int

    @classmethod
    def init(cls, achievement: Achievement, user_id: int, mode: "GameMode") -> Self:
        instance = cls(
            title=achievement.name,
            cover_url=achievement.url,
            slug=achievement.assets_id,
            achievement_id=achievement.id,
            achievement_mode=mode.value.lower(),
            description=achievement.desc,
            user_id=user_id,
        )
        return instance

    async def get_receivers(self, session: AsyncSession) -> list[int]:
        return [self.user_id]

    name: ClassVar[NotificationName] = NotificationName.USER_ACHIEVEMENT_UNLOCK

    @property
    def object_id(self) -> int:
        return self.achievement_id

    @property
    def source_user_id(self) -> int:
        return self.user_id

    @property
    def object_type(self) -> str:
        return "achievement"


class TeamApplicationBase(NotificationDetail):
    cover_url: str
    title: str

    _team_request: "TeamRequest" = PrivateAttr()

    @classmethod
    # TODO: 可能隐藏 MissingGreenlet 问题
    def init(cls, team_request: "TeamRequest") -> Self:
        instance = cls(
            title=team_request.team.name,
            cover_url=team_request.team.flag_url or "",
        )
        instance._team_request = team_request
        return instance

    async def get_receivers(self, session: AsyncSession) -> list[int]:
        return [self._team_request.user_id]

    @property
    def object_id(self) -> int:
        return self._team_request.team_id

    @property
    def source_user_id(self) -> int:
        return self._team_request.user_id

    @property
    def object_type(self) -> str:
        return "team"


class TeamApplicationAccept(TeamApplicationBase):
    name: ClassVar[NotificationName] = NotificationName.TEAM_APPLICATION_ACCEPT


class TeamApplicationReject(TeamApplicationBase):
    name: ClassVar[NotificationName] = NotificationName.TEAM_APPLICATION_REJECT


class TeamApplicationStore(TeamApplicationBase):
    name: ClassVar[NotificationName] = NotificationName.TEAM_APPLICATION_STORE

    async def get_receivers(self, session: AsyncSession) -> list[int]:
        return [self._team_request.team.leader_id]

    @classmethod
    def init(cls, team_request: "TeamRequest") -> Self:
        instance = cls(
            title=team_request.user.username,
            cover_url=team_request.team.flag_url or "",
        )
        instance._team_request = team_request
        return instance


NotificationDetails = (
    ChannelMessage
    | ChannelMessageTeam
    | UserAchievementUnlock
    | TeamApplicationAccept
    | TeamApplicationReject
    | TeamApplicationStore
)
