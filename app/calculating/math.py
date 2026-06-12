def clamp[T: int | float](n: T, min_value: T, max_value: T) -> T:
    """Clamp a value between minimum and maximum bounds.

    Args:
        n: The value to clamp.
        min_value: The minimum allowed value.
        max_value: The maximum allowed value.

    Returns:
        The clamped value.
    """
    if n < min_value:
        return min_value
    elif n > max_value:
        return max_value
    else:
        return n
