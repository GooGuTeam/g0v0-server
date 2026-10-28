"""The changelog service.

Serves the changelog API (`GET /api/v2/changelog`, `GET /api/v2/changelog/{stream}/{version}`
and `GET /api/v2/changelog/{version}`) out of a JSON directory: a directory per
update stream, one file per build, and an optional `streams.json` describing the
streams themselves.

```text
changelog/
    streams.json          # [{"name": "g0v0", "display_name": "g0v0", "is_featured": true}]
    g0v0/
        2025.101.0.json   # {"version": "2025.101.0", "created_at": "...", "entries": [...]}
```

A build file is the response osu-web serves, minus everything a client does not
send: `version`, `created_at` and `entries` are enough, and `id`, `display_version`,
`users` and `youtube_id` are derived or defaulted when a file leaves them out.

The response shape follows osu-web's `Build` and `UpdateStream`, except that the
body of a changelog entry is returned as Markdown only: the server does not render
HTML, and the client renders Markdown.

`streams.json` describes the streams but never their `user_count`: osu-web sums the
`users` of a stream's builds for it, and so does this service, so the figure cannot
drift away from the builds it is supposed to describe.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from typing import Annotated, Any
import zlib

from app.config import settings
from app.log import log
from app.models.changelog import (
    BuildVersionNavigation,
    ChangelogBuild,
    ChangelogEntry,
    ChangelogEntryType,
    ChangelogIndex,
    ChangelogSearch,
    GitHubUser,
    UpdateStream,
)
from app.models.error import ErrorType, RequestError
from app.service.content.base import (
    ContentIndex,
    NonEmptyText,
    OptionalTimestamp,
    Timestamp,
    content_repository,
    describe_errors,
    gather_limited,
    list_content_directory,
    read_metadata,
    write_metadata,
)
from app.service.repository import RepositoryEntry, join_repo_path

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

logger = log("Changelog")

# Number of builds a listing returns, as osu-web serves it.
DEFAULT_LIMIT = 21

# File describing the streams, at the root of the changelog directory.
STREAMS_FILE_NAME = "streams.json"

# Version strings that carry their release date, `YYYY.MMDD.N`, which is what osu-web's
# own builds look like; used when a build file leaves `created_at` out.
_VERSION_DATE_PATTERN = re.compile(r"^(?P<year>\d{4})\.(?P<monthday>\d{1,4})\.")
_VERSION_DIGITS_PATTERN = re.compile(r"[^0-9.]")


# Heading an entry is grouped under when its file names none.
DEFAULT_CATEGORY = "Miscellaneous"

_STREAM_NAME_PATTERN = re.compile(r"[a-z0-9-]+")


def _entry_type(value: object) -> ChangelogEntryType:
    """Read the kind of change an entry describes.

    A strict model takes the member itself, so the string a file holds is read here.
    An unknown one is refused rather than filed under the miscellaneous type, which
    would hide a typo behind an icon the author did not choose.

    Args:
        value: The declared type, as the file writes it.

    Returns:
        ChangelogEntryType: The type.

    Raises:
        ValueError: When the value is not one of the types the client knows.
    """
    if isinstance(value, ChangelogEntryType):
        return value
    if isinstance(value, str):
        try:
            return ChangelogEntryType(value)
        except ValueError:
            pass

    known = ", ".join(f"`{member.value}`" for member in ChangelogEntryType)
    raise ValueError(f"{value!r} is not a change type, use one of {known}")


_EntryType = Annotated[ChangelogEntryType, BeforeValidator(_entry_type)]


class _Entry(BaseModel):
    """A change listed under a build.

    The model is what the metadata cache round-trips through, so every field is
    declared once: `parse_build_document` stores `model_dump(mode="json")` and
    `build_from_metadata` reads it back with `model_validate`.

    Nothing is repaired here. The content repository validates its files against the
    schema next to them before they are pushed, so a file this model refuses is a file
    the author has to fix, not a value to guess at.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    type: _EntryType
    title: NonEmptyText | None = None
    message: str | None = None
    category: NonEmptyText = DEFAULT_CATEGORY
    created_at: OptionalTimestamp = None
    id: int | None = Field(default=None, ge=1)
    url: NonEmptyText | None = None
    repository: NonEmptyText | None = None
    github_url: NonEmptyText | None = None
    github_pull_request_id: int | None = None
    github_user: GitHubUser = Field(default_factory=GitHubUser)
    major: bool = False

    @model_validator(mode="after")
    def _require_body(self) -> "_Entry":
        """Refuse an entry that has nothing to show."""
        # `message` is Markdown, so its own indentation is left alone; only an entry
        # that writes nothing but whitespace is empty.
        if not self.title and not (self.message or "").strip():
            raise ValueError("an entry needs a `title`, a `message`, or both")
        return self


class _Build(BaseModel):
    """A build, as the changelog needs it.

    `stream` and `file_path` come from where the file was found rather than from its
    content, so they are left out of the metadata cache. `id`, `display_version` and
    `created_at` are required here and filled in before validation when the version
    spells them out.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    stream: str = ""
    file_path: str = ""
    id: int = Field(ge=1)
    version: NonEmptyText
    display_version: NonEmptyText
    created_at: Timestamp
    users: int = Field(default=0, ge=0)
    youtube_id: NonEmptyText | None = None
    entries: list[_Entry]

    @model_validator(mode="before")
    @classmethod
    def _derive(cls, data: object) -> object:
        """Fill in what the version spells out, before anything is validated.

        The three stay required: a version that carries no date is an error the author
        has to see, not a build to drop.

        Args:
            data: The decoded file, or the metadata cache of it.

        Returns:
            object: The file with the derivable fields filled in.

        Raises:
            ValueError: When the version carries no date and no `created_at` is given.
        """
        if not isinstance(data, dict):
            return data

        # `$schema` only points the editor at the schema next to the file, and is not
        # part of the build. Any other key is refused by `extra="forbid"`.
        values = {key: value for key, value in data.items() if key != "$schema"}

        version = values.get("version")
        if not isinstance(version, str) or not version.strip():
            return values

        version = version.strip()
        values["version"] = version
        if values.get("display_version") is None:
            values["display_version"] = display_version(version)
        if values.get("id") is None:
            values["id"] = build_id(version)
        if values.get("created_at") is None:
            released_at = date_from_version(version)
            if released_at is None:
                raise ValueError(
                    f"`created_at` is required: `{version}` carries no date, so it cannot be derived from the version"
                )
            values["created_at"] = released_at

        return values

    @model_validator(mode="after")
    def _require_entries(self) -> "_Build":
        """Refuse a build that lists nothing, which is a file left half-written.

        Checked here rather than on the field, so that an entry the model refuses is
        reported as itself instead of also emptying the list it belongs to.
        """
        if not self.entries:
            raise ValueError("a build lists at least one entry")
        return self

    @model_validator(mode="after")
    def _fill_entries(self) -> "_Build":
        """Give an entry that leaves a field out the value its build carries.

        An entry without a number of its own borrows the build's, offset by its
        position, so that two builds listing the same change still answer apart.
        """
        for index, entry in enumerate(self.entries, start=1):
            entry.id = entry.id or self.id * 1000 + index
            entry.created_at = entry.created_at or self.created_at
        return self

    @property
    def sort_key(self) -> tuple[datetime, int]:
        """Key that orders builds as they were released."""
        return self.created_at, self.id


class _Stream(BaseModel):
    """An update stream, as the changelog needs it.

    The name is both the directory holding the builds and the `{stream}` segment of the
    changelog URL, so it is limited to what both accept.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    # Zero means the file leaves the identifier out, which `_StreamsDocument` numbers
    # by position.
    id: int = Field(default=0, ge=1)
    name: NonEmptyText
    display_name: NonEmptyText | None = None
    is_featured: bool = False

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if _STREAM_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("a stream name may only hold lowercase letters, digits and dashes")
        return value


class _StreamsDocument(BaseModel):
    """The file describing the update streams.

    Held either as the list itself or under a `streams` key, since both read well and
    the file is short enough that either shape is comfortable.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    streams: list[_Stream]

    @model_validator(mode="before")
    @classmethod
    def _read_list(cls, data: object) -> object:
        if isinstance(data, list):
            return {"streams": data}
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key != "$schema"}
        return data

    @model_validator(mode="after")
    def _fill(self) -> "_StreamsDocument":
        """Number the streams that leave their identifier out, and refuse duplicates."""
        if not self.streams:
            raise ValueError("the file describes no stream; leave it out instead of emptying it")

        seen: set[str] = set()
        for index, stream in enumerate(self.streams, start=1):
            if stream.name in seen:
                raise ValueError(f"stream `{stream.name}` is declared twice")
            seen.add(stream.name)
            stream.id = stream.id or index
            stream.display_name = stream.display_name or stream.name
        return self


@dataclass(frozen=True)
class _Snapshot:
    """Everything the changelog knows, parsed once.

    Attributes:
        streams: The streams, the featured ones first.
        builds: The builds of every stream, oldest first.
    """

    streams: tuple[_Stream, ...]
    builds: tuple[_Build, ...]


def display_version(version: str) -> str:
    """Strip a version down to what the client displays.

    A version may carry the platform it was built for (`2025.101.0-g0v0`), which is
    dropped, as is everything that is not a digit or a dot.

    Args:
        version: The full version, e.g. `2025.101.0-g0v0`.

    Returns:
        str: The version as the client displays it, falling back to the full version
            when nothing is left.
    """
    base = version.split("-", 1)[0]
    return _VERSION_DIGITS_PATTERN.sub("", base) or version


def build_id(version: str) -> int:
    """Derive the identifier of a build from its version.

    osu-web numbers its builds from a database column; here the number is derived, so
    that it stays the same across restarts and deployments. A build may declare its
    own identifier, which is the only way to keep it apart from a version that spells
    the same digits differently.

    Args:
        version: The version of the build.

    Returns:
        int: The identifier.
    """
    # Only the part before the stream suffix numbers the build: a suffix such as
    # `-g0v0` carries a digit of its own, which is not part of the number.
    base = version.split("-", 1)[0]
    digits = _VERSION_DIGITS_PATTERN.sub("", base).replace(".", "")
    return int(digits) if digits.isdigit() else zlib.crc32(version.encode())


def date_from_version(version: str) -> datetime | None:
    """Read the release date a version string carries.

    Args:
        version: The version, e.g. `2025.101.0`.

    Returns:
        datetime | None: The date, or `None` when the version does not carry a usable one.
    """
    match = _VERSION_DATE_PATTERN.match(version)
    if match is None:
        return None

    monthday = match.group("monthday").zfill(4)
    try:
        return datetime(int(match.group("year")), int(monthday[:2]), int(monthday[2:]), tzinfo=UTC)
    except ValueError:
        return None


def _build_from_payload(payload: object, *, stream: str, file_path: str) -> _Build:
    """Validate a decoded build.

    Args:
        payload: The decoded JSON, or the metadata cache of it.
        stream: Name of the stream the file was found under.
        file_path: Repository path of the file.

    Returns:
        _Build: The build.

    Raises:
        ValueError: When the payload does not describe a build.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"a build holds a JSON object, not a {type(payload).__name__}")

    # Where a build sits in the repository decides these two; a file that writes them
    # would either repeat its own directory or contradict it.
    for reserved in ("stream", "file_path"):
        if reserved in payload:
            raise ValueError(f"`{reserved}` comes from the repository, not from the file")

    return _Build.model_validate({**payload, "stream": stream, "file_path": file_path})


def parse_build_document(text: str) -> dict[str, Any]:
    """Read a build file into the metadata that gets cached.

    Args:
        text: The content of the JSON file.

    Returns:
        dict[str, Any]: The metadata of the build, ready to be cached as JSON.

    Raises:
        ValueError: When the file is not a build, with the reasons it was refused.
    """
    payload = json.loads(text)
    build = _build_from_payload(payload, stream="", file_path="")
    return build.model_dump(mode="json", exclude={"stream", "file_path"})


def parse_streams_document(text: str) -> list[_Stream]:
    """Read the file describing the streams.

    A `user_count` key is refused rather than ignored: the figure is summed from the
    builds of a stream, so a number stored next to it would only be a copy that goes
    stale, and the schema refuses it for the same reason.

    Args:
        text: The content of the JSON file, which may hold the list directly or under
            a `streams` key.

    Returns:
        list[_Stream]: The declared streams.

    Raises:
        ValueError: When the file does not describe the streams, with the reasons.
    """
    return list(_StreamsDocument.model_validate(json.loads(text)).streams)


def build_from_metadata(stream: str, file_path: str, metadata: dict[str, Any]) -> _Build:
    """Rebuild a build out of the metadata cached for its file.

    Args:
        stream: Name of the stream the file belongs to.
        file_path: Repository path of the file.
        metadata: The parsed metadata of the file.

    Returns:
        _Build: The build.

    Raises:
        ValueError: When the cached metadata no longer describes a build.
    """
    return _build_from_payload(metadata, stream=stream, file_path=file_path)


class ChangelogService:
    """Answers changelog requests from a snapshot of the changelog directory.

    Attributes:
        _index: Snapshot of every build, refreshed behind the requests.
    """

    def __init__(self) -> None:
        self._index: ContentIndex[_Snapshot] = ContentIndex(
            self._load_index,
            expire=settings.content_index_expire_seconds,
        )

    @property
    def _root(self) -> str:
        """Repository directory holding the builds."""
        return settings.changelog_content_root.strip("/")

    async def _load_streams(self, entries: list[RepositoryEntry]) -> list[_Stream]:
        """Read the streams the repository declares.

        Args:
            entries: The entries of the changelog directory.

        Returns:
            list[_Stream]: The declared streams; empty when the repository declares none.
        """
        description = next(
            (entry for entry in entries if entry.kind == "file" and entry.name == STREAMS_FILE_NAME),
            None,
        )
        if description is None:
            return []

        text = await content_repository().fetch_text(description.path)
        if text is None:
            return []

        streams = await self._read_streams(description.path, text)
        return streams

    async def _read_streams(self, path: str, text: str) -> list[_Stream]:
        """Read the streams, falling back to the directories when the file is refused.

        Losing the file only costs the order and the featured flag of the streams, since
        every directory still becomes one, so a refused `streams.json` is reported and
        stepped over rather than failing the whole changelog.

        Args:
            path: Repository path of the file, for the log.
            text: The content of the file.

        Returns:
            list[_Stream]: The declared streams, or an empty list when it was refused.
        """
        try:
            return parse_streams_document(text)
        except ValueError as error:
            logger.error(f"Refusing {path}: {describe_errors(error)}")
            return []

    async def _load_build(self, stream: str, entry: RepositoryEntry) -> _Build | None:
        """Read a build, from the cache when its content did not change.

        Args:
            stream: Name of the stream the file belongs to.
            entry: The file backing the build.

        Returns:
            _Build | None: The build, or `None` when the file is not a usable build.
        """
        metadata = await read_metadata("changelog", entry.sha)
        if metadata is None:
            text = await content_repository().fetch_text(entry.path)
            if text is None:
                logger.warning(f"Changelog file {entry.path} disappeared while it was being read")
                return None

            try:
                metadata = parse_build_document(text)
            except ValueError as error:
                logger.error(f"Refusing {entry.path}: {describe_errors(error)}")
                return None

            await write_metadata("changelog", entry.sha, metadata)

        try:
            return build_from_metadata(stream, entry.path, metadata)
        except ValueError as error:
            logger.error(f"Refusing the cached metadata of {entry.path}: {describe_errors(error)}")
            return None

    async def _load_index(self) -> _Snapshot:
        """Build the snapshot of every stream and build.

        Returns:
            _Snapshot: The streams and the builds, oldest build first.
        """
        entries = await list_content_directory(self._root)
        declared = await self._load_streams(entries)

        directories = [entry for entry in entries if entry.kind == "dir"]
        listings = await gather_limited(
            [list_content_directory(entry.path or join_repo_path(self._root, entry.name)) for entry in directories]
        )

        candidates = [
            (directory.name, entry)
            for directory, listing in zip(directories, listings, strict=True)
            for entry in listing
            if entry.kind == "file" and entry.name.endswith(".json")
        ]

        builds = [
            build
            for build in await gather_limited([self._load_build(stream, entry) for stream, entry in candidates])
            if build is not None
        ]

        if candidates and not builds:
            # Every file was refused or unreachable. An empty listing would tell clients
            # that the streams have no builds, which the log says otherwise about.
            logger.error(f"No build of {self._root} could be read, all {len(candidates)} file(s) were refused")
            raise RequestError(ErrorType.INTERNAL, status_code=503)

        ordered = sorted(builds, key=lambda build: build.sort_key)

        # Two versions can spell the same digits, which would give them the same derived
        # identifier; the one loaded later moves instead, so the identifiers stay unique.
        seen: set[int] = set()
        unique: list[_Build] = []
        for build in ordered:
            identifier = build.id
            while identifier in seen:
                identifier += 1
            seen.add(identifier)
            unique.append(build if identifier == build.id else build.model_copy(update={"id": identifier}))

        return _Snapshot(streams=tuple(self._complete_streams(declared, unique)), builds=tuple(unique))

    def _complete_streams(self, declared: list[_Stream], builds: list[_Build]) -> list[_Stream]:
        """Add the streams a build mentions but the repository does not describe.

        Args:
            declared: The streams the repository declares.
            builds: Every build of the snapshot.

        Returns:
            list[_Stream]: The streams, the featured ones first.
        """
        streams = list(declared)
        known = {stream.name for stream in streams}
        next_id = max((stream.id for stream in streams), default=0) + 1

        for name in sorted({build.stream for build in builds} - known):
            # A stream nobody described still needs to exist, or its builds would be
            # listed without the stream they belong to.
            streams.append(_Stream(id=next_id, name=name, display_name=name, is_featured=False))
            next_id += 1

        return sorted(streams, key=lambda stream: not stream.is_featured)

    def _stream_model(
        self,
        stream: _Stream,
        *,
        latest: _Build | None = None,
        user_count: int | None = None,
        include_markdown: bool = False,
    ) -> UpdateStream:
        """Build the response model of a stream.

        Args:
            stream: The stream to describe.
            latest: The newest build of the stream, listed with the index only.
            user_count: Number of users on the stream, summed from its builds and
                listed with the index only.
            include_markdown: Whether the listed build keeps its entry bodies.

        Returns:
            UpdateStream: The response model.
        """
        return UpdateStream(
            id=stream.id,
            name=stream.name,
            display_name=stream.display_name or stream.name,
            is_featured=stream.is_featured,
            latest_build=(
                self._build_model(latest, stream=stream, include_markdown=include_markdown, include_entries=False)
                if latest is not None
                else None
            ),
            user_count=user_count,
        )

    def _entry_model(self, entry: _Entry, *, include_markdown: bool) -> ChangelogEntry:
        """Build the response model of an entry.

        Args:
            entry: The entry to describe.
            include_markdown: Whether to include the body.

        Returns:
            ChangelogEntry: The response model.
        """
        return ChangelogEntry(
            id=entry.id,
            repository=entry.repository,
            github_pull_request_id=entry.github_pull_request_id,
            github_url=entry.github_url,
            url=entry.url,
            type=entry.type,
            category=entry.category,
            # An entry may carry only a message; the response keeps a title field for
            # the client, which renders the type icon next to it.
            title=entry.title or "",
            message=entry.message if include_markdown else None,
            message_html=None,
            major=entry.major,
            created_at=entry.created_at,
            github_user=entry.github_user,
        )

    def _build_model(
        self,
        build: _Build,
        *,
        stream: _Stream,
        include_markdown: bool,
        include_entries: bool = True,
        navigation: BuildVersionNavigation | None = None,
    ) -> ChangelogBuild:
        """Build the response model of a build.

        Args:
            build: The build to describe.
            stream: The stream the build belongs to.
            include_markdown: Whether to include the entry bodies.
            include_entries: Whether to include the entries at all.
            navigation: The neighbouring builds, when they were resolved.

        Returns:
            ChangelogBuild: The response model.
        """
        return ChangelogBuild(
            id=build.id,
            version=build.version,
            display_version=build.display_version,
            users=build.users,
            created_at=build.created_at,
            youtube_id=build.youtube_id,
            update_stream=self._stream_model(stream),
            changelog_entries=[self._entry_model(entry, include_markdown=include_markdown) for entry in build.entries]
            if include_entries
            else [],
            versions=navigation,
        )

    def _navigation(self, snapshot: _Snapshot, build: _Build) -> BuildVersionNavigation:
        """Resolve the builds released around a build, within its stream.

        Args:
            snapshot: The snapshot holding every build.
            build: The build being read.

        Returns:
            BuildVersionNavigation: The neighbouring builds, when they exist.
        """
        siblings = [candidate for candidate in snapshot.builds if candidate.stream == build.stream]
        position = next((index for index, candidate in enumerate(siblings) if candidate.id == build.id), None)
        if position is None:
            return BuildVersionNavigation()

        stream = self._stream(snapshot, build.stream)
        return BuildVersionNavigation(
            next=(
                self._build_model(
                    siblings[position + 1],
                    stream=stream,
                    include_markdown=False,
                    include_entries=False,
                )
                if position + 1 < len(siblings)
                else None
            ),
            previous=(
                self._build_model(
                    siblings[position - 1],
                    stream=stream,
                    include_markdown=False,
                    include_entries=False,
                )
                if position > 0
                else None
            ),
        )

    def _stream(self, snapshot: _Snapshot, name: str) -> _Stream:
        """Find a stream of the snapshot.

        Args:
            snapshot: The snapshot holding every stream.
            name: Name of the stream.

        Returns:
            _Stream: The stream, or a placeholder when the snapshot does not know it.
        """
        found = next((stream for stream in snapshot.streams if stream.name == name), None)
        if found is not None:
            return found
        return _Stream(id=0, name=name, display_name=name, is_featured=False)

    def _user_count(self, snapshot: _Snapshot, stream: str) -> int:
        """Count the users a stream reports, as the sum of its builds.

        The number belongs to the builds, not to the stream: osu-web derives it the
        same way (`UpdateStream::scopeWithUserCount` sums `users` over the stream's
        builds), so it stays consistent with the data instead of being a figure that
        goes stale between edits of a hand-written stream list.

        Args:
            snapshot: The snapshot holding every build.
            stream: Name of the stream.

        Returns:
            int: Number of users running a build of the stream.
        """
        return sum(build.users for build in snapshot.builds if build.stream == stream)

    def _latest_of(self, snapshot: _Snapshot, stream: str) -> _Build | None:
        """Find the newest build of a stream.

        Args:
            snapshot: The snapshot holding every build.
            stream: Name of the stream.

        Returns:
            _Build | None: The newest build, or `None` when the stream has none.
        """
        builds = [build for build in snapshot.builds if build.stream == stream]
        return max(builds, key=lambda build: build.sort_key) if builds else None

    def _find_by_version(self, builds: list[_Build], version: str) -> _Build | None:
        """Find a build by its version, within the builds a listing may return.

        Args:
            builds: The builds to search.
            version: The version to look for, with or without its stream suffix.

        Returns:
            _Build | None: The build, or `None` when none carries that version.
        """
        candidates = [build for build in builds if build.version == version]
        if not candidates:
            normalized = display_version(version)
            candidates = [build for build in builds if build.display_version == normalized]
        return max(candidates, key=lambda build: build.sort_key) if candidates else None

    async def get_index(
        self,
        *,
        stream: str | None = None,
        from_version: str | None = None,
        to_version: str | None = None,
        max_id: int | None = None,
        include_markdown: bool = True,
    ) -> ChangelogIndex:
        """List the builds, newest first.

        Args:
            stream: Stream to list, or every stream when omitted.
            from_version: Lowest version to include.
            to_version: Highest version to include.
            max_id: Highest build identifier to include.
            include_markdown: Whether to include the entry bodies.

        Returns:
            ChangelogIndex: The builds, with every stream that has one.
        """
        snapshot = await self._index.get()
        builds = list(snapshot.builds)

        if stream is not None:
            builds = [build for build in builds if build.stream == stream]

        if from_version is not None:
            bound = self._find_by_version(builds, from_version)
            builds = [build for build in builds if bound is not None and build.sort_key >= bound.sort_key]

        if to_version is not None:
            bound = self._find_by_version(builds, to_version)
            builds = [build for build in builds if bound is not None and build.sort_key <= bound.sort_key]

        if max_id is not None:
            builds = [build for build in builds if build.id <= max_id]

        builds.sort(key=lambda build: build.sort_key, reverse=True)
        page = builds[:DEFAULT_LIMIT]

        streams = [
            self._stream_model(
                candidate,
                latest=self._latest_of(snapshot, candidate.name),
                user_count=self._user_count(snapshot, candidate.name),
                include_markdown=include_markdown,
            )
            for candidate in snapshot.streams
            if any(build.stream == candidate.name for build in snapshot.builds)
        ]

        return ChangelogIndex(
            streams=streams,
            builds=[
                self._build_model(build, stream=self._stream(snapshot, build.stream), include_markdown=include_markdown)
                for build in page
            ],
            search=ChangelogSearch(
                stream=stream,
                from_=from_version,
                to=to_version,
                max_id=max_id,
                limit=DEFAULT_LIMIT,
            ),
        )

    async def get_build(self, stream: str, version: str, *, include_markdown: bool = True) -> ChangelogBuild | None:
        """Read the build a stream published under a version.

        Args:
            stream: Name of the stream.
            version: Version of the build, with or without its stream suffix.
            include_markdown: Whether to include the entry bodies.

        Returns:
            ChangelogBuild | None: The build, or `None` when the stream has no such build.
        """
        snapshot = await self._index.get()
        builds = [build for build in snapshot.builds if build.stream == stream and build.version == version]

        if not builds:
            # A build names its stream in its version (`2026.1009.0-g0v0`), while a link
            # copied out of the client carries the version it displays, without it.
            normalized = display_version(version)
            builds = [
                build for build in snapshot.builds if build.stream == stream and build.display_version == normalized
            ]

        if not builds:
            return None

        build = max(builds, key=lambda candidate: candidate.sort_key)
        return self._build_model(
            build,
            stream=self._stream(snapshot, stream),
            include_markdown=include_markdown,
            navigation=self._navigation(snapshot, build),
        )

    async def get_build_by_reference(
        self,
        reference: str,
        *,
        by_id: bool = False,
        include_markdown: bool = True,
    ) -> ChangelogBuild | None:
        """Read a build from anything a client may address it with.

        A build is addressed by its identifier, its exact version, the name of the
        stream it belongs to (which resolves to its newest build), or a version that
        differs from the stored one only by the characters that are not a digit or a
        dot.

        Args:
            reference: The identifier, version or stream name.
            by_id: Whether `reference` holds an identifier.
            include_markdown: Whether to include the entry bodies.

        Returns:
            ChangelogBuild | None: The build, or `None` when nothing matches.
        """
        snapshot = await self._index.get()

        build: _Build | None = None
        if by_id:
            # The identifier comes from the request, not from a file: one that is not a
            # number matches no build rather than being reported as a broken request.
            identifier = int(reference) if reference.isdigit() else None
            build = next((candidate for candidate in snapshot.builds if candidate.id == identifier), None)
        else:
            build = self._find_by_version(list(snapshot.builds), reference)

            if build is None:
                # Failing an exact version, the reference may name a stream.
                build = self._latest_of(snapshot, reference)

            if build is None:
                normalized = display_version(reference)
                candidates = [candidate for candidate in snapshot.builds if candidate.display_version == normalized]
                build = max(candidates, key=lambda candidate: candidate.sort_key) if candidates else None

        if build is None:
            return None

        return self._build_model(
            build,
            stream=self._stream(snapshot, build.stream),
            include_markdown=include_markdown,
            navigation=self._navigation(snapshot, build),
        )

    async def warm(self) -> None:
        """Build the snapshot ahead of the first request.

        Meant to run as a background task, as it waits on the repository.
        """
        await self._index.warm()

    async def close(self) -> None:
        """Stop a refresh that is still running."""
        await self._index.close()
