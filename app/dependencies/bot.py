from typing import Annotated

from app.router.notification.banchobot import Bot, bot

from fast_depends import Depends as FastDepends
from fastapi import Depends

BanchoBot = Annotated[Bot, Depends(lambda: bot), FastDepends(lambda: bot)]
