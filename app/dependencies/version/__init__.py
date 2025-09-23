from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header


def get_api_version(version: int | None = Header(None, alias="x-api-version")) -> int:
    if version is None:
        return 0
    if version < 1:
        raise ValueError
    return version


APIVersion = Annotated[int, Depends(get_api_version)]
