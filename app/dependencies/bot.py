from typing import Annotated

from app.router.notification.banchobot import Bot, bot

from fast_depends import Depends as DIDepends
from fastapi import Depends

BanchoBot = Annotated[Bot, Depends(lambda: bot), DIDepends(lambda: bot)]
