"""Game modes API endpoint.

Provides information about all supported game modes in the system.
"""

from app.config import settings
from app.models.score import DOWNLOAD_URL, RULESETS_VERSION_HASH, GameMode

from .router import router

from pydantic import BaseModel, Field


class GameModeInfo(BaseModel):
    """Game mode information.

    Attributes:
        id: Numeric game mode ID.
        name: Game mode name.
        readable: Human-readable game mode name.
        is_official: Whether this is an official mode.
        is_custom_ruleset: Whether this is a custom ruleset.
    """

    id: int = Field(description="Numeric game mode ID")
    name: str = Field(description="Game mode name")
    readable: str = Field(description="Human-readable game mode name")
    is_official: bool = Field(description="Whether this is an official mode")
    is_custom_ruleset: bool = Field(description="Whether this is a custom ruleset")


class GameModesResponse(BaseModel):
    """Game modes list response.

    Attributes:
        gamemodes: List of game modes.
        total: Total number of game modes.
        enable_rx: Whether RX mode is enabled.
        enable_ap: Whether AP mode is enabled.
    """

    gamemodes: list[GameModeInfo] = Field(description="List of game modes")
    total: int = Field(description="Total number of game modes")
    enable_rx: bool = Field(description="Whether RX mode is enabled")
    enable_ap: bool = Field(description="Whether AP mode is enabled")


class GameModeVersionInfo(BaseModel):
    """Version and release information of a registered custom game mode.

    Attributes:
        id: Numeric game mode ID.
        name: Game mode name.
        readable: Human-readable game mode name.
        latest_version: Latest ruleset version.
        versions: Mapping of ruleset versions to their MD5 hashes.
        download_url: Release link of the latest ruleset version.
    """

    id: int = Field(description="Numeric game mode ID")
    name: str = Field(description="Game mode name")
    readable: str = Field(description="Human-readable game mode name")
    latest_version: str = Field(description="Latest ruleset version")
    versions: dict[str, str] = Field(description="Mapping of ruleset version to its MD5 hash")
    download_url: str | None = Field(description="Release link of the latest ruleset version")


class GameModeVersionsResponse(BaseModel):
    """Game modes version list response.

    Attributes:
        gamemodes: List of registered custom game modes with version information.
        total: Total number of registered custom game modes.
    """

    gamemodes: list[GameModeVersionInfo] = Field(
        description="List of registered custom game modes with version information"
    )
    total: int = Field(description="Total number of registered custom game modes")


@router.get(
    "/gamemodes",
    response_model=GameModesResponse,
    tags=["Game Modes", "g0v0 API"],
    name="Get game modes list",
    description="Get all supported game modes and their corresponding IDs",
)
async def get_gamemodes() -> GameModesResponse:
    gamemodes = []

    # Iterate through all game modes
    for mode in GameMode:
        gamemodes.append(
            GameModeInfo(
                id=int(mode),
                name=str(mode),
                readable=mode.readable(),
                is_official=mode.is_official(),
                is_custom_ruleset=mode.is_custom_ruleset(),
            )
        )

    # Sort by ID
    gamemodes.sort(key=lambda x: x.id)

    return GameModesResponse(
        gamemodes=gamemodes,
        total=len(gamemodes),
        enable_rx=settings.enable_rx,
        enable_ap=settings.enable_ap,
    )


@router.get(
    "/gamemodes/versions",
    response_model=GameModeVersionsResponse,
    tags=["Game Modes", "g0v0 API"],
    name="Get game mode versions",
    description="Get version, hash and release link details of registered custom game modes",
)
async def get_gamemode_versions() -> GameModeVersionsResponse:
    """Get version information of all registered custom game modes.

    Only custom rulesets that have been registered at startup (i.e. enabled)
    are returned. Official game modes ship with the client and have no
    version information.

    Returns:
        GameModeVersionsResponse with version details per registered custom game mode.
    """
    gamemodes = []

    # Iterate through all registered custom game modes
    for mode, entry in RULESETS_VERSION_HASH.items():
        latest_version = entry["latest-version"]
        gamemodes.append(
            GameModeVersionInfo(
                id=int(mode),
                name=str(mode),
                readable=mode.readable(),
                latest_version=latest_version,
                versions=entry["versions"],
                download_url=DOWNLOAD_URL.format(version=latest_version) if latest_version else None,
            )
        )

    # Sort by ID
    gamemodes.sort(key=lambda x: x.id)

    return GameModeVersionsResponse(
        gamemodes=gamemodes,
        total=len(gamemodes),
    )
