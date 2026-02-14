"""V1 API public router module.

This module provides the public (unauthenticated) API router for osu! API v1 endpoints.
These endpoints do not require API key authentication.
"""

from datetime import datetime
from enum import Enum

from app.dependencies.rate_limit import LIMITERS

from fastapi import APIRouter
from pydantic import BaseModel, field_serializer

# Public V1 API router - no authentication required
public_router = APIRouter(prefix="/api/v1", dependencies=LIMITERS, tags=["V1 Public API"])


class AllStrModel(BaseModel):
    """Base model that serializes all values to strings for V1 API compatibility.

    The legacy osu! API v1 returns all values as strings. This model ensures
    proper serialization of various Python types to match that behavior.
    """

    @field_serializer("*", when_used="json")
    def serialize_datetime(self, v, _info):
        """Serialize values to V1 API compatible string format.

        Args:
            v: The value to serialize.
            _info: Pydantic field info.

        Returns:
            String representation of the value in V1 API format.
        """
        if isinstance(v, Enum):
            return str(v.value)
        elif isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(v, bool):
            return "1" if v else "0"
        elif isinstance(v, list):
            return [self.serialize_datetime(item, _info) for item in v]
        return str(v)
