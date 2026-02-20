from typing import Annotated

from app.models.performance import PerformanceAttributes
from app.models.score import ScoreData

from ._base import PluginEvent

from pydantic import AfterValidator, ValidationInfo


def _fill_score_id(value: int, info: ValidationInfo) -> int:
    if value != 0:
        return value
    score = info.data["score"]
    return score.id


class BeforeCalculatingPPEvent(PluginEvent):
    """Event fired before calculating PP for a score."""

    score: ScoreData
    score_id: Annotated[int, AfterValidator(_fill_score_id)] = 0
    beatmap_raw: str


class AfterCalculatingPPEvent(PluginEvent):
    """Event fired after calculating PP for a score."""

    score: ScoreData
    score_id: Annotated[int, AfterValidator(_fill_score_id)] = 0
    beatmap_raw: str
    performance_attribute: PerformanceAttributes
