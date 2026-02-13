from typing import Annotated

from app.plugins import (
    EventHub as OriginalEventHub,
    event_hub,
)

from fast_depends import Depends as DIDepends
from fastapi import Depends

EventHub = Annotated[OriginalEventHub, Depends(lambda: event_hub), DIDepends(lambda: event_hub)]
