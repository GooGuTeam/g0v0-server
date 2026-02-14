"""User preference database models.

This module stores user preferences for UI settings, audio,
beatmap downloads, and profile customization.
"""

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlmodel import JSON, BigInteger, Column, Field, ForeignKey, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User

DEFAULT_ORDER = [
    "me",
    "recent_activity",
    "top_ranks",
    "medals",
    "historical",
    "beatmaps",
    "kudosu",
]
"""Default profile section order."""


class BeatmapCardSize(StrEnum):
    """Beatmap card size options."""

    NORMAL = "normal"
    EXTRA = "extra"


class BeatmapDownload(StrEnum):
    """Beatmap download options."""

    ALL = "all"
    NO_VIDEO = "no_video"
    direct = "direct"


class ScoringMode(StrEnum):
    """Score display mode options."""

    STANDARDISED = "standardised"
    CLASSIC = "classic"


class UserListFilter(StrEnum):
    """User list filter options."""

    ALL = "all"
    ONLINE = "online"
    OFFLINE = "offline"


class UserListSort(StrEnum):
    """User list sort options."""

    LAST_VISIT = "last_visit"
    RANK = "rank"
    USERNAME = "username"


class UserListView(StrEnum):
    """User list view mode options."""

    CARD = "card"
    LIST = "list"
    BRICK = "brick"


class UserPreference(SQLModel, table=True):
    """Database table for user preferences."""

    user_id: int = Field(
        exclude=True, sa_column=Column(BigInteger, ForeignKey("lazer_users.id", ondelete="CASCADE"), primary_key=True)
    )

    theme: str = "light"
    # refer to https://github.com/ppy/osu/blob/30fd40efd16a651a6c00b5c89289a85ffcbe546b/osu.Game/Localisation/Language.cs
    # zh_hant -> zh-tw
    language: str = "en"
    extra: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # https://github.com/ppy/osu-web/blob/cae2fdf03cfb8c30c8e332cfb142e03188ceffef/app/Models/UserProfileCustomization.php#L20-L38
    audio_autoplay: bool = False
    audio_muted: bool = False
    audio_volume: float = 0.45
    beatmapset_card_size: BeatmapCardSize = BeatmapCardSize.NORMAL
    beatmap_download: BeatmapDownload = BeatmapDownload.ALL
    beatmapset_show_nsfw: bool = False

    # comments_show_deleted: bool = False
    # forum_posts_show_deleted: bool = False

    extras_order: list[str] = Field(
        default_factory=lambda: DEFAULT_ORDER,
        sa_column=Column(JSON),
        exclude=True,
    )
    legacy_score_only: bool = False  # lazer mode
    profile_cover_expanded: bool = True
    scoring_mode: ScoringMode = ScoringMode.STANDARDISED
    user_list_filter: UserListFilter = UserListFilter.ALL
    user_list_sort: UserListSort = UserListSort.LAST_VISIT
    user_list_view: UserListView = UserListView.CARD

    user: "User" = Relationship(back_populates="user_preference")
