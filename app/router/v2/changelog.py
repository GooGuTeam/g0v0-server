"""Changelog API endpoints.

Implements the osu! changelog API on top of a content repository, so that
osu!(lazer)'s changelog overlay shows what this server released.

A build is addressed the way osu-web addresses it: by update stream and version, or
by anything a client has at hand (a build identifier, an exact version, a stream
name resolving to its newest build, or a version that differs from the stored one
only in the characters that are not a digit or a dot). Entry bodies are returned as
Markdown only; the client renders them.
"""

from typing import Annotated

from app.config import settings
from app.models.changelog import ChangelogBuild, ChangelogIndex
from app.models.error import ErrorType, RequestError
from app.service.content import changelog_service

from .router import router

from fastapi import Path, Query


def _include_markdown(formats: list[str] | None) -> bool:
    """Decide whether changelog entry bodies belong in the response.

    A client asks for the formats it wants; both are returned when it asks for
    neither, and only Markdown is ever available, as the server does not render HTML.

    Args:
        formats: The requested formats, as `message_formats[]`.

    Returns:
        bool: Whether the Markdown bodies should be included.
    """
    if not formats:
        return True
    return any(value.strip().lower() == "markdown" for value in formats)


@router.get(
    "/changelog",
    response_model=ChangelogIndex,
    tags=["Changelog"],
    name="Get Changelog Listing",
    description=(
        "Returns a listing of update streams, builds, and changelog entries. Builds are read from the "
        "server's content repository, and entry bodies are returned as Markdown."
    ),
)
async def router_get_changelog_index(
    stream: Annotated[
        str | None,
        Query(description="Stream name to return builds from. Example: g0v0"),
    ] = None,
    from_version: Annotated[
        str | None,
        Query(alias="from", description="Minimum build version. Example: 2025.101.0"),
    ] = None,
    to_version: Annotated[
        str | None,
        Query(alias="to", description="Maximum build version. Example: 2025.102.0"),
    ] = None,
    max_id: Annotated[
        int | None,
        Query(description="Maximum build ID."),
    ] = None,
    message_formats: Annotated[
        list[str] | None,
        Query(alias="message_formats[]", description="`html`, `markdown`. Default to both."),
    ] = None,
) -> ChangelogIndex:
    """Retrieve the changelog listing.

    Args:
        stream: Stream name to return builds from.
        from_version: Minimum build version.
        to_version: Maximum build version.
        max_id: Maximum build ID.
        message_formats: Formats the entry bodies are requested in.

    Returns:
        ChangelogIndex: The builds, and every stream that has one.

    Raises:
        RequestError: If the changelog is disabled.
    """
    if not settings.enable_changelog:
        raise RequestError(ErrorType.NOT_FOUND)

    return await changelog_service.get_index(
        stream=stream,
        from_version=from_version,
        to_version=to_version,
        max_id=max_id,
        include_markdown=_include_markdown(message_formats),
    )


@router.get(
    "/changelog/{stream}/{build}",
    response_model=ChangelogBuild,
    tags=["Changelog"],
    name="Get Changelog Build",
    description=(
        "Returns details of the specified build, with the builds released before and after it in the same stream."
    ),
)
async def router_get_changelog_build(
    stream: Annotated[
        str,
        Path(description="Update stream name. Example: g0v0"),
    ],
    build: Annotated[
        str,
        Path(description="Build version. Example: 2025.101.0"),
    ],
    message_formats: Annotated[
        list[str] | None,
        Query(alias="message_formats[]", description="`html`, `markdown`. Default to both."),
    ] = None,
) -> ChangelogBuild:
    """Retrieve a build of a stream.

    Args:
        stream: Update stream name.
        build: Build version.
        message_formats: Formats the entry bodies are requested in.

    Returns:
        ChangelogBuild: The build.

    Raises:
        RequestError: If the changelog is disabled or the build does not exist.
    """
    if not settings.enable_changelog:
        raise RequestError(ErrorType.NOT_FOUND)

    found = await changelog_service.get_build(stream, build, include_markdown=_include_markdown(message_formats))
    if found is None:
        raise RequestError(ErrorType.NOT_FOUND)

    return found


@router.get(
    "/changelog/{version}",
    response_model=ChangelogBuild,
    tags=["Changelog"],
    name="Lookup Changelog Build",
    description=(
        "Returns details of the specified build. The build is addressed by its identifier (with `key=id`), "
        "its version, the name of its stream, or a version that only differs in the characters that are "
        "not a digit or a dot."
    ),
)
async def router_lookup_changelog_build(
    version: Annotated[
        str,
        Path(description="Build version, update stream name, or build ID. Example: 2025.101.0"),
    ],
    key: Annotated[
        str | None,
        Query(description="Unset to query by build version or stream name, or `id` to query by build ID."),
    ] = None,
    message_formats: Annotated[
        list[str] | None,
        Query(alias="message_formats[]", description="`html`, `markdown`. Default to both."),
    ] = None,
) -> ChangelogBuild:
    """Look up a build by identifier, version or stream name.

    Args:
        version: The build ID, version or stream name.
        key: `id` to read `version` as a build ID.
        message_formats: Formats the entry bodies are requested in.

    Returns:
        ChangelogBuild: The build.

    Raises:
        RequestError: If the changelog is disabled or nothing matches.
    """
    if not settings.enable_changelog:
        raise RequestError(ErrorType.NOT_FOUND)

    found = await changelog_service.get_build_by_reference(
        version,
        by_id=key == "id",
        include_markdown=_include_markdown(message_formats),
    )
    if found is None:
        raise RequestError(ErrorType.NOT_FOUND)

    return found
