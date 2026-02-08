from app.config import settings
from app.models.score import GameMode

from .router import router

from pydantic import BaseModel, Field


class GameModeInfo(BaseModel):
    """游戏模式信息
    - id: 游戏模式数字ID
    - name: 游戏模式名称
    - readable: 可读的游戏模式名称
    - is_official: 是否为官方模式
    - is_custom_ruleset: 是否为自定义规则集
    """

    id: int = Field(description="游戏模式数字ID")
    name: str = Field(description="游戏模式名称")
    readable: str = Field(description="可读的游戏模式名称")
    is_official: bool = Field(description="是否为官方模式")
    is_custom_ruleset: bool = Field(description="是否为自定义规则集")


class GameModesResponse(BaseModel):
    """游戏模式列表响应
    - gamemodes: 游戏模式列表
    - total: 游戏模式总数
    - enable_rx: 是否启用了RX模式
    - enable_ap: 是否启用了AP模式
    """

    gamemodes: list[GameModeInfo] = Field(description="游戏模式列表")
    total: int = Field(description="游戏模式总数")
    enable_rx: bool = Field(description="是否启用了RX模式")
    enable_ap: bool = Field(description="是否启用了AP模式")


@router.get(
    "/gamemodes",
    response_model=GameModesResponse,
    tags=["游戏模式", "g0v0 API"],
    name="获取游戏模式列表",
    description="获取当前支持的所有游戏模式及其对应的ID列表",
)
async def get_gamemodes() -> GameModesResponse:
    """获取所有支持的游戏模式

    返回当前项目中支持的所有游戏模式，包括：
    - 官方游戏模式（osu, taiko, fruits, mania）
    - 特殊模式（osurx, osuap, taikorx, fruitsrx）
    - 自定义规则集（Sentakki, tau, rush, hishigata, soyokaze）

    同时返回RX和AP模式的启用状态。
    """
    gamemodes = []

    # 遍历所有游戏模式
    for mode in GameMode:
        gamemodes.append(
            GameModeInfo(
                id=int(mode),
                name=str(mode),
                readable=mode.readable(),
                is_official=mode.is_official(),
                is_custom_ruleset=mode.is_custom_ruleset(),
            )
        )

    # 按ID排序
    gamemodes.sort(key=lambda x: x.id)

    return GameModesResponse(
        gamemodes=gamemodes,
        total=len(gamemodes),
        enable_rx=settings.enable_rx,
        enable_ap=settings.enable_ap,
    )
