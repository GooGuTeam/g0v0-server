"""Type-related helper functions and utilities.

This module provides functions for checking if a type annotation is Optional
(Union[T, None]) and other type-related utilities.
"""

from types import NoneType, UnionType
from typing import Union, get_args, get_origin


def type_is_optional(typ: type):
    """Check if a type annotation is Optional.

    Args:
        typ: The type to check.

    Returns:
        True if the type is Optional (Union[T, None]), False otherwise.
    """
    origin_type = get_origin(typ)
    args = get_args(typ)
    return (origin_type is UnionType or origin_type is Union) and len(args) == 2 and NoneType in args
