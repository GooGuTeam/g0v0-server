from typing import Annotated

from fast_depends import Depends as FastDepends
from fastapi import Depends, Header


def get_api_version(version: int | None = Header(None, alias="x-api-version", include_in_schema=False)) -> int:
    if version is None:
        return 0
    if version < 1:
        raise ValueError
    return version


APIVersion = Annotated[int, Depends(get_api_version), FastDepends(get_api_version)]
