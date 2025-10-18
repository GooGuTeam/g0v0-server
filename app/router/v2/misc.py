from datetime import UTC, datetime

from app.config import settings

from .router import router

from pydantic import BaseModel


class Background(BaseModel):
    """季节背景图单项。
    - url: 图片链接地址。"""

    url: str


class BackgroundsResp(BaseModel):
    """季节背景图返回模型。
    - ends_at: 结束时间（若为远未来表示长期有效）。
    - backgrounds: 背景图列表。"""

    ends_at: datetime = datetime(year=9999, month=12, day=31, tzinfo=UTC)
    backgrounds: list[Background]


@router.get(
    "/seasonal-backgrounds",
    response_model=BackgroundsResp,
    tags=["杂项"],
    name="获取季节背景图列表",
    description="获取当前季节背景图列表。",
)
async def get_seasonal_backgrounds():
    return BackgroundsResp(backgrounds=[Background(url=url) for url in settings.seasonal_backgrounds])
