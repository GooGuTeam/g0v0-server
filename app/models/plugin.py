from pydantic import BaseModel, ConfigDict, Field

META_FILENAME = "plugin.json"


class PluginMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(..., pattern=r"^[a-z0-9\-_]+$")
    name: str
    author: str
    version: str
    description: str | None = None
    dependencies: tuple[str, ...] = ()
