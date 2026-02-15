"""Performance and difficulty calculation module.

This module provides functionality for calculating performance points (PP),
difficulty ratings, score conversions, and beatmap validation. It integrates
with pluggable performance calculator backends and includes suspicious beatmap
detection.
"""

from enum import Enum
from typing import TYPE_CHECKING

from app.models.score import GameMode

from osupyparser import HitObject, OsuFile
from osupyparser.osu.objects import Slider

if TYPE_CHECKING:
    pass


# Suspicious beatmap detection algorithm based on:
# https://github.com/MaxOhn/rosu-pp/blob/main/src/model/beatmap/suspicious.rs


class Threshold(int, Enum):
    """Threshold constants for suspicious beatmap detection."""

    # Beatmap abnormality constants
    NOTES_THRESHOLD = 500000  # Object count limit for non-taiko modes
    TAIKO_THRESHOLD = 30000  # Object count limit for taiko mode

    NOTES_PER_1S_THRESHOLD = 200  # 3000 BPM equivalent
    NOTES_PER_10S_THRESHOLD = 500  # 600 BPM equivalent

    # This is already 4x the normal play area size
    NOTE_POSX_THRESHOLD = 512  # x: [-512,512]
    NOTE_POSY_THRESHOLD = 384  # y: [-384,384]

    POS_ERROR_THRESHOLD = 1280 * 50  # Ban if this many objects (including slider control points) have position issues

    SLIDER_REPEAT_THRESHOLD = 5000


def too_dense(hit_objects: list[HitObject], per_1s: int, per_10s: int) -> bool:
    """Check if hit objects are too densely packed.

    Args:
        hit_objects: List of hit objects.
        per_1s: Maximum objects allowed per 1 second window.
        per_10s: Maximum objects allowed per 10 second window.

    Returns:
        True if the beatmap is too dense, False otherwise.
    """
    per_1s = max(1, per_1s)
    per_10s = max(1, per_10s)
    for i in range(0, len(hit_objects)):
        if len(hit_objects) > i + per_1s:
            if hit_objects[i + per_1s].start_time - hit_objects[i].start_time < 1000:
                return True
        elif len(hit_objects) > i + per_10s and hit_objects[i + per_10s].start_time - hit_objects[i].start_time < 10000:
            return True
    return False


def slider_is_sus(hit_objects: list[HitObject]) -> bool:
    """Check if sliders have suspicious properties.

    Args:
        hit_objects: List of hit objects.

    Returns:
        True if any slider is suspicious, False otherwise.
    """
    for obj in hit_objects:
        if isinstance(obj, Slider):
            flag_repeat = obj.repeat_count > Threshold.SLIDER_REPEAT_THRESHOLD
            flag_pos = int(
                obj.pos.x > Threshold.NOTE_POSX_THRESHOLD
                or obj.pos.x < 0
                or obj.pos.y > Threshold.NOTE_POSY_THRESHOLD
                or obj.pos.y < 0
            )
            for point in obj.points:
                flag_pos += int(
                    point.x > Threshold.NOTE_POSX_THRESHOLD
                    or point.x < 0
                    or point.y > Threshold.NOTE_POSY_THRESHOLD
                    or point.y < 0
                )
            if flag_pos or flag_repeat:
                return True
    return False


def is_2b(hit_objects: list[HitObject]) -> bool:
    """Check if beatmap contains overlapping (2B) hit objects.

    Args:
        hit_objects: List of hit objects.

    Returns:
        True if overlapping objects are detected, False otherwise.
    """
    return any(hit_objects[i] == hit_objects[i + 1].start_time for i in range(0, len(hit_objects) - 1))


def is_suspicious_beatmap(content: str) -> bool:
    """Check if a beatmap is suspicious.

    Analyzes beatmap content for abnormal properties like excessive
    object count, density, position errors, or 2B patterns.

    Args:
        content: The .osu beatmap file content.

    Returns:
        True if the beatmap is suspicious, False otherwise.
    """
    osufile = OsuFile(content=content.encode("utf-8")).parse_file()

    if osufile.hit_objects[-1].start_time - osufile.hit_objects[0].start_time > 24 * 60 * 60 * 1000:
        return True
    if osufile.mode == int(GameMode.TAIKO):
        if len(osufile.hit_objects) > Threshold.TAIKO_THRESHOLD:
            return True
    elif len(osufile.hit_objects) > Threshold.NOTES_THRESHOLD:
        return True
    match osufile.mode:
        case int(GameMode.OSU):
            return (
                too_dense(
                    osufile.hit_objects,
                    Threshold.NOTES_PER_1S_THRESHOLD,
                    Threshold.NOTES_PER_10S_THRESHOLD,
                )
                or slider_is_sus(osufile.hit_objects)
                or is_2b(osufile.hit_objects)
            )
        case int(GameMode.TAIKO):
            return too_dense(
                osufile.hit_objects,
                Threshold.NOTES_PER_1S_THRESHOLD * 2,
                Threshold.NOTES_PER_10S_THRESHOLD * 2,
            ) or is_2b(osufile.hit_objects)
        case int(GameMode.FRUITS):
            return slider_is_sus(osufile.hit_objects) or is_2b(osufile.hit_objects)
        case int(GameMode.MANIA):
            keys_per_hand = max(1, int(osufile.cs / 2))
            per_1s = Threshold.NOTES_PER_1S_THRESHOLD * keys_per_hand
            per_10s = Threshold.NOTES_PER_10S_THRESHOLD * keys_per_hand
            return too_dense(osufile.hit_objects, per_1s, per_10s)
    return False
