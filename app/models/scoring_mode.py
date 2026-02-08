from enum import StrEnum


class ScoringMode(StrEnum):
    """
    Scoring mode for calculating scores.

    STANDARDISED: Modern scoring mode used in current osu!lazer
    CLASSIC: Legacy scoring mode for backward compatibility
    """

    STANDARDISED = "standardised"
    CLASSIC = "classic"
