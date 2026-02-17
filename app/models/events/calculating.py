from app.models.performance import PerformanceAttributes

from ._base import PluginEvent


class BeforeCalculatingPPEvent(PluginEvent):
    """Event fired before calculating PP for a score."""

    score_id: int
    beatmap_raw: str


class AfterCalculatingPPEvent(PluginEvent):
    """Event fired after calculating PP for a score."""

    score_id: int
    beatmap_raw: str
    performance_attribute: PerformanceAttributes
