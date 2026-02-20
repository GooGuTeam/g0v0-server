from enum import StrEnum
from typing import Annotated, Any

from app.models.scoring_mode import ScoringMode

from pydantic import (
    AliasChoices,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


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


class StorageServiceType(StrEnum):
    LOCAL = "local"
    CLOUDFLARE_R2 = "r2"
    AWS_S3 = "s3"


class OldScoreProcessingMode(StrEnum):
    STRICT = "strict"
    NORMAL = "normal"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    # database
    mysql_host: Annotated[str, Field(default="localhost"), "database"]
    mysql_port: Annotated[int, Field(default=3306), "database"]
    mysql_database: Annotated[str, Field(default="osu_api"), "database"]
    mysql_user: Annotated[str, Field(default="osu_api"), "database"]
    mysql_password: Annotated[str, Field(default="password"), "database"]
    mysql_root_password: Annotated[str, Field(default="password"), "database"]
    redis_url: Annotated[str, Field(default="redis://127.0.0.1:6379"), "database"]

    @property
    def database_url(self) -> str:
        return f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"

    # jwt
    secret_key: Annotated[str, Field(default="your_jwt_secret_here", alias="jwt_secret_key"), "jwt"]
    algorithm: Annotated[str, Field(default="HS256", alias="jwt_algorithm"), "jwt"]
    access_token_expire_minutes: Annotated[int, Field(default=1440), "jwt"]
    refresh_token_expire_minutes: Annotated[int, Field(default=21600), "jwt"]  # 15 days
    jwt_audience: Annotated[str, Field(default="5"), "jwt"]
    jwt_issuer: Annotated[str | None, Field(default=None), "jwt"]

    # oauth
    osu_client_id: Annotated[int, Field(default=5), "oauth"]
    osu_client_secret: Annotated[str, Field(default="FGc9GAtyHzeQDshWP5Ah7dega8hJACAJpQtw6OXk"), "oauth"]
    osu_web_client_id: Annotated[int, Field(default=6), "oauth"]
    osu_web_client_secret: Annotated[str, Field(default="your_osu_web_client_secret_here"), "oauth"]

    # server
    host: Annotated[str, Field(default="0.0.0.0"), "server"]  # noqa: S104
    port: Annotated[int, Field(default=8000), "server"]
    debug: Annotated[bool, Field(default=False), "server"]
    cors_urls: Annotated[list[HttpUrl], Field(default=[]), "server"]
    server_url: Annotated[HttpUrl, Field(default=HttpUrl("http://localhost:8000")), "server"]
    frontend_url: Annotated[HttpUrl | None, Field(default=None), "server"]
    enable_rate_limit: Annotated[bool, Field(default=True), "server"]

    @property
    def web_url(self):
        if self.frontend_url is not None:
            return str(self.frontend_url)
        elif self.server_url is not None:
            return str(self.server_url)
        else:
            return "/"

    # fetcher
    fetcher_client_id: Annotated[str, Field(default=""), "fetcher"]
    fetcher_client_secret: Annotated[str, Field(default=""), "fetcher"]

    # NOTE: Reserve for user-based-fetcher

    # fetcher_scopes: Annotated[
    #     list[str],
    #     Field(default=["public"]),
    #     "Fetcher 设置",
    #     NoDecode,
    # ]

    # @field_validator("fetcher_scopes", mode="before")
    # @classmethod
    # def validate_fetcher_scopes(cls, v: Any) -> list[str]:
    #     if isinstance(v, str):
    #         return v.split(",")
    #     return v

    # @property
    # def fetcher_callback_url(self) -> str:
    #     return f"{self.server_url}fetcher/callback"

    # logging
    log_level: Annotated[str, Field(default="INFO"), "logging"]

    # verification
    enable_totp_verification: Annotated[bool, Field(default=True), "verification"]
    totp_issuer: Annotated[str | None, Field(default=None), "verification"]
    totp_service_name: Annotated[str, Field(default="g0v0! Lazer Server"), "verification"]
    totp_use_username_in_label: Annotated[bool, Field(default=True), "verification"]
    enable_turnstile_verification: Annotated[bool, Field(default=False), "verification"]
    turnstile_secret_key: Annotated[str, Field(default=""), "verification"]
    turnstile_dev_mode: Annotated[bool, Field(default=False), "verification"]
    enable_email_verification: Annotated[bool, Field(default=False), "verification"]
    enable_session_verification: Annotated[bool, Field(default=True), "verification"]
    enable_multi_device_login: Annotated[bool, Field(default=True), "verification"]
    max_tokens_per_client: Annotated[int, Field(default=10), "verification"]
    device_trust_duration_days: Annotated[int, Field(default=30), "verification"]

    email_provider: Annotated[str, Field(default="smtp"), "email"]
    email_provider_config: Annotated[dict, Field(default_factory=dict), "email"]
    from_email: Annotated[str, Field(default="noreply@example.com"), "email"]
    from_name: Annotated[str, Field(default="osu! server"), "email"]

    # monitoring
    sentry_dsn: Annotated[HttpUrl | None, Field(default=None), "monitoring"]
    new_relic_environment: Annotated[str | None, Field(default=None), "monitoring"]

    # geoip
    maxmind_license_key: Annotated[str, Field(default=""), "geoip"]
    geoip_dest_dir: Annotated[str, Field(default="./geoip"), "geoip"]
    geoip_update_day: Annotated[int, Field(default=1), "geoip"]
    geoip_update_hour: Annotated[int, Field(default=2), "geoip"]

    # game
    enable_rx: Annotated[
        bool, Field(default=False, validation_alias=AliasChoices("enable_rx", "enable_osu_rx")), "game"
    ]
    enable_ap: Annotated[
        bool, Field(default=False, validation_alias=AliasChoices("enable_ap", "enable_osu_ap")), "game"
    ]
    enable_supporter_for_all_users: Annotated[bool, Field(default=False), "game"]
    enable_all_beatmap_leaderboard: Annotated[bool, Field(default=False), "game"]
    enable_all_beatmap_pp: Annotated[bool, Field(default=False), "game"]
    seasonal_backgrounds: Annotated[list[str], Field(default=[]), "game"]
    beatmap_tag_top_count: Annotated[int, Field(default=2), "game"]
    old_score_processing_mode: Annotated[OldScoreProcessingMode, Field(default=OldScoreProcessingMode.NORMAL), "game"]
    scoring_mode: Annotated[ScoringMode, Field(default=ScoringMode.STANDARDISED), "game"]

    # calculator
    calculator: Annotated[str, Field(default="performance_server"), "calculator"]
    calculator_config: Annotated[dict[str, Any], Field(default={"server_url": "http://localhost:5225"}), "calculator"]
    fallback_no_calculator_pp: Annotated[bool, Field(default=False), "calculator"]

    # cache - beatmap
    enable_beatmap_preload: Annotated[bool, Field(default=True), "cache"]
    beatmap_cache_expire_hours: Annotated[int, Field(default=24), "cache"]
    beatmapset_cache_expire_seconds: Annotated[int, Field(default=3600), "cache"]

    # cache - ranking
    enable_ranking_cache: Annotated[bool, Field(default=True), "cache"]
    ranking_cache_expire_minutes: Annotated[int, Field(default=10), "cache"]
    ranking_cache_refresh_interval_minutes: Annotated[int, Field(default=10), "cache"]
    ranking_cache_max_pages: Annotated[int, Field(default=20), "cache"]
    ranking_cache_top_countries: Annotated[int, Field(default=20), "cache"]

    # cache - user
    enable_user_cache_preload: Annotated[bool, Field(default=True), "cache"]
    user_cache_expire_seconds: Annotated[int, Field(default=300), "cache"]
    user_scores_cache_expire_seconds: Annotated[int, Field(default=60), "cache"]
    user_beatmapsets_cache_expire_seconds: Annotated[int, Field(default=600), "cache"]
    user_cache_max_preload_users: Annotated[int, Field(default=200), "cache"]

    # asset_proxy
    enable_asset_proxy: Annotated[bool, Field(default=False), "asset_proxy"]
    custom_asset_domain: Annotated[str, Field(default="g0v0.top"), "asset_proxy"]
    asset_proxy_prefix: Annotated[str, Field(default="assets-ppy"), "asset_proxy"]
    avatar_proxy_prefix: Annotated[str, Field(default="a-ppy"), "asset_proxy"]
    beatmap_proxy_prefix: Annotated[str, Field(default="b-ppy"), "asset_proxy"]

    # beatmap_sync
    enable_auto_beatmap_sync: Annotated[bool, Field(default=False), "beatmap_sync"]
    beatmap_sync_interval_minutes: Annotated[int, Field(default=60), "beatmap_sync"]

    # anticheat
    banned_name: Annotated[
        list[str],
        Field(
            default=[
                "mrekk",
                "vaxei",
                "btmc",
                "cookiezi",
                "peppy",
                "saragi",
                "chocomint",
            ]
        ),
        "anticheat",
    ]
    allow_delete_scores: Annotated[bool, Field(default=False), "anticheat"]
    check_ruleset_version: Annotated[bool, Field(default=True), "anticheat"]
    check_client_version: Annotated[bool, Field(default=True), "anticheat"]
    client_version_urls: Annotated[
        list[str],
        Field(default=["https://raw.githubusercontent.com/GooGuTeam/g0v0-client-versions/main/version_list.json"]),
        "anticheat",
    ]

    # storage
    storage_service: Annotated[StorageServiceType, Field(default=StorageServiceType.LOCAL), "storage"]
    storage_settings: Annotated[
        LocalStorageSettings | CloudflareR2Settings | AWSS3StorageSettings,
        Field(default=LocalStorageSettings()),
        "storage",
    ]

    plugin_dirs: Annotated[list[str], Field(default=["./plugins"]), "plugins"]
    disabled_plugins: Annotated[list[str], Field(default=[]), "plugins"]

    @field_validator("storage_settings", mode="after")
    @classmethod
    def validate_storage_settings(
        cls,
        v: LocalStorageSettings | CloudflareR2Settings | AWSS3StorageSettings,
        info: ValidationInfo,
    ) -> LocalStorageSettings | CloudflareR2Settings | AWSS3StorageSettings:
        service = info.data.get("storage_service")
        if service == StorageServiceType.CLOUDFLARE_R2 and not isinstance(v, CloudflareR2Settings):
            raise ValueError("When storage_service is 'r2', storage_settings must be CloudflareR2Settings")
        if service == StorageServiceType.LOCAL and not isinstance(v, LocalStorageSettings):
            raise ValueError("When storage_service is 'local', storage_settings must be LocalStorageSettings")
        if service == StorageServiceType.AWS_S3 and not isinstance(v, AWSS3StorageSettings):
            raise ValueError("When storage_service is 's3', storage_settings must be AWSS3StorageSettings")
        return v


settings = Settings()  # pyright: ignore[reportCallIssue]
