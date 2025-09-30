# ruff: noqa: I002
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    AliasChoices,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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


SPECTATOR_DOC = """
## 旁观服务器设置
| 变量名 | 描述 | 类型 | 默认值 |
|--------|------|--------|--------|
| `SAVE_REPLAYS` | 是否保存回放，设置为 `1` 为启用 | boolean | `0` |
| `REDIS_HOST` | Redis 服务器地址 | string | `localhost` |
| `SHARED_INTEROP_DOMAIN` | API 服务器（即本服务）地址 | string (url) | `http://localhost:8000` |
| `SERVER_PORT` | 旁观服务器端口 | integer | `8006` |
| `SP_SENTRY_DSN` | 旁观服务器的 Sentry DSN | string | `null` |
"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
        json_schema_extra={
            "paragraphs_desc": {
                "Fetcher 设置": "Fetcher 用于从 osu! 官方 API 获取数据，使用 osu! 官方 API 的 OAuth 2.0 认证",
                "监控设置": (
                    "配置应用的监控选项，如 Sentry 和 New Relic。\n\n"
                    "将 newrelic.ini 配置文件放入项目根目录即可自动启用 New Relic 监控。"
                    "如果配置文件不存在或 newrelic 包未安装，将跳过 New Relic 初始化。"
                ),
                "存储服务设置": """用于存储回放文件、头像等静态资源。

### 本地存储 (推荐用于开发环境)

本地存储将文件保存在服务器的本地文件系统中，适合开发和小规模部署。

```bash
STORAGE_SERVICE="local"
STORAGE_SETTINGS='{"local_storage_path": "./storage"}'
```

### Cloudflare R2 存储 (推荐用于生产环境)

```bash
STORAGE_SERVICE="r2"
STORAGE_SETTINGS='{
  "r2_account_id": "your_cloudflare_account_id",
  "r2_access_key_id": "your_r2_access_key_id",
  "r2_secret_access_key": "your_r2_secret_access_key",
  "r2_bucket_name": "your_bucket_name",
  "r2_public_url_base": "https://your-custom-domain.com"
}'
```

### AWS S3 存储

```bash
STORAGE_SERVICE="s3"
STORAGE_SETTINGS='{
  "s3_access_key_id": "your_aws_access_key_id",
  "s3_secret_access_key": "your_aws_secret_access_key",
  "s3_bucket_name": "your_s3_bucket_name",
  "s3_region_name": "us-east-1",
  "s3_public_url_base": "https://your-custom-domain.com"
}'
```
""",
            }
        },
    )

    # 数据库设置
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
    mysql_root_password: Annotated[
        str,
        Field(default="password", description="MySQL root 密码"),
        "数据库设置",
    ]
    redis_url: Annotated[
        str,
        Field(default="redis://127.0.0.1:6379/0", description="Redis 连接 URL"),
        "数据库设置",
    ]

    @property
    def database_url(self) -> str:
        return f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"

    # JWT 设置
    secret_key: Annotated[
        str,
        Field(
            default="your_jwt_secret_here",
            alias="jwt_secret_key",
            description="JWT 签名密钥",
        ),
        "JWT 设置",
    ]
    algorithm: Annotated[
        str,
        Field(default="HS256", alias="jwt_algorithm", description="JWT 算法"),
        "JWT 设置",
    ]
    access_token_expire_minutes: Annotated[
        int,
        Field(default=1440, description="访问令牌过期时间（分钟）"),
        "JWT 设置",
    ]
    jwt_audience: Annotated[
        str,
        Field(default="5", description="JWT 受众"),
        "JWT 设置",
    ]
    jwt_issuer: Annotated[
        str | None,
        Field(default=None, description="JWT 签发者"),
        "JWT 设置",
    ]

    # OAuth 设置
    osu_client_id: Annotated[
        int,
        Field(default=5, description="OAuth 客户端 ID"),
        "OAuth 设置",
    ]
    osu_client_secret: Annotated[
        str,
        Field(
            default="FGc9GAtyHzeQDshWP5Ah7dega8hJACAJpQtw6OXk",
            description="OAuth 客户端密钥",
        ),
        "OAuth 设置",
    ]
    osu_web_client_id: Annotated[
        int,
        Field(default=6, description="Web OAuth 客户端 ID"),
        "OAuth 设置",
    ]
    osu_web_client_secret: Annotated[
        str,
        Field(
            default="your_osu_web_client_secret_here",
            description="Web OAuth 客户端密钥",
        ),
        "OAuth 设置",
    ]

    # 服务器设置
    host: Annotated[
        str,
        Field(default="0.0.0.0", description="服务器监听地址"),
        "服务器设置",
    ]
    port: Annotated[
        int,
        Field(default=8000, description="服务器监听端口"),
        "服务器设置",
    ]
    debug: Annotated[
        bool,
        Field(default=False, description="是否启用调试模式"),
        "服务器设置",
    ]
    cors_urls: Annotated[
        list[HttpUrl],
        Field(default=[], description="额外的 CORS 允许的域名列表 (JSON 格式)"),
        "服务器设置",
    ]
    server_url: Annotated[
        HttpUrl,
        Field(
            default=HttpUrl("http://localhost:8000"),
            description="服务器 URL",
        ),
        "服务器设置",
    ]
    frontend_url: Annotated[
        HttpUrl | None,
        Field(
            default=None,
            description="前端 URL，当访问从游戏打开的 URL 时会重定向到这个 URL，为空表示不重定向",
        ),
        "服务器设置",
    ]
    enable_rate_limit: Annotated[
        bool,
        Field(default=True, description="是否启用速率限制"),
        "服务器设置",
    ]

    @property
    def web_url(self):
        if self.frontend_url is not None:
            return str(self.frontend_url)
        elif self.server_url is not None:
            return str(self.server_url)
        else:
            return "/"

    # SignalR 设置
    signalr_negotiate_timeout: Annotated[
        int,
        Field(default=30, description="SignalR 协商超时时间（秒）"),
        "SignalR 服务器设置",
    ]
    signalr_ping_interval: Annotated[
        int,
        Field(default=15, description="SignalR ping 间隔（秒）"),
        "SignalR 服务器设置",
    ]

    # Fetcher 设置
    fetcher_client_id: Annotated[
        str,
        Field(default="", description="Fetcher 客户端 ID"),
        "Fetcher 设置",
    ]
    fetcher_client_secret: Annotated[
        str,
        Field(default="", description="Fetcher 客户端密钥"),
        "Fetcher 设置",
    ]
    fetcher_scopes: Annotated[
        list[str],
        Field(default=["public"], description="Fetcher 权限范围，以逗号分隔每个权限"),
        "Fetcher 设置",
        NoDecode,
    ]

    @property
    def fetcher_callback_url(self) -> str:
        return f"{self.server_url}fetcher/callback"

    # 日志设置
    log_level: Annotated[
        str,
        Field(default="INFO", description="日志级别"),
        "日志设置",
    ]

    # 验证服务设置
    enable_totp_verification: Annotated[bool, Field(default=True, description="是否启用TOTP双因素验证"), "验证服务设置"]
    totp_issuer: Annotated[
        str | None,
        Field(default=None, description="TOTP 认证器中的发行者名称"),
        "验证服务设置",
    ]
    totp_service_name: Annotated[
        str,
        Field(default="g0v0! Lazer Server", description="TOTP 认证器中显示的服务名称"),
        "验证服务设置",
    ]
    totp_use_username_in_label: Annotated[
        bool,
        Field(default=True, description="在TOTP标签中使用用户名而不是邮箱"),
        "验证服务设置",
    ]
    enable_email_verification: Annotated[
        bool,
        Field(default=False, description="是否启用邮件验证功能"),
        "验证服务设置",
    ]
    enable_smart_verification: Annotated[
        bool,
        Field(default=True, description="是否启用智能验证（基于客户端类型和设备信任）"),
        "验证服务设置",
    ]
    enable_session_verification: Annotated[
        bool,
        Field(default=True, description="是否启用会话验证中间件"),
        "验证服务设置",
    ]
    enable_multi_device_login: Annotated[
        bool,
        Field(default=True, description="是否允许多设备同时登录"),
        "验证服务设置",
    ]
    max_tokens_per_client: Annotated[
        int,
        Field(default=10, description="每个用户每个客户端的最大令牌数量"),
        "验证服务设置",
    ]
    device_trust_duration_days: Annotated[
        int,
        Field(default=30, description="设备信任持续天数"),
        "验证服务设置",
    ]
    location_trust_duration_days: Annotated[
        int,
        Field(default=90, description="位置信任持续天数"),
        "验证服务设置",
    ]
    smtp_server: Annotated[
        str,
        Field(default="localhost", description="SMTP 服务器地址"),
        "验证服务设置",
    ]
    smtp_port: Annotated[
        int,
        Field(default=587, description="SMTP 服务器端口"),
        "验证服务设置",
    ]
    smtp_username: Annotated[
        str,
        Field(default="", description="SMTP 用户名"),
        "验证服务设置",
    ]
    smtp_password: Annotated[
        str,
        Field(default="", description="SMTP 密码"),
        "验证服务设置",
    ]
    from_email: Annotated[
        str,
        Field(default="noreply@example.com", description="发件人邮箱"),
        "验证服务设置",
    ]
    from_name: Annotated[
        str,
        Field(default="osu! server", description="发件人名称"),
        "验证服务设置",
    ]

    # 监控配置
    sentry_dsn: Annotated[
        HttpUrl | None,
        Field(default=None, description="Sentry DSN，为空不启用 Sentry"),
        "监控设置",
    ]
    new_relic_environment: Annotated[
        str | None,
        Field(default=None, description='New Relic 环境标识，设置为 "production" 或 "development"'),
        "监控设置",
    ]

    # GeoIP 配置
    maxmind_license_key: Annotated[
        str,
        Field(default="", description="MaxMind License Key（用于下载离线IP库）"),
        "GeoIP 配置",
    ]
    geoip_dest_dir: Annotated[
        str,
        Field(default="./geoip", description="GeoIP 数据库存储目录"),
        "GeoIP 配置",
    ]
    geoip_update_day: Annotated[
        int,
        Field(default=1, description="GeoIP 每周更新的星期几（0=周一，6=周日）"),
        "GeoIP 配置",
    ]
    geoip_update_hour: Annotated[
        int,
        Field(default=2, description="GeoIP 每周更新时间（小时，0-23）"),
        "GeoIP 配置",
    ]

    # 游戏设置
    enable_rx: Annotated[
        bool,
        Field(
            default=False,
            validation_alias=AliasChoices("enable_rx", "enable_osu_rx"),
            description="启用 RX mod 统计数据",
        ),
        "游戏设置",
    ]
    enable_ap: Annotated[
        bool,
        Field(
            default=False,
            validation_alias=AliasChoices("enable_ap", "enable_osu_ap"),
            description="启用 AP mod 统计数据",
        ),
        "游戏设置",
    ]
    enable_supporter_for_all_users: Annotated[
        bool,
        Field(default=False, description="启用所有新注册用户的支持者状态"),
        "游戏设置",
    ]
    enable_all_beatmap_leaderboard: Annotated[
        bool,
        Field(default=False, description="启用所有谱面的排行榜"),
        "游戏设置",
    ]
    enable_all_beatmap_pp: Annotated[
        bool,
        Field(default=False, description="允许任何谱面获得 PP"),
        "游戏设置",
    ]
    seasonal_backgrounds: Annotated[
        list[str],
        Field(default=[], description="季节背景图 URL 列表"),
        "游戏设置",
    ]
    beatmap_tag_top_count: Annotated[
        int,
        Field(default=2, description="显示在结算列表的标签所需的最低票数"),
        "游戏设置",
    ]

    # 谱面缓存设置
    enable_beatmap_preload: Annotated[
        bool,
        Field(default=True, description="启用谱面缓存预加载"),
        "缓存设置",
        "谱面缓存",
    ]
    beatmap_cache_expire_hours: Annotated[
        int,
        Field(default=24, description="谱面缓存过期时间（小时）"),
        "缓存设置",
        "谱面缓存",
    ]

    # 排行榜缓存设置
    enable_ranking_cache: Annotated[
        bool,
        Field(default=True, description="启用排行榜缓存"),
        "缓存设置",
        "排行榜缓存",
    ]
    ranking_cache_expire_minutes: Annotated[
        int,
        Field(default=10, description="排行榜缓存过期时间（分钟）"),
        "缓存设置",
        "排行榜缓存",
    ]
    ranking_cache_refresh_interval_minutes: Annotated[
        int,
        Field(default=10, description="排行榜缓存刷新间隔（分钟）"),
        "缓存设置",
        "排行榜缓存",
    ]
    ranking_cache_max_pages: Annotated[
        int,
        Field(default=20, description="最多缓存的页数"),
        "缓存设置",
        "排行榜缓存",
    ]
    ranking_cache_top_countries: Annotated[
        int,
        Field(default=20, description="缓存前N个国家的排行榜"),
        "缓存设置",
        "排行榜缓存",
    ]

    # 用户缓存设置
    enable_user_cache_preload: Annotated[
        bool,
        Field(default=True, description="启用用户缓存预加载"),
        "缓存设置",
        "用户缓存",
    ]
    user_cache_expire_seconds: Annotated[
        int,
        Field(default=300, description="用户信息缓存过期时间（秒）"),
        "缓存设置",
        "用户缓存",
    ]
    user_scores_cache_expire_seconds: Annotated[
        int,
        Field(default=60, description="用户成绩缓存过期时间（秒）"),
        "缓存设置",
        "用户缓存",
    ]
    user_beatmapsets_cache_expire_seconds: Annotated[
        int,
        Field(default=600, description="用户谱面集缓存过期时间（秒）"),
        "缓存设置",
        "用户缓存",
    ]
    user_cache_max_preload_users: Annotated[
        int,
        Field(default=200, description="最多预加载的用户数量"),
        "缓存设置",
        "用户缓存",
    ]
    user_cache_concurrent_limit: Annotated[
        int,
        Field(default=10, description="并发缓存用户的限制"),
        "缓存设置",
        "用户缓存",
    ]

    # 资源代理设置
    enable_asset_proxy: Annotated[
        bool,
        Field(default=False, description="启用资源代理"),
        "资源代理设置",
    ]
    custom_asset_domain: Annotated[
        str,
        Field(default="g0v0.top", description="自定义资源域名"),
        "资源代理设置",
    ]
    asset_proxy_prefix: Annotated[
        str,
        Field(default="assets-ppy", description="assets.ppy.sh 的自定义前缀"),
        "资源代理设置",
    ]
    avatar_proxy_prefix: Annotated[
        str,
        Field(default="a-ppy", description="a.ppy.sh 的自定义前缀"),
        "资源代理设置",
    ]
    beatmap_proxy_prefix: Annotated[
        str,
        Field(default="b-ppy", description="b.ppy.sh 的自定义前缀"),
        "资源代理设置",
    ]

    # 反作弊设置
    suspicious_score_check: Annotated[
        bool,
        Field(default=True, description="启用可疑分数检查（star>25&acc<80 或 pp>3000）"),
        "反作弊设置",
    ]
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
            ],
            description="禁止使用的用户名列表",
        ),
        "反作弊设置",
    ]

    # 存储设置
    storage_service: Annotated[
        StorageServiceType,
        Field(default=StorageServiceType.LOCAL, description="存储服务类型：local、r2、s3"),
        "存储服务设置",
    ]
    storage_settings: Annotated[
        LocalStorageSettings | CloudflareR2Settings | AWSS3StorageSettings,
        Field(default=LocalStorageSettings(), description="存储服务配置 (JSON 格式)"),
        "存储服务设置",
    ]

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


settings = Settings()  # pyright: ignore[reportCallIssue]
