from typing import Annotated

from app.plugins import (
    EventHub as OriginalEventHub,
    hub,
)

from fast_depends import Depends as DIDepends
from fastapi import Depends

EventHub = Annotated[OriginalEventHub, Depends(lambda: hub), DIDepends(lambda: hub)]
