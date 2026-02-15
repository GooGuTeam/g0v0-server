"""Date and time utilities.

This module provides functions for timestamp conversion and week calculations.
"""

from datetime import UTC, datetime


def unix_timestamp_to_windows(timestamp: int) -> int:
    """Convert a Unix timestamp to a Windows timestamp."""
    return (timestamp + 62135596800) * 10_000_000


def are_adjacent_weeks(dt1: datetime, dt2: datetime) -> bool:
    """Check if two datetime objects are in adjacent weeks.

    Args:
        dt1: The first datetime.
        dt2: The second datetime.

    Returns:
        True if the dates are in adjacent weeks, False otherwise.
    """
    y1, w1, _ = dt1.isocalendar()
    y2, w2, _ = dt2.isocalendar()

    # Sort by (year, week), ensure dt1 <= dt2
    if (y1, w1) > (y2, w2):
        y1, w1, y2, w2 = y2, w2, y1, w1

    # Same year, adjacent week numbers
    if y1 == y2 and w2 - w1 == 1:
        return True

    # Year boundary: check if y2 is next year, w2 == 1, and w1 is last week of y1
    if y2 == y1 + 1 and w2 == 1:
        # Determine last week number of y1
        last_week_y1 = datetime(y1, 12, 28).isocalendar()[1]  # 12-28 is guaranteed in last week
        if w1 == last_week_y1:
            return True

    return False


def are_same_weeks(dt1: datetime, dt2: datetime) -> bool:
    """Check if two datetime objects are in the same week.

    Args:
        dt1: The first datetime.
        dt2: The second datetime.

    Returns:
        True if the dates are in the same week, False otherwise.
    """
    return dt1.isocalendar()[:2] == dt2.isocalendar()[:2]


def utcnow() -> datetime:
    """Get the current UTC datetime.

    Returns:
        The current datetime with UTC timezone.
    """
    return datetime.now(tz=UTC)
