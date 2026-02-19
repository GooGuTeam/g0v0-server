from app.models.performance import PerformanceAttributes
from app.models.score import ScoreData

from ._base import PluginEvent


class BeforeCalculatingPPEvent(PluginEvent):
    """Event fired before calculating PP for a score."""

    score: ScoreData
    beatmap_raw: str

    @property
    def score_id(self) -> int:
        return self.score.id


class AfterCalculatingPPEvent(PluginEvent):
    """Event fired after calculating PP for a score."""

    score: ScoreData
    beatmap_raw: str
    performance_attribute: PerformanceAttributes

    @property
    def score_id(self) -> int:
        return self.score.id
