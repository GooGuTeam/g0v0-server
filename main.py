from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from app.config import settings
from app.database import User
from app.dependencies.database import Database, engine, get_redis, redis_client
from app.dependencies.fetcher import get_fetcher
from app.dependencies.scheduler import start_scheduler, stop_scheduler
from app.log import logger
from app.router import (
    api_v1_router,
    api_v2_router,
    auth_router,
    chat_router,
    fetcher_router,
    file_router,
    lio_router,
    private_router,
    redirect_api_router,
)
from app.router.redirect import redirect_router
from app.router.v1 import api_v1_public_router
from app.scheduler.cache_scheduler import start_cache_scheduler, stop_cache_scheduler
from app.scheduler.database_cleanup_scheduler import (
    start_database_cleanup_scheduler,
    stop_database_cleanup_scheduler,
)
from app.service.achievements.load_achievements import load_achievements
from app.service.assets.beatmap_download_service import download_service
from app.service.bootstrap.create_banchobot import create_banchobot
from app.service.email.email_queue import start_email_processor, stop_email_processor
from app.service.geoip.geoip_scheduler import schedule_geoip_updates
from app.service.geoip.init_geoip import init_geoip
from app.service.messaging.redis_message_system import redis_message_system
from app.service.ranking.calculate_all_user_rank import calculate_user_rank
from app.service.ranking.osu_rx_statistics import create_rx_statistics
from app.service.rooms.daily_challenge import (
    daily_challenge_job,
    process_daily_challenge_top,
)
from app.utils import bg_tasks, utcnow

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi_limiter import FastAPILimiter
import sentry_sdk


@asynccontextmanager
async def lifespan(app: FastAPI):
    # on startup
    await FastAPILimiter.init(get_redis())
    await get_fetcher()  # 初始化 fetcher
    await init_geoip()  # 初始化 GeoIP 数据库
    await create_rx_statistics()
    await calculate_user_rank(True)
    start_scheduler()
    schedule_geoip_updates()  # 调度 GeoIP 定时更新任务
    await daily_challenge_job()
    await process_daily_challenge_top()
    await create_banchobot()
    await start_email_processor()  # 启动邮件队列处理器
    await download_service.start_health_check()  # 启动下载服务健康检查
    await start_cache_scheduler()  # 启动缓存调度器
    await start_database_cleanup_scheduler()  # 启动数据库清理调度器
    redis_message_system.start()  # 启动 Redis 消息系统
    load_achievements()

    # 显示资源代理状态
    if settings.enable_asset_proxy:
        logger.info(f"Asset Proxy enabled - Domain: {settings.custom_asset_domain}")

    # on shutdown
    yield
    bg_tasks.stop()
    stop_scheduler()
    redis_message_system.stop()  # 停止 Redis 消息系统
    await stop_cache_scheduler()  # 停止缓存调度器
    await stop_database_cleanup_scheduler()  # 停止数据库清理调度器
    await download_service.stop_health_check()  # 停止下载服务健康检查
    await stop_email_processor()  # 停止邮件队列处理器
    await engine.dispose()
    await redis_client.aclose()


desc = f"""osu! API 模拟服务器，支持 osu! API v1, v2 和 osu!lazer 的绝大部分功能。

## 端点说明

所有 v2 API 均以 `/api/v2/` 开头，所有 v1 API 均以 `/api/v1/` 开头（直接访问 `/api` 的 v1 API 会进行重定向）。

所有 g0v0-server 提供的额外 API（g0v0-api） 均以 `/api/private/` 开头。

## 鉴权

v2 API 采用 OAuth 2.0 鉴权，支持以下鉴权方式：

- `password` 密码鉴权，仅适用于 osu!lazer 客户端和前端等服务，需要提供用户的用户名和密码进行登录。
- `authorization_code` 授权码鉴权，适用于第三方应用，需要提供用户的授权码进行登录。
- `client_credentials` 客户端凭证鉴权，适用于服务端应用，需要提供客户端 ID 和客户端密钥进行登录。

使用 `password` 鉴权的具有全部权限。`authorization_code` 具有指定 scope 的权限。`client_credentials` 只有 `public` 权限。各接口需要的权限请查看每个 Endpoint 的 Authorization。

v1 API 采用 API Key 鉴权，将 API Key 放入 Query `k` 中。

{
    '''
## 速率限制

所有 API 请求均受到速率限制，具体限制规则如下：

- 每分钟最多可以发送 1200 个请求
- 突发请求限制为每秒最多 200 个请求

此外，下载回放 API (`/api/v1/get_replay`, `/api/v2/scores/{score_id}/download`) 的速率限制为每分钟最多 10 个请求。
'''
    if settings.enable_rate_limit
    else ""
}

## 参考

- v2 API 文档：[osu-web 文档](https://osu.ppy.sh/docs/index.html)
- v1 API 文档：[osu-api](https://github.com/ppy/osu-api/wiki)
"""  # noqa: E501

# 检查 New Relic 配置文件是否存在，如果存在则初始化 New Relic
newrelic_config_path = Path("newrelic.ini")
if newrelic_config_path.exists():
    try:
        import newrelic.agent

        environment = settings.new_relic_environment or ("production" if not settings.debug else "development")

        newrelic.agent.initialize(newrelic_config_path, environment)
        logger.info(f"[NewRelic] Enabled, environment: {environment}")
    except ImportError:
        logger.warning("[NewRelic] Config file found but 'newrelic' package is not installed")
    except Exception as e:
        logger.error(f"[NewRelic] Initialization failed: {e}")
else:
    logger.info("[NewRelic] No newrelic.ini config file found, skipping initialization")

if settings.sentry_dsn is not None:
    sentry_sdk.init(
        dsn=str(settings.sentry_dsn),
        send_default_pii=False,
        environment="production" if not settings.debug else "development",
    )

app = FastAPI(
    title="g0v0-server",
    version="0.1.0",
    lifespan=lifespan,
    description=desc,
)


app.include_router(api_v2_router)
app.include_router(api_v1_router)
app.include_router(api_v1_public_router)
app.include_router(chat_router)
app.include_router(redirect_api_router)
app.include_router(fetcher_router)
app.include_router(file_router)
app.include_router(auth_router)
app.include_router(private_router)
app.include_router(lio_router)

# from app.signalr import signalr_router
# app.include_router(signalr_router)

# CORS 配置
origins = []
for url in [*settings.cors_urls, settings.server_url]:
    origins.append(str(url))
    origins.append(str(url).removesuffix("/"))
if settings.frontend_url:
    origins.append(str(settings.frontend_url))
    origins.append(str(settings.frontend_url).removesuffix("/"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.frontend_url is not None:
    app.include_router(redirect_router)


@app.get("/users/{user_id}/avatar", include_in_schema=False)
async def get_user_avatar_root(
    user_id: int,
    session: Database,
):
    """用户头像重定向端点 (根路径)"""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    avatar_url = user.avatar_url
    if not avatar_url:
        avatar_url = "https://lazer.g0v0.top/default.jpg"

    separator = "&" if "?" in avatar_url else "?"
    avatar_url_with_timestamp = f"{avatar_url}{separator}"

    return RedirectResponse(url=avatar_url_with_timestamp, status_code=301)


@app.get("/", include_in_schema=False)
async def root():
    """根端点"""
    return {"message": "osu! API 模拟服务器正在运行"}


@app.get("/health", include_in_schema=False)
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "timestamp": utcnow().isoformat()}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": exc.errors(),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(requst: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


if settings.secret_key == "your_jwt_secret_here":
    logger.warning("jwt_secret_key is unset. Your server is unsafe. Use this command to generate: openssl rand -hex 32")
if settings.osu_web_client_secret == "your_osu_web_client_secret_here":
    logger.warning(
        "osu_web_client_secret is unset. Your server is unsafe. Use this command to generate: openssl rand -hex 40"
    )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=None,  # 禁用uvicorn默认日志配置
        access_log=True,  # 启用访问日志
    )
