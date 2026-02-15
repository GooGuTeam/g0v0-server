"""Game modes API endpoint.

Provides information about all supported game modes in the system.
"""

from app.config import settings
from app.models.score import GameMode

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
