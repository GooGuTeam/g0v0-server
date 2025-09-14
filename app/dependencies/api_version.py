from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header


def get_api_version(version: str | None = Header(None, alias="x-api-version")) -> int:
    if version is None:
        return 0
    try:
        version_int = int(version)
        if version_int < 1:
            raise ValueError
        return version_int
    except ValueError:
        raise ValueError("Invalid API version header")


APIVersion = Annotated[int, Depends(get_api_version)]
