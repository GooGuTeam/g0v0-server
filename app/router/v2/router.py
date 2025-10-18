from app.dependencies.rate_limit import LIMITERS

from fastapi import APIRouter

router = APIRouter(prefix="/api/v2", dependencies=LIMITERS)
