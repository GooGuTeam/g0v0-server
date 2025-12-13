from pydantic import BaseModel


class PluginMeta(BaseModel):
    id: str
    name: str
    author: str
    version: str
    description: str | None = None
    dependencies: list[str] = []
