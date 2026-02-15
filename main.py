from contextlib import asynccontextmanager
import json
from pathlib import Path
import time

from app.calculating import init_calculator
from app.config import settings
from app.database import User
from app.dependencies.database import (
    Database,
    engine,
    redis_binary_client,
    redis_client,
    redis_message_client,
)
from app.dependencies.fetcher import get_fetcher
from app.dependencies.scheduler import start_scheduler, stop_scheduler
from app.helpers import bg_tasks, utcnow
from app.log import system_logger
from app.middleware.verify_session import VerifySessionMiddleware
from app.models.error import RequestError
from app.models.events.http import RequestHandledEvent, RequestReceivedEvent
from app.models.mods import init_mods, init_ranked_mods
from app.models.score import init_ruleset_version_hash
from app.plugins import event_hub, plugin_manager, plugin_router
from app.router import (
    api_v1_router,
    api_v2_router,
    auth_router,
    chat_router,
    file_router,
    lio_router,
    private_router,
    redirect_api_router,
)
from app.router.redirect import redirect_router
from app.router.v1 import api_v1_public_router
from app.service.beatmap_download_service import download_service
from app.service.beatmapset_update_service import init_beatmapset_update_service
from app.service.client_verification_service import init_client_verification_service
from app.service.email_queue import start_email_processor, stop_email_processor
from app.service.redis_message_system import redis_message_system
from app.service.subscribers.user_cache import user_online_subscriber
from app.tasks import (
    calculate_user_rank,
    create_banchobot,
    create_custom_ruleset_statistics,
    create_rx_statistics,
    daily_challenge_job,
    init_geoip,
    load_achievements,
    process_daily_challenge_top,
    start_cache_tasks,
    stop_cache_tasks,
)

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
import sentry_sdk


@asynccontextmanager
async def lifespan(app: FastAPI):
    # === on startup ===
    # init mods, achievements and performance calculator
    plugin_manager.load_all_plugins()
    app.include_router(plugin_router)

    init_mods()
    init_ranked_mods()
    init_ruleset_version_hash()
    load_achievements()
    await init_calculator()

    if settings.check_client_version:
        await init_client_verification_service()

    # init fetcher
    fetcher = await get_fetcher()
    # init GeoIP
    await init_geoip()

    # init game server
    await create_rx_statistics()
    await create_custom_ruleset_statistics()
    await calculate_user_rank(True)
    await daily_challenge_job()
    await process_daily_challenge_top()
    await create_banchobot()

    # services
    await start_email_processor()
    await download_service.start_health_check()
    await start_cache_tasks()
    init_beatmapset_update_service(fetcher)  # 初始化谱面集更新服务
    redis_message_system.start()
    start_scheduler()
    await user_online_subscriber.start_subscribe()

    # show the status of AssetProxy
    if settings.enable_asset_proxy:
        system_logger("AssetProxy").info(f"Asset Proxy enabled - Domain: {settings.custom_asset_domain}")

    yield

    # === on shutdown ===
    # stop services
    bg_tasks.stop()
    await stop_cache_tasks()
    stop_scheduler()
    await download_service.stop_health_check()
    await stop_email_processor()

    # close database & redis
    await engine.dispose()
    await redis_client.aclose()
    await redis_binary_client.aclose()
    await redis_message_client.aclose()


desc = f"""g0v0-server is an osu!(lazer) server written in Python, supporting the latest osu!(lazer) client and providing additional features (e.g., Relax/Autopilot Mod statistics, custom ruleset support).

g0v0-server is implemented based on osu! API v2, achieving compatibility with the vast majority of osu! API v1 and v2. This means you can easily integrate existing osu! applications into g0v0-server.

Meanwhile, g0v0-server also provides a series of g0v0! APIs to implement operations for other functionalities outside of the osu! API.

g0v0-server is not just a score server. It implements most of the osu! website features (e.g., chat, user settings, etc.).

If you want to develop this or to run another instance, please check our [documentation](https://docs.g0v0.top/). If you are confused about this project, welcome to our [Discord server](https://discord.gg/AhzJXXWYfF) to seek answers.

g0v0-server is developed by [GooGuTeam](https://github.com/GooGuTeam) and licensed under **GNU Affero General Public License v3.0 (AGPL-3.0-only)**. Any derivative work, modification, or deployment **MUST** clearly and prominently attribute the original authors:
> GooGuTeam - https://github.com/GooGuTeam/g0v0-server

## Endpoint Specifications

All v2 APIs begin with `/api/v2/`, while all v1 APIs start with `/api/v1/` (direct access to `/api` for v1 APIs will redirect).
All additional APIs provided by g0v0-server (g0v0-api) begin with `/api/private/`. All additional APIs provided by plugins begin with `/api/plugins/<plugin-id>/`

## Authentication

v2 APIs use OAuth 2.0 authentication and support the following methods:
- `password`: Password authentication, applicable only to services like the osu!lazer client and frontend. Requires providing the user's username and password for login.
- `authorization_code`: Authorization code authentication, suitable for third-party applications. Requires providing the user's authorization code for login.
- `client_credentials`: Client credentials authentication for server-side applications, requiring the client ID and client secret for login.
`password` authentication grants full permissions. `authorization_code` grants permissions for specified scopes. `client_credentials` grants only `public` permissions. Refer to each Endpoint's Authorization section for specific permission requirements.

v1 API uses API Key authentication. Place the API Key in the Query `k` field.

{
    '''
## Rate Limiting

All API requests are subject to rate limiting. Specific restrictions are as follows:

- Maximum of 1200 requests per minute
- Burst requests capped at 200 requests per second

Additionally, the download replay API (`/api/v1/get_replay`, `/api/v2/scores/{score_id}/download`) is rate-limited to 10 requests per minute.
'''
    if settings.enable_rate_limit
    else ""
}

## References
- v2 API Documentation: [osu-web Documentation](https://osu.ppy.sh/docs/index.html)
- v1 API Documentation: [osu-api](https://github.com/ppy/osu-api/wiki)
"""  # noqa: E501

# 检查 New Relic 配置文件是否存在，如果存在则初始化 New Relic
newrelic_config_path = Path("newrelic.ini")
if newrelic_config_path.is_file():
    try:
        import newrelic.agent

        environment = settings.new_relic_environment or ("production" if not settings.debug else "development")

        newrelic.agent.initialize(newrelic_config_path, environment)
        system_logger("NewRelic").info(f"Enabled, environment: {environment}")
    except Exception as e:
        system_logger("NewRelic").error(f"Initialization failed: {e}")

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
# app.include_router(fetcher_router)
app.include_router(file_router)
app.include_router(auth_router)
app.include_router(private_router)
app.include_router(lio_router)

# 会话验证中间件
if settings.enable_session_verification:
    app.add_middleware(VerifySessionMiddleware)

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
    if settings.frontend_url:
        return RedirectResponse(url=str(settings.frontend_url), status_code=302)
    return {"message": "g0v0-server is running"}


@app.get("/health", include_in_schema=False)
async def health_check():
    return {"status": "ok", "timestamp": utcnow().isoformat()}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):  # noqa: ARG001
    return JSONResponse(
        status_code=422,
        content={
            "error": json.dumps(exc.errors()),
        },
    )


@app.exception_handler(RequestError)
async def request_error_handler(request: Request, exc: RequestError):  # noqa: ARG001
    content = {
        "error": exc.formatted_message,
        "msg_key": exc.msg_key,
    }

    content.update(exc.details)
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(exc_class_or_status_code=HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):  # noqa: ARG001
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    event_hub.emit(RequestReceivedEvent(time=time.time(), request=request))
    response = await call_next(request)
    event_hub.emit(RequestHandledEvent(time=time.time(), request=request, response=response))
    return response


if settings.secret_key == "your_jwt_secret_here":  # noqa: S105
    raise RuntimeError(
        "jwt_secret_key is unset. Your server is unsafe. Use this command to generate: openssl rand -hex 32"
    )
if settings.osu_web_client_secret == "your_osu_web_client_secret_here":  # noqa: S105
    system_logger("Security").opt(colors=True).warning(
        "<y>osu_web_client_secret</y> is unset. Your server is unsafe. "
        "Use this command to generate: <blue>openssl rand -hex 40</blue>."
    )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=None,
        access_log=True,
    )
