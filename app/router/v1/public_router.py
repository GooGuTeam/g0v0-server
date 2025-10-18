from datetime import datetime
from enum import Enum

from app.dependencies.rate_limit import LIMITERS

from fastapi import APIRouter
from pydantic import BaseModel, field_serializer

# 公共V1 API路由器 - 不需要认证
public_router = APIRouter(prefix="/api/v1", dependencies=LIMITERS, tags=["V1 Public API"])


class AllStrModel(BaseModel):
    @field_serializer("*", when_used="json")
    def serialize_datetime(self, v, _info):
        if isinstance(v, Enum):
            return str(v.value)
        elif isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(v, bool):
            return "1" if v else "0"
        elif isinstance(v, list):
            return [self.serialize_datetime(item, _info) for item in v]
        return str(v)
