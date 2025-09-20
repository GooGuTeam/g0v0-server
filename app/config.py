from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import (
    AliasChoices,
    BeforeValidator,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_list(v):
    if v is None or v == "" or str(v).strip() in ("[]", "{}"):
        return []
    if isinstance(v, list):
        return v
    s = str(v).strip()
    try:
        import json

        parsed = json.loads(s)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return [x.strip() for x in s.split(",") if x.strip()]


class AWSS3StorageSettings(BaseSettings):
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket_name: str
    s3_region_name: str
    s3_public_url_base: str | None = None


class CloudflareR2Settings(BaseSettings):
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str
    r2_public_url_base: str | None = None


class LocalStorageSettings(BaseSettings):
    local_storage_path: str = "./storage"


class StorageServiceType(str, Enum):
    LOCAL = "local"
    CLOUDFLARE_R2 = "r2"
    AWS_S3 = "s3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="allow")

    # 数据库设置
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_database: str = "osu_api"
    mysql_user: str = "osu_api"
    mysql_password: str = "password"
    mysql_root_password: str = "password"
    redis_url: str = "redis://127.0.0.1:6379/0"

    @property
    def database_url(self) -> str:
        return f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"

    # JWT 设置
    secret_key: str = Field(default="your_jwt_secret_here", alias="jwt_secret_key")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    jwt_audience: str = "5"
    jwt_issuer: str | None = None

    # OAuth 设置
    osu_client_id: int = 5
    osu_client_secret: str = "FGc9GAtyHzeQDshWP5Ah7dega8hJACAJpQtw6OXk"
    osu_web_client_id: int = 6
    osu_web_client_secret: str = "your_osu_web_client_secret_here"

    # 服务器设置
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    cors_urls: list[HttpUrl] = []
    server_url: HttpUrl = HttpUrl("http://localhost:8000")
    frontend_url: HttpUrl | None = None
    enable_rate_limit: bool = True

    @property
    def web_url(self):
        if self.frontend_url is not None:
            return str(self.frontend_url)
        elif self.server_url is not None:
            return str(self.server_url)
        else:
            return "/"

    # SignalR 设置
    signalr_negotiate_timeout: int = 30
    signalr_ping_interval: int = 15

    # Fetcher 设置
    fetcher_client_id: str = ""
    fetcher_client_secret: str = ""
    fetcher_scopes: Annotated[list[str], NoDecode] = ["public"]

    @property
    def fetcher_callback_url(self) -> str:
        return f"{self.server_url}fetcher/callback"

    # 日志设置
    log_level: str = "INFO"

    # 验证服务设置
    enable_totp_verification: bool = Field(default=True, description="是否启用TOTP双因素验证")
    totp_issuer: str | None = None
    enable_email_verification: bool = Field(default=False, description="是否启用邮件验证功能")
    smtp_server: str = "localhost"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_email: str = "noreply@example.com"
    from_name: str = "osu! server"

    # Sentry 配置
    sentry_dsn: HttpUrl | None = None

    # New Relic 配置
    new_relic_environment: None | str = None

    # GeoIP 配置
    maxmind_license_key: str = ""
    geoip_dest_dir: str = "./geoip"
    geoip_update_day: int = 1  # 每周更新的星期几（0=周一，6=周日）
    geoip_update_hour: int = 2  # 每周更新的小时数（0-23）

    # 游戏设置
    enable_rx: bool = Field(default=False, validation_alias=AliasChoices("enable_rx", "enable_osu_rx"))
    enable_ap: bool = Field(default=False, validation_alias=AliasChoices("enable_ap", "enable_osu_ap"))
    enable_all_mods_pp: bool = False
    enable_supporter_for_all_users: bool = False
    enable_all_beatmap_leaderboard: bool = False
    enable_all_beatmap_pp: bool = False
    seasonal_backgrounds: Annotated[list[str], BeforeValidator(_parse_list)] = []
    beatmap_tag_top_count: int = 2  # this is 10 in osu-web

    # 谱面缓存设置
    enable_beatmap_preload: bool = True
    beatmap_cache_expire_hours: int = 24

    # 排行榜缓存设置
    enable_ranking_cache: bool = True
    ranking_cache_expire_minutes: int = 10  # 排行榜缓存过期时间（分钟）
    ranking_cache_refresh_interval_minutes: int = 10  # 排行榜缓存刷新间隔（分钟）
    ranking_cache_max_pages: int = 20  # 最多缓存的页数
    ranking_cache_top_countries: int = 20  # 缓存前N个国家的排行榜

    # 用户缓存设置
    enable_user_cache_preload: bool = True  # 启用用户缓存预加载
    user_cache_expire_seconds: int = 300  # 用户信息缓存过期时间（秒）
    user_scores_cache_expire_seconds: int = 60  # 用户成绩缓存过期时间（秒）
    user_beatmapsets_cache_expire_seconds: int = 600  # 用户谱面集缓存过期时间（秒）
    user_cache_max_preload_users: int = 200  # 最多预加载的用户数量
    user_cache_concurrent_limit: int = 10  # 并发缓存用户的限制

    # 资源代理设置
    enable_asset_proxy: bool = True  # 启用资源代理功能
    custom_asset_domain: str = "g0v0.top"  # 自定义资源域名
    asset_proxy_prefix: str = "assets-ppy"  # assets.ppy.sh的自定义前缀
    avatar_proxy_prefix: str = "a-ppy"  # a.ppy.sh的自定义前缀
    beatmap_proxy_prefix: str = "b-ppy"  # b.ppy.sh的自定义前缀

    # 反作弊设置
    suspicious_score_check: bool = True
    banned_name: list[str] = [
        "mrekk",
        "vaxei",
        "btmc",
        "cookiezi",
        "peppy",
        "saragi",
        "chocomint",
    ]

    # 存储设置
    storage_service: StorageServiceType = StorageServiceType.LOCAL
    storage_settings: LocalStorageSettings | CloudflareR2Settings | AWSS3StorageSettings = LocalStorageSettings()

    @field_validator("fetcher_scopes", mode="before")
    def validate_fetcher_scopes(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return v.split(",")
        return v

    @field_validator("storage_settings", mode="after")
    def validate_storage_settings(
        cls,
        v: LocalStorageSettings | CloudflareR2Settings | AWSS3StorageSettings,
        info: ValidationInfo,
    ) -> LocalStorageSettings | CloudflareR2Settings | AWSS3StorageSettings:
        if info.data.get("storage_service") == StorageServiceType.CLOUDFLARE_R2:
            if not isinstance(v, CloudflareR2Settings):
                raise ValueError("When storage_service is 'r2', storage_settings must be CloudflareR2Settings")
        elif info.data.get("storage_service") == StorageServiceType.LOCAL:
            if not isinstance(v, LocalStorageSettings):
                raise ValueError("When storage_service is 'local', storage_settings must be LocalStorageSettings")
        elif info.data.get("storage_service") == StorageServiceType.AWS_S3:
            if not isinstance(v, AWSS3StorageSettings):
                raise ValueError("When storage_service is 's3', storage_settings must be AWSS3StorageSettings")
        return v


settings = Settings()
