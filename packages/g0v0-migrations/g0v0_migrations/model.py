from pathlib import Path
from typing import Annotated, TypedDict

from alembic.config import Config as AlembicConfig
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings.main import SettingsConfigDict


class G0v0ServerDatabaseConfig(BaseSettings):
    model_config: SettingsConfigDict = SettingsConfigDict(
        extra="ignore",
        env_file_encoding="utf-8",
    )

    mysql_host: Annotated[
        str,
        Field(default="localhost", description="MySQL 服务器地址"),
        "数据库设置",
    ]
    mysql_port: Annotated[
        int,
        Field(default=3306, description="MySQL 服务器端口"),
        "数据库设置",
    ]
    mysql_database: Annotated[
        str,
        Field(default="osu_api", description="MySQL 数据库名称"),
        "数据库设置",
    ]
    mysql_user: Annotated[
        str,
        Field(default="osu_api", description="MySQL 用户名"),
        "数据库设置",
    ]
    mysql_password: Annotated[
        str,
        Field(default="password", description="MySQL 密码"),
        "数据库设置",
    ]

    @property
    def database_url(self) -> str:
        return f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"


class ContextObject(TypedDict):
    g0v0_server_path: Path
    plugin_path: Path | None
    alembic_config: AlembicConfig
    plugin_id: str | None
