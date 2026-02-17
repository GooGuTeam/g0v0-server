"""Color-related helper functions and utilities.

This module provides functions for converting hex color strings to hue values.
"""


def hex_to_hue(hex_color: str) -> int:
    """Convert a hex color string to a hue value (0-360).

    Args:
        hex_color: The hex color string (e.g. "#FF0000" or "FF0000").

    Returns:
        The hue value corresponding to the color.
    """
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        raise ValueError("Invalid hex color format. Expected format: RRGGBB")

    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0

    max_c = max(r, g, b)
    min_c = min(r, g, b)
    delta = max_c - min_c

    if delta == 0:
        return 0  # Achromatic (grey)

    if max_c == r:
        hue = (60 * ((g - b) / delta) + 360) % 360
    elif max_c == g:
        hue = (60 * ((b - r) / delta) + 120) % 360
    else:  # max_c == b
        hue = (60 * ((r - g) / delta) + 240) % 360

    return int(hue)
