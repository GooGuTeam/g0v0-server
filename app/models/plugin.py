from pydantic import BaseModel, ConfigDict

META_FILENAME = "plugin.json"


class PluginMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    author: str
    version: str
    description: str | None = None
    dependencies: tuple[str, ...] = ()
