from datetime import UTC
from typing import TYPE_CHECKING

from ._base import ModMultiplierCalculator, ModMultiplierContext
from .catch import CatchModMultiplierCalculator
from .mania import ManiaModMultiplierCalculator
from .osu import OsuModMultiplierCalculator
from .taiko import TaikoModMultiplierCalculator

if TYPE_CHECKING:
    from app.database import Beatmap, Score


__all__ = [
    "CatchModMultiplierCalculator",
    "ManiaModMultiplierCalculator",
    "OsuModMultiplierCalculator",
    "TaikoModMultiplierCalculator",
]


def get_mod_multiplier_calculator(
    score: "Score", beatmap: "Beatmap", client_version: str = ""
) -> ModMultiplierCalculator:
    from app.models.score import GameMode

    mode = score.gamemode.to_base_ruleset()
    context = ModMultiplierContext(
        mods=score.mods,
        cs=beatmap.cs,
        ar=beatmap.ar,
        od=beatmap.accuracy,
        hp=beatmap.drain,
        client_version=client_version,
        date=score.started_at.astimezone(UTC),
    )
    if mode == GameMode.OSU:
        return OsuModMultiplierCalculator(context)
    elif mode == GameMode.TAIKO:
        return TaikoModMultiplierCalculator(context)
    elif mode == GameMode.FRUITS:
        return CatchModMultiplierCalculator(context)
    elif mode == GameMode.MANIA:
        return ManiaModMultiplierCalculator(context)
    raise ValueError(f"Unsupported game mode: {mode}")
