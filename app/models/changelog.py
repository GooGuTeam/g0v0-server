"""Changelog models.

Reference: <https://osu.ppy.sh/docs/index.html> (`Build`, `ChangelogEntry`,
`UpdateStream`).
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer


class ChangelogEntryType(StrEnum):
    """Kind of change an entry describes.

    The client renders an icon per type, and falls back to the miscellaneous one for
    anything it does not know.
    """

    ADD = "add"
    FIX = "fix"
    MISC = "misc"


class GitHubUser(BaseModel):
    """The GitHub account behind a changelog entry.

    `user_id` and the osu! fields only carry a value when the account was matched
    with one on this server, which a content repository has no way to know, so they
    stay empty there.
    """

    display_name: str | None = None
    github_url: str | None = None
    github_username: str | None = None
    id: int | None = None
    osu_username: str | None = None
    user_id: int | None = None
    user_url: str | None = None


class ChangelogEntry(BaseModel):
    """A single change listed under a build.

    Attributes:
        message: The change in Markdown, as the client renders it.
        message_html: The change rendered as HTML. Empty, because the body is not
            rendered on the server; consumers use `message`.
    """

    id: int | None = None
    repository: str | None = None
    github_pull_request_id: int | None = None
    github_url: str | None = None
    url: str | None = None
    type: ChangelogEntryType = ChangelogEntryType.MISC
    category: str = "Miscellaneous"
    title: str = ""
    message: str | None = None
    message_html: str | None = None
    major: bool = False
    created_at: datetime | None = None
    github_user: GitHubUser = Field(default_factory=GitHubUser)


class UpdateStream(BaseModel):
    """An update stream.

    Attributes:
        latest_build: The newest build of the stream, listed with the index only.
        user_count: Number of users on the stream, summed from its builds and listed
            with the index only.
    """

    id: int
    name: str
    display_name: str
    is_featured: bool = False
    latest_build: "ChangelogBuild | None" = None
    user_count: int | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_details(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Drop the details a stream only carries in the listing.

        A stream nested under a build is the four identifying fields, exactly as
        osu-web serialises it, so the details are left out rather than nulled.
        """
        data = handler(self)
        for key in ("latest_build", "user_count"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class BuildVersionNavigation(BaseModel):
    """The neighbouring builds of a build, within its own stream."""

    next: "ChangelogBuild | None" = None
    previous: "ChangelogBuild | None" = None


class ChangelogBuild(BaseModel):
    """A build, with the changes it shipped.

    Attributes:
        version: Full version string, which may carry a platform suffix.
        display_version: Version without anything that is not a digit or a dot,
            which is what the client shows.
        users: Number of users on this build; always zero, as the server does not
            track which build a client runs.
        youtube_id: Video summarising the build, when the content repository has one.
    """

    id: int
    version: str
    display_version: str
    users: int = 0
    created_at: datetime
    youtube_id: str | None = None
    update_stream: "UpdateStream"
    changelog_entries: list[ChangelogEntry] = Field(default_factory=list)
    versions: BuildVersionNavigation | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_navigation(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Drop `versions` where it was not resolved.

        Only the build endpoints navigate between builds; a build listed inside
        another one is a plain summary, and osu-web leaves the key out there.
        """
        data = handler(self)
        if data.get("versions") is None:
            data.pop("versions", None)
        return data


class ChangelogSearch(BaseModel):
    """The filters a changelog listing was answered with."""

    stream: str | None = None
    from_: str | None = Field(default=None, serialization_alias="from")
    to: str | None = None
    max_id: int | None = None
    limit: int = 21


class ChangelogIndex(BaseModel):
    """Response of the changelog listing."""

    streams: list[UpdateStream] = Field(default_factory=list)
    builds: list[ChangelogBuild] = Field(default_factory=list)
    search: ChangelogSearch = Field(default_factory=ChangelogSearch)


# The three types reference each other, so they can only be resolved once all of them exist.
BuildVersionNavigation.model_rebuild()
ChangelogBuild.model_rebuild()
UpdateStream.model_rebuild()

__all__ = [
    "BuildVersionNavigation",
    "ChangelogBuild",
    "ChangelogEntry",
    "ChangelogEntryType",
    "ChangelogIndex",
    "ChangelogSearch",
    "GitHubUser",
    "UpdateStream",
]
