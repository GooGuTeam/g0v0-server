from app.dependencies.rate_limit import LIMITERS

from fastapi import APIRouter

router = APIRouter(prefix="/api/private", dependencies=LIMITERS)

# 导入并包含子路由
from .audio_proxy import router as audio_proxy_router
from .gamemodes import router as gamemodes_router

router.include_router(audio_proxy_router)
router.include_router(gamemodes_router)
