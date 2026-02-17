"""V1 API authenticated router module.

This module provides the authenticated API router for osu! API v1 endpoints.
All routes require v1 API key authentication.
"""

from datetime import datetime
from enum import Enum

from app.dependencies.rate_limit import LIMITERS
from app.dependencies.user import v1_authorize

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_serializer

router = APIRouter(prefix="/api/v1", dependencies=[Depends(v1_authorize), *LIMITERS], tags=["V1 API"])


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
