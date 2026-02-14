"""Beatmap failtime/exit distribution database models.

This module stores when players fail or exit during a beatmap,
used for generating fail/exit graphs on beatmap pages.
"""

from struct import Struct
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlmodel import (
    VARBINARY,
    Column,
    Field,
    Relationship,
    SQLModel,
)

if TYPE_CHECKING:
    from .beatmap import Beatmap

FAILTIME_STRUCT = Struct("<100i")
"""Struct format for packing/unpacking 100 integers for fail/exit data."""


class FailTime(SQLModel, table=True):
    """Stores fail and exit time distributions for a beatmap."""

    __tablename__: str = "failtime"
    beatmap_id: int = Field(primary_key=True, foreign_key="beatmaps.id")
    exit: bytes = Field(sa_column=Column(VARBINARY(400), nullable=False))
    fail: bytes = Field(sa_column=Column(VARBINARY(400), nullable=False))

    beatmap: "Beatmap" = Relationship(back_populates="failtimes")

    @property
    def exit_(self) -> list[int]:
        return list(FAILTIME_STRUCT.unpack(self.exit))

    @property
    def fail_(self) -> list[int]:
        return list(FAILTIME_STRUCT.unpack(self.fail))

    @classmethod
    def from_resp(cls, beatmap_id: int, failtime: "FailTimeResp") -> "FailTime":
        return cls(
            beatmap_id=beatmap_id,
            exit=FAILTIME_STRUCT.pack(*failtime.exit),
            fail=FAILTIME_STRUCT.pack(*failtime.fail),
        )


class FailTimeResp(BaseModel):
    """Response model for fail/exit time data."""

    exit: list[int] = Field(default_factory=lambda: list(FAILTIME_STRUCT.unpack(b"\x00" * 400)))
    fail: list[int] = Field(default_factory=lambda: list(FAILTIME_STRUCT.unpack(b"\x00" * 400)))

    @classmethod
    def from_db(cls, failtime: FailTime) -> "FailTimeResp":
        return cls(
            exit=list(FAILTIME_STRUCT.unpack(failtime.exit)),
            fail=list(FAILTIME_STRUCT.unpack(failtime.fail)),
        )
