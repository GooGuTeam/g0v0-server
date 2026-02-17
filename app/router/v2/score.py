"""Score endpoints for osu! API v2.

This module provides endpoints for score submission, leaderboards, pinned scores,
replay downloads, and room/playlist score management.
"""

from datetime import UTC, date
import time
from typing import Annotated

from app.calculating import clamp
from app.config import settings
from app.database import (
    Beatmap,
    Playlist,
    Room,
    Score,
    ScoreToken,
    ScoreTokenResp,
    User,
)
from app.database.achievement import process_achievements
from app.database.counts import ReplayWatchedCount
from app.database.daily_challenge import process_daily_challenge_score
from app.database.item_attempts_count import ItemAttemptsCount
from app.database.playlist_best_score import (
    PlaylistBestScore,
    get_position,
    process_playlist_best_score,
)
from app.database.relationship import Relationship, RelationshipType
from app.database.score import (
    LegacyScoreResp,
    MultiplayerScores,
    MultiplayScoreDict,
    ScoreModel,
    get_leaderboard,
    get_score_position_by_id,
    process_score,
    process_user,
)
from app.dependencies.api_version import APIVersion
from app.dependencies.cache import UserCacheService
from app.dependencies.client_verification import ClientVerificationService
from app.dependencies.database import Database, Redis, get_redis, with_db
from app.dependencies.fetcher import Fetcher, get_fetcher
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser, get_current_user
from app.helpers import api_doc, utcnow
from app.log import log
from app.models.beatmap import BeatmapRankStatus
from app.models.error import ErrorType, RequestError
from app.models.events.score import (
    MultiplayerScoreCreatedEvent,
    MultiplayerScoreSubmittedEvent,
    ReplayDownloadedEvent,
    ScoreProcessedEvent,
    ScoreType,
    SoloScoreCreatedEvent,
    SoloScoreSubmittedEvent,
)
from app.models.room import RoomCategory
from app.models.score import (
    GameMode,
    LeaderboardType,
    Rank,
    SoloScoreSubmissionInfo,
)
from app.plugins import hub
from app.service.beatmap_cache_service import get_beatmap_cache_service
from app.service.user_cache_service import refresh_user_cache_background

from .router import router

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    Form,
    Path,
    Query,
    Security,
)
from fastapi.responses import RedirectResponse
from fastapi_limiter.depends import RateLimiter
from httpx import HTTPError
from pydantic import BaseModel
from pyrate_limiter import Duration, Limiter, Rate
from sqlalchemy.orm import joinedload
from sqlmodel import col, exists, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

READ_SCORE_TIMEOUT = 10
DEFAULT_SCORE_INCLUDES = ["user", "user.country", "user.cover", "user.team"]
logger = log("Score")


async def _process_user_achievement(score_id: int):
    """Process achievements for a submitted score.

    Args:
        score_id: The score ID to process achievements for.
    """
    async with with_db() as session:
        await process_achievements(session, get_redis(), score_id)


async def _process_user(score_id: int, user_id: int, redis: Redis, fetcher: Fetcher):
    """Process user statistics after score submission.

    Args:
        score_id: The submitted score ID.
        user_id: The user ID who submitted the score.
        redis: Redis connection.
        fetcher: Fetcher service for external data.
    """
    async with with_db() as session:
        user = await session.get(User, user_id)
        if not user:
            logger.warning(
                "User {user_id} not found when processing score {score_id}", user_id=user_id, score_id=score_id
            )
            return
        score = await session.get(Score, score_id)
        if not score:
            logger.warning(
                "Score {score_id} not found when processing user {user_id}", score_id=score_id, user_id=user_id
            )
            return
        gamemode = score.gamemode
        score_token = (await session.exec(select(ScoreToken.id).where(ScoreToken.score_id == score_id))).first()
        if not score_token:
            logger.warning(
                "ScoreToken for score {score_id} not found when processing user {user_id}",
                score_id=score_id,
                user_id=user_id,
            )
            return
        beatmap = (
            await session.exec(
                select(Beatmap.total_length, Beatmap.beatmap_status).where(Beatmap.id == score.beatmap_id)
            )
        ).first()
        if not beatmap:
            logger.warning(
                "Beatmap {beatmap_id} not found when processing user {user_id} for score {score_id}",
                beatmap_id=score.beatmap_id,
                user_id=user_id,
                score_id=score_id,
            )
            return
        await process_user(session, redis, fetcher, user, score, score_token, beatmap[0], BeatmapRankStatus(beatmap[1]))
        await refresh_user_cache_background(redis, user_id, gamemode)
        await redis.publish("osu-channel:score:processed", f'{{"ScoreId": {score_id}}}')
        hub.emit(ScoreProcessedEvent(score_id=score_id))


async def submit_score(
    background_task: BackgroundTasks,
    info: SoloScoreSubmissionInfo,
    token: int,
    current_user: User,
    db: AsyncSession,
    redis: Redis,
    fetcher: Fetcher,
):
    """Submit a score using a score token.

    Args:
        background_task: Background tasks handler.
        info: Score submission information.
        token: Score token ID.
        current_user: The authenticated user.
        db: Database session.
        redis: Redis connection.
        fetcher: Fetcher service for external data.

    Returns:
        dict: The submitted score response.

    Raises:
        RequestError: If token not found, score not found, or beatmap not found.
    """
    # Get user ID immediately to avoid lazy loading issues
    user_id = current_user.id

    if not info.passed:
        info.rank = Rank.F
    score_token = (
        await db.exec(select(ScoreToken).options(joinedload(ScoreToken.beatmap)).where(ScoreToken.id == token))
    ).first()
    if not score_token or score_token.user_id != user_id:
        raise RequestError(ErrorType.SCORE_TOKEN_NOT_FOUND)
    if score_token.score_id:
        score = (
            await db.exec(
                select(Score).where(
                    Score.id == score_token.score_id,
                    Score.user_id == user_id,
                )
            )
        ).first()
        if not score:
            raise RequestError(ErrorType.SCORE_NOT_FOUND)
    else:
        beatmap = score_token.beatmap_id
        try:
            cache_service = get_beatmap_cache_service(redis, fetcher)
            await cache_service.smart_preload_for_score(beatmap)
        except Exception as e:
            logger.debug(f"Beatmap preload failed for {beatmap}: {e}")

        try:
            db_beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=beatmap)
        except HTTPError:
            raise RequestError(ErrorType.BEATMAP_NOT_FOUND)
        status = db_beatmap.beatmap_status
        score = await process_score(
            current_user,
            beatmap,
            status.has_pp() or settings.enable_all_beatmap_pp,
            score_token,
            info,
            db,
        )
        await db.refresh(score_token)
        score_id = score.id
        score_token.score_id = score_id
        await db.commit()
        await db.refresh(score)

    resp = await ScoreModel.transform(
        score,
    )
    await db.commit()
    background_task.add_task(_process_user_achievement, resp["id"])
    background_task.add_task(_process_user, resp["id"], user_id, redis, fetcher)
    return resp


async def _preload_beatmap_for_pp_calculation(beatmap_id: int) -> None:
    """Pre-cache beatmap file to speed up PP calculation.

    When a player starts playing, asynchronously preload the beatmap raw file to Redis cache.

    Args:
        beatmap_id: The beatmap ID to preload.
    """
    # Check if beatmap preload feature is enabled
    if not settings.enable_beatmap_preload:
        return

    try:
        # Asynchronously get fetcher and redis connection
        fetcher = await get_fetcher()
        redis = get_redis()

        # Check if already cached to avoid duplicate downloads
        cache_key = f"beatmap:raw:{beatmap_id}"
        if await redis.exists(cache_key):
            logger.debug(f"Beatmap {beatmap_id} already cached, skipping preload")
            return

        await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
        logger.debug(f"Successfully preloaded beatmap {beatmap_id} for PP calculation")

    except Exception as e:
        # Preload failure should not affect normal gameplay
        logger.warning(f"Failed to preload beatmap {beatmap_id}: {e}")


LeaderboardScoreType = ScoreModel.generate_typeddict(tuple(DEFAULT_SCORE_INCLUDES)) | LegacyScoreResp


class BeatmapUserScore(BaseModel):
    """Response model for a user's score on a beatmap.

    Attributes:
        position: The user's position on the leaderboard.
        score: The score data.
    """

    position: int
    score: LeaderboardScoreType  # pyright: ignore[reportInvalidTypeForm]


class BeatmapScores(BaseModel):
    """Response model for beatmap leaderboard scores.

    Attributes:
        scores: List of scores on the leaderboard.
        user_score: The current user's score (if any).
        score_count: Total number of scores on the leaderboard.
    """

    scores: list[LeaderboardScoreType]  # pyright: ignore[reportInvalidTypeForm]
    user_score: BeatmapUserScore | None = None
    score_count: int = 0


@router.get(
    "/beatmaps/{beatmap_id}/scores",
    tags=["Scores"],
    responses={
        200: {
            "model": BeatmapScores,
            "description": (
                "Leaderboard and current user's score.\n\n"
                f"If `x-api-version >= 20220705`, returns `BeatmapScores[Score]`"
                f" (includes: {', '.join([f'`{inc}`' for inc in DEFAULT_SCORE_INCLUDES])}), "
                "otherwise returns `BeatmapScores[LegacyScoreResp]`."
            ),
        }
    },
    name="Get beatmap leaderboard",
    description="Get the leaderboard and current user's score for a specific beatmap under certain conditions.",
)
async def get_beatmap_scores(
    db: Database,
    api_version: APIVersion,
    beatmap_id: Annotated[int, Path(description="Beatmap ID")],
    mode: Annotated[GameMode, Query(description="Specified ruleset")],
    mods: Annotated[
        list[str],
        Query(default_factory=set, alias="mods[]", description="Filter by mods (optional, multiple values)"),
    ],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    legacy_only: Annotated[bool | None, Query(description="Whether to only query Stable scores")] = None,
    type: Annotated[
        LeaderboardType,
        Query(
            description=("Leaderboard type: GLOBAL / COUNTRY / FRIENDS / TEAM"),
        ),
    ] = LeaderboardType.GLOBAL,
    limit: Annotated[int, Query(ge=1, le=200, description="Number of results (1-200)")] = 50,
):
    """Get beatmap leaderboard scores.

    Args:
        db: Database session dependency.
        api_version: API version from request headers.
        beatmap_id: The beatmap ID.
        mode: The game mode.
        mods: Optional mod filter.
        current_user: The authenticated user.
        legacy_only: Whether to only query Stable scores.
        type: Leaderboard type filter.
        limit: Maximum number of results.

    Returns:
        dict: Leaderboard scores with user score and count.
    """
    all_scores, user_score, count = await get_leaderboard(
        db,
        beatmap_id,
        mode,
        type=type,
        user=current_user,
        limit=limit,
        mods=sorted(mods),
    )

    user_score_resp = await user_score.to_resp(db, api_version, includes=DEFAULT_SCORE_INCLUDES) if user_score else None
    return {
        "scores": [await score.to_resp(db, api_version, includes=DEFAULT_SCORE_INCLUDES) for score in all_scores],
        "user_score": (
            {
                "score": user_score_resp,
                "position": (
                    await get_score_position_by_id(
                        db,
                        user_score.beatmap_id,
                        user_score.id,
                        mode=user_score.gamemode,
                        user=user_score.user,
                    )
                    or 0
                ),
            }
            if user_score and user_score_resp
            else None
        ),
        "score_count": count,
    }


@router.get(
    "/beatmaps/{beatmap_id}/scores/users/{user_id}",
    tags=["Scores"],
    responses={
        200: {
            "model": BeatmapUserScore,
            "description": (
                "User's best score on the specified beatmap\n\n"
                "If `x-api-version >= 20220705`, returns `BeatmapUserScore[Score]`, "
                f" (includes: {', '.join([f'`{inc}`' for inc in DEFAULT_SCORE_INCLUDES])}), "
                "otherwise returns `BeatmapUserScore[LegacyScoreResp]`."
            ),
        }
    },
    name="Get user's best beatmap score",
    description="Get the best score for a specific user on a specific beatmap.",
)
async def get_user_beatmap_score(
    db: Database,
    api_version: APIVersion,
    beatmap_id: Annotated[int, Path(description="Beatmap ID")],
    user_id: Annotated[int, Path(description="User ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    legacy_only: Annotated[bool | None, Query(description="Whether to only query Stable scores")] = None,
    mode: Annotated[GameMode | None, Query(description="Specified ruleset (optional)")] = None,
    mods: Annotated[str | None, Query(description="Filter by mods (not implemented)")] = None,
):
    """Get user's best score on a beatmap.

    Args:
        db: Database session dependency.
        api_version: API version from request headers.
        beatmap_id: The beatmap ID.
        user_id: The user ID.
        current_user: The authenticated user.
        legacy_only: Whether to only query Stable scores.
        mode: Optional game mode filter.
        mods: Mod filter (not implemented).

    Returns:
        dict: User's best score with position.

    Raises:
        RequestError: If the score is not found.
    """
    user_score = (
        await db.exec(
            select(Score)
            .where(
                Score.gamemode == mode if mode is not None else True,
                Score.beatmap_id == beatmap_id,
                Score.user_id == user_id,
                col(Score.passed).is_(True),
            )
            .order_by(col(Score.total_score).desc())
            .limit(1)
        )
    ).first()

    if not user_score:
        raise RequestError(
            ErrorType.SCORE_NOT_FOUND,
            {"user_id": user_id, "beatmap_id": beatmap_id},
        )
    else:
        resp = await user_score.to_resp(db, api_version=api_version, includes=DEFAULT_SCORE_INCLUDES)
        return {
            "position": (
                await get_score_position_by_id(
                    db,
                    user_score.beatmap_id,
                    user_score.id,
                    mode=user_score.gamemode,
                    user=user_score.user,
                )
                or 0
            ),
            "score": resp,
        }


@router.get(
    "/beatmaps/{beatmap_id}/scores/users/{user_id}/all",
    tags=["Scores"],
    responses={
        200: api_doc(
            (
                "All user scores on beatmap\n\n"
                "If `x-api-version >= 20220705`, returns `Score` list, "
                "otherwise returns `LegacyScoreResp` list."
            ),
            list[ScoreModel] | list[LegacyScoreResp],
            DEFAULT_SCORE_INCLUDES,
        )
    },
    name="Get all user beatmap scores",
    description="Get all scores for a specific user on a specific beatmap.",
)
async def get_user_all_beatmap_scores(
    db: Database,
    api_version: APIVersion,
    beatmap_id: Annotated[int, Path(description="Beatmap ID")],
    user_id: Annotated[int, Path(description="User ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    legacy_only: Annotated[bool | None, Query(description="Whether to only query Stable scores")] = None,
    ruleset: Annotated[GameMode | None, Query(description="Specified ruleset (optional)")] = None,
):
    """Get all user scores on a beatmap.

    Args:
        db: Database session dependency.
        api_version: API version from request headers.
        beatmap_id: The beatmap ID.
        user_id: The user ID.
        current_user: The authenticated user.
        legacy_only: Whether to only query Stable scores.
        ruleset: Optional game mode filter.

    Returns:
        list: All user scores on the beatmap.
    """
    all_user_scores = (
        await db.exec(
            select(Score)
            .where(
                Score.gamemode == ruleset if ruleset is not None else True,
                Score.beatmap_id == beatmap_id,
                Score.user_id == user_id,
                col(Score.passed).is_(True),
                ~User.is_restricted_query(col(Score.user_id)),
            )
            .order_by(col(Score.total_score).desc())
        )
    ).all()

    return [await score.to_resp(db, api_version, includes=DEFAULT_SCORE_INCLUDES) for score in all_user_scores]


@router.post(
    "/beatmaps/{beatmap_id}/solo/scores",
    tags=["Gameplay"],
    response_model=ScoreTokenResp,
    name="Create solo score token",
    description="\nCreate a one-time score submission token for a specific beatmap.",
)
async def create_solo_score(
    background_task: BackgroundTasks,
    db: Database,
    fetcher: Fetcher,
    verification_service: ClientVerificationService,
    beatmap_id: Annotated[int, Path(description="Beatmap ID")],
    beatmap_hash: Annotated[str, Form(description="Beatmap file hash")],
    ruleset_id: Annotated[int, Form(..., description="Ruleset numeric ID (0-3)")],
    current_user: ClientUser,
    version_hash: Annotated[str, Form(description="Game version hash")] = "",
    ruleset_hash: Annotated[str, Form(description="Ruleset version hash")] = "",
):
    """Create a solo score submission token.

    Args:
        background_task: Background tasks handler.
        db: Database session dependency.
        fetcher: Fetcher service.
        verification_service: Client verification service.
        beatmap_id: The beatmap ID.
        beatmap_hash: The beatmap file hash.
        ruleset_id: The ruleset numeric ID (0-3).
        current_user: The authenticated client user.
        version_hash: Game version hash.
        ruleset_hash: Ruleset version hash.

    Returns:
        ScoreTokenResp: The created score token.

    Raises:
        RequestError: If validation fails.
    """
    # Get user ID immediately to avoid lazy loading issues
    user_id = current_user.id

    try:
        gamemode = GameMode.from_int(ruleset_id)
    except ValueError:
        raise RequestError(ErrorType.INVALID_RULESET_ID)

    if not (
        client_version := await verification_service.validate_client_version(
            version_hash,
        )
    ):
        logger.info(
            f"Client version check failed for user {current_user.id} on beatmap {beatmap_id} "
            f"(version hash: {version_hash})"
        )
        raise RequestError(ErrorType.INVALID_CLIENT_HASH)

    beatmap = await Beatmap.get_or_fetch(db, fetcher, md5=beatmap_hash)
    if not beatmap or beatmap.id != beatmap_id:
        raise RequestError(ErrorType.INVALID_OR_MISSING_BEATMAP_HASH)

    result = gamemode.check_ruleset_version(ruleset_hash)
    if not result:
        logger.info(
            f"Ruleset version check failed for user {current_user.id} on beatmap {beatmap_id} "
            f"(ruleset: {ruleset_id}, hash: {ruleset_hash})"
        )

        details = {"ruleset_id": ruleset_id, "ruleset_hash": ruleset_hash}

        # The result may have useful information in its own message
        if result.error_msg:
            details.update({"error": result.error_msg})

        raise RequestError(ErrorType.RULESET_VERSION_CHECK_FAILED, details)

    background_task.add_task(_preload_beatmap_for_pp_calculation, beatmap_id)
    async with db:
        score_token = ScoreToken(
            user_id=user_id,
            beatmap_id=beatmap_id,
            ruleset_id=GameMode.from_int(ruleset_id),
        )
        db.add(score_token)
        await db.commit()
        await db.refresh(score_token)
        logger.debug(
            "User {user_id} created solo score {score_token} for beatmap {beatmap_id} "
            "(mode: {mode}), using client {client_version}",
            user_id=user_id,
            score_token=score_token.id,
            beatmap_id=beatmap_id,
            mode=ruleset_id,
            client_version=str(client_version),
        )

        hub.emit(
            SoloScoreCreatedEvent(
                user_id=user_id,
                beatmap_id=beatmap_id,
                beatmap_hash=beatmap_hash,
                gamemode=GameMode.from_int(ruleset_id),
                score_token=score_token.id,
            )
        )

        return ScoreTokenResp.from_db(score_token)


@router.put(
    "/beatmaps/{beatmap_id}/solo/scores/{token}",
    tags=["Gameplay"],
    name="Submit solo score",
    description="\nSubmit a solo score using a token.",
    responses={200: api_doc("Solo score submission result.", ScoreModel)},
)
async def submit_solo_score(
    background_task: BackgroundTasks,
    db: Database,
    beatmap_id: Annotated[int, Path(description="Beatmap ID")],
    token: Annotated[int, Path(description="Score token ID")],
    info: Annotated[SoloScoreSubmissionInfo, Body(description="Score submission information")],
    current_user: ClientUser,
    redis: Redis,
    fetcher: Fetcher,
):
    """Submit a solo score.

    Args:
        background_task: Background tasks handler.
        db: Database session dependency.
        beatmap_id: The beatmap ID.
        token: The score token ID.
        info: Score submission information.
        current_user: The authenticated client user.
        redis: Redis connection.
        fetcher: Fetcher service.

    Returns:
        dict: The submitted score.
    """
    hub.emit(
        SoloScoreSubmittedEvent(
            submission_info=info,
            user_id=current_user.id,
        )
    )

    return await submit_score(background_task, info, token, current_user, db, redis, fetcher)


@router.post(
    "/rooms/{room_id}/playlist/{playlist_id}/scores",
    tags=["Gameplay"],
    response_model=ScoreTokenResp,
    name="Create room item score token",
    description="\nCreate a score submission token for a room playlist item.",
)
async def create_playlist_score(
    session: Database,
    background_task: BackgroundTasks,
    room_id: int,
    playlist_id: int,
    verification_service: ClientVerificationService,
    beatmap_id: Annotated[int, Form(description="Beatmap ID")],
    beatmap_hash: Annotated[str, Form(description="Beatmap file hash")],
    ruleset_id: Annotated[int, Form(..., description="Ruleset numeric ID (0-3)")],
    current_user: ClientUser,
    version_hash: Annotated[str, Form(description="Game version hash")] = "",
    ruleset_hash: Annotated[str, Form(description="Ruleset version hash")] = "",
):
    """Create a score token for a playlist item.

    Args:
        session: Database session dependency.
        background_task: Background tasks handler.
        room_id: The room ID.
        playlist_id: The playlist item ID.
        verification_service: Client verification service.
        beatmap_id: The beatmap ID.
        beatmap_hash: The beatmap file hash.
        ruleset_id: The ruleset numeric ID.
        current_user: The authenticated client user.
        version_hash: Game version hash.
        ruleset_hash: Ruleset version hash.

    Returns:
        ScoreTokenResp: The created score token.

    Raises:
        RequestError: If validation fails.
    """
    try:
        gamemode = GameMode.from_int(ruleset_id)
    except ValueError:
        raise RequestError(ErrorType.INVALID_RULESET_ID)

    if not (
        client_version := await verification_service.validate_client_version(
            version_hash,
        )
    ):
        logger.info(
            f"Client version check failed for user {current_user.id} on room {room_id}, playlist {playlist_id} "
            f"(version hash: {version_hash})"
        )
        raise RequestError(ErrorType.INVALID_CLIENT_HASH)

    result = gamemode.check_ruleset_version(ruleset_hash)
    if not result:
        logger.info(
            f"Ruleset version check failed for user {current_user.id} on room {room_id}, playlist {playlist_id},"
            f" (ruleset: {ruleset_id}, hash: {ruleset_hash})"
        )

        details = {"ruleset_id": ruleset_id, "ruleset_hash": ruleset_hash}

        # The result may have useful information in its own message
        if result.error_msg:
            details.update({"error": result.error_msg})

        raise RequestError(ErrorType.RULESET_VERSION_CHECK_FAILED, details)

    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    user_id = current_user.id

    room = await session.get(Room, room_id)
    if not room:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)
    db_room_time = room.ends_at.replace(tzinfo=UTC) if room.ends_at else None
    if db_room_time and db_room_time < utcnow().replace(tzinfo=UTC):
        raise RequestError(ErrorType.ROOM_HAS_ENDED)
    item = (await session.exec(select(Playlist).where(Playlist.id == playlist_id, Playlist.room_id == room_id))).first()
    if not item:
        raise RequestError(ErrorType.PLAYLIST_NOT_FOUND)

    # validate
    if not item.freestyle:
        if item.ruleset_id != ruleset_id:
            raise RequestError(ErrorType.RULESET_MISMATCH_PLAYLIST_ITEM)
        if item.beatmap_id != beatmap_id:
            raise RequestError(ErrorType.BEATMAP_ID_MISMATCH_PLAYLIST_ITEM)
    agg = await session.exec(
        select(ItemAttemptsCount).where(
            ItemAttemptsCount.room_id == room_id,
            ItemAttemptsCount.user_id == user_id,
        )
    )
    agg = agg.first()
    if agg and room.max_attempts and agg.attempts >= room.max_attempts:
        raise RequestError(ErrorType.MAX_ATTEMPTS_REACHED)
    if item.expired:
        raise RequestError(ErrorType.PLAYLIST_ITEM_EXPIRED)
    if item.played_at:
        raise RequestError(ErrorType.PLAYLIST_ITEM_ALREADY_PLAYED)
    # Mod validation should not be needed here
    background_task.add_task(_preload_beatmap_for_pp_calculation, beatmap_id)
    score_token = ScoreToken(
        user_id=user_id,
        beatmap_id=beatmap_id,
        ruleset_id=GameMode.from_int(ruleset_id),
        playlist_item_id=playlist_id,
        room_id=room_id,
    )
    session.add(score_token)
    await session.commit()
    await session.refresh(score_token)
    logger.debug(
        "User {user_id} created playlist score {score_token} for beatmap {beatmap_id} "
        "(mode: {mode}, room {room_id}, item {playlist_id}), using client {client_version}",
        user_id=user_id,
        score_token=score_token.id,
        beatmap_id=beatmap_id,
        mode=ruleset_id,
        room_id=room_id,
        playlist_id=playlist_id,
        client_version=str(client_version),
    )

    hub.emit(
        MultiplayerScoreCreatedEvent(
            user_id=user_id,
            beatmap_id=beatmap_id,
            beatmap_hash=beatmap_hash,
            gamemode=GameMode.from_int(ruleset_id),
            score_token=score_token.id,
            score_type=ScoreType.MULTIPLAYER,
            room_id=room_id,
            playlist_id=playlist_id,
        )
    )
    return ScoreTokenResp.from_db(score_token)


@router.put(
    "/rooms/{room_id}/playlist/{playlist_id}/scores/{token}",
    tags=["Gameplay"],
    name="Submit room item score",
    description="\nSubmit a score for a room playlist item.",
    responses={200: api_doc("Solo score submission result.", ScoreModel)},
)
async def submit_playlist_score(
    background_task: BackgroundTasks,
    session: Database,
    room_id: int,
    playlist_id: int,
    token: int,
    info: SoloScoreSubmissionInfo,
    current_user: ClientUser,
    redis: Redis,
    fetcher: Fetcher,
):
    """Submit a playlist score.

    Args:
        background_task: Background tasks handler.
        session: Database session dependency.
        room_id: The room ID.
        playlist_id: The playlist item ID.
        token: The score token ID.
        info: Score submission information.
        current_user: The authenticated client user.
        redis: Redis connection.
        fetcher: Fetcher service.

    Returns:
        dict: The submitted score.

    Raises:
        RequestError: If validation fails.
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    user_id = current_user.id

    hub.emit(
        MultiplayerScoreSubmittedEvent(
            submission_info=info,
            room_id=room_id,
            playlist_id=playlist_id,
            user_id=user_id,
        )
    )

    item = (await session.exec(select(Playlist).where(Playlist.id == playlist_id, Playlist.room_id == room_id))).first()
    if not item:
        raise RequestError(ErrorType.PLAYLIST_ITEM_NOT_FOUND)
    room = await session.get(Room, room_id)
    if not room:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)
    room_category = room.category
    score_resp = await submit_score(
        background_task,
        info,
        token,
        current_user,
        session,
        redis,
        fetcher,
    )
    await process_playlist_best_score(
        room_id,
        playlist_id,
        user_id,
        score_resp["id"],
        score_resp["total_score"],
        session,
        redis,
    )
    await session.commit()
    if room_category == RoomCategory.DAILY_CHALLENGE and score_resp["passed"]:
        await process_daily_challenge_score(session, user_id, room_id)
    await ItemAttemptsCount.get_or_create(room_id, user_id, session)
    await session.commit()
    return score_resp


class IndexedScoreResp(MultiplayerScores):
    """Response model for indexed multiplayer scores.

    Attributes:
        total: Total number of scores.
        user_score: The current user's score (if any).
    """

    total: int
    user_score: MultiplayScoreDict | None = None  # pyright: ignore[reportInvalidTypeForm]


@router.get(
    "/rooms/{room_id}/playlist/{playlist_id}/scores",
    # response_model=IndexedScoreResp,
    name="Get room item leaderboard",
    description="Get the leaderboard for a room playlist item.",
    tags=["Scores"],
    responses={
        200: {
            "description": (
                f"Room item leaderboard.\n\n"
                f"Includes: {', '.join([f'`{inc}`' for inc in Score.MULTIPLAYER_BASE_INCLUDES])}"
            ),
            "model": IndexedScoreResp,
        }
    },
)
async def index_playlist_scores(
    session: Database,
    room_id: int,
    playlist_id: int,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    limit: Annotated[int, Query(ge=1, le=50, description="Number of results (1-50)")] = 50,
    cursor: Annotated[
        int,
        Query(alias="cursor[total_score]", description="Pagination cursor (previous page lowest score)"),
    ] = 2000000,
):
    """Get playlist item leaderboard.

    Args:
        session: Database session dependency.
        room_id: The room ID.
        playlist_id: The playlist item ID.
        current_user: The authenticated user.
        limit: Maximum number of results.
        cursor: Pagination cursor.

    Returns:
        IndexedScoreResp: Leaderboard scores with pagination.

    Raises:
        RequestError: If the room is not found.
    """
    # Get user ID immediately to avoid lazy loading issues
    user_id = current_user.id

    room = await session.get(Room, room_id)
    if not room:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)

    limit = clamp(limit, 1, 50)

    scores = (
        await session.exec(
            select(PlaylistBestScore)
            .where(
                PlaylistBestScore.playlist_id == playlist_id,
                PlaylistBestScore.room_id == room_id,
                PlaylistBestScore.total_score < cursor,
                ~User.is_restricted_query(col(PlaylistBestScore.user_id)),
            )
            .order_by(col(PlaylistBestScore.total_score).desc())
            .limit(limit + 1)
        )
    ).all()
    has_more = len(scores) > limit
    if has_more:
        scores = scores[:-1]

    user_score = None
    score_resp = [await ScoreModel.transform(score.score, includes=Score.MULTIPLAYER_BASE_INCLUDES) for score in scores]
    for score in score_resp:
        if (room.category == RoomCategory.DAILY_CHALLENGE and score["user_id"] == user_id and score["passed"]) or score[
            "user_id"
        ] == user_id:
            user_score = score
            user_score["position"] = await get_position(room_id, playlist_id, score["id"], session)
            break

    resp = IndexedScoreResp(
        scores=score_resp,
        user_score=user_score,
        total=len(scores),
        params={
            "limit": limit,
        },
    )
    if has_more:
        resp.cursor = {
            "total_score": scores[-1].total_score,
        }
    return resp


@router.get(
    "/rooms/{room_id}/playlist/{playlist_id}/scores/{score_id}",
    name="Get room item score",
    description="Get details for a specific score in a room playlist item.",
    tags=["Scores"],
    responses={
        200: api_doc(
            "Room item score details.",
            ScoreModel,
            [*Score.MULTIPLAYER_BASE_INCLUDES, "position", "scores_around"],
        )
    },
)
async def show_playlist_score(
    session: Database,
    room_id: int,
    playlist_id: int,
    score_id: int,
    current_user: ClientUser,
    redis: Redis,
):
    """Get a specific playlist score.

    Args:
        session: Database session dependency.
        room_id: The room ID.
        playlist_id: The playlist item ID.
        score_id: The score ID.
        current_user: The authenticated client user.
        redis: Redis connection.

    Returns:
        dict: Score details with position and surrounding scores.

    Raises:
        RequestError: If room or score is not found.
    """
    room = await session.get(Room, room_id)
    if not room:
        raise RequestError(ErrorType.ROOM_NOT_FOUND)

    start_time = time.time()
    score_record = None
    is_playlist = room.category != RoomCategory.REALTIME
    completed = is_playlist
    while time.time() - start_time < READ_SCORE_TIMEOUT:
        if score_record is None:
            score_record = (
                await session.exec(
                    select(PlaylistBestScore).where(
                        PlaylistBestScore.score_id == score_id,
                        PlaylistBestScore.playlist_id == playlist_id,
                        PlaylistBestScore.room_id == room_id,
                        ~User.is_restricted_query(col(PlaylistBestScore.user_id)),
                    )
                )
            ).first()
        if completed_players := await redis.get(f"multiplayer:{room_id}:gameplay:players"):
            completed = completed_players == "0"
        if score_record and completed:
            break
    if not score_record:
        raise RequestError(ErrorType.SCORE_NOT_FOUND)
    includes = [
        *Score.MULTIPLAYER_BASE_INCLUDES,
        "position",
    ]
    if completed:
        includes.append("scores_around")
    resp = await ScoreModel.transform(
        score_record.score,
        includes=includes,
        playlist_id=playlist_id,
        room_id=room_id,
        is_playlist=is_playlist,
    )
    return resp


@router.get(
    "/rooms/{room_id}/playlist/{playlist_id}/scores/users/{user_id}",
    responses={
        200: api_doc(
            "Room item score details.",
            ScoreModel,
            [*Score.MULTIPLAYER_BASE_INCLUDES, "position", "scores_around"],
        )
    },
    name="Get user's room item score",
    description="Get a specific user's score in a room playlist item.",
    tags=["Scores"],
)
async def get_user_playlist_score(
    session: Database,
    room_id: int,
    playlist_id: int,
    user_id: int,
    current_user: ClientUser,
):
    """Get a user's playlist score.

    Args:
        session: Database session dependency.
        room_id: The room ID.
        playlist_id: The playlist item ID.
        user_id: The user ID.
        current_user: The authenticated client user.

    Returns:
        dict: Score details with position and surrounding scores.

    Raises:
        RequestError: If the score is not found.
    """
    score_record = None
    start_time = time.time()
    while time.time() - start_time < READ_SCORE_TIMEOUT:
        score_record = (
            await session.exec(
                select(PlaylistBestScore).where(
                    PlaylistBestScore.user_id == user_id,
                    PlaylistBestScore.playlist_id == playlist_id,
                    PlaylistBestScore.room_id == room_id,
                    ~User.is_restricted_query(col(PlaylistBestScore.user_id)),
                )
            )
        ).first()
        if score_record:
            break
    if not score_record:
        raise RequestError(ErrorType.SCORE_NOT_FOUND)

    resp = await ScoreModel.transform(
        score_record.score,
        includes=[
            *Score.MULTIPLAYER_BASE_INCLUDES,
            "position",
            "scores_around",
        ],
    )
    return resp


@router.put(
    "/score-pins/{score_id}",
    status_code=204,
    name="Pin score",
    description="\nPin a score to the user's profile (in order).",
    tags=["Scores"],
)
async def pin_score(
    db: Database,
    current_user: ClientUser,
    user_cache_service: UserCacheService,
    score_id: Annotated[int, Path(description="Score ID")],
):
    """Pin a score to the user's profile.

    Args:
        db: Database session dependency.
        current_user: The authenticated client user.
        user_cache_service: User cache service.
        score_id: The score ID to pin.

    Raises:
        RequestError: If the score is not found.
    """
    # Get user ID immediately to avoid lazy loading issues
    user_id = current_user.id

    score_record = (
        await db.exec(
            select(Score).where(
                Score.id == score_id,
                Score.user_id == user_id,
                col(Score.passed).is_(True),
            )
        )
    ).first()
    if not score_record:
        raise RequestError(ErrorType.SCORE_NOT_FOUND)

    if score_record.pinned_order > 0:
        return

    next_order = (
        (
            await db.exec(
                select(func.max(Score.pinned_order)).where(
                    Score.user_id == current_user.id,
                    Score.gamemode == score_record.gamemode,
                )
            )
        ).first()
        or 0
    ) + 1
    score_record.pinned_order = next_order
    await user_cache_service.invalidate_user_scores_cache(user_id, score_record.gamemode)
    await db.commit()


@router.delete(
    "/score-pins/{score_id}",
    status_code=204,
    name="Unpin score",
    description="\nUnpin a score from the user's profile.",
    tags=["Scores"],
)
async def unpin_score(
    db: Database,
    user_cache_service: UserCacheService,
    score_id: Annotated[int, Path(description="Score ID")],
    current_user: ClientUser,
):
    """Unpin a score from the user's profile.

    Args:
        db: Database session dependency.
        user_cache_service: User cache service.
        score_id: The score ID to unpin.
        current_user: The authenticated client user.

    Raises:
        RequestError: If the score is not found.
    """
    # Get user ID immediately to avoid lazy loading issues
    user_id = current_user.id

    score_record = (await db.exec(select(Score).where(Score.id == score_id, Score.user_id == user_id))).first()
    if not score_record:
        raise RequestError(ErrorType.SCORE_NOT_FOUND)

    if score_record.pinned_order == 0:
        return
    changed_score = (
        await db.exec(
            select(Score).where(
                Score.user_id == user_id,
                Score.pinned_order > score_record.pinned_order,
                Score.gamemode == score_record.gamemode,
            )
        )
    ).all()
    for s in changed_score:
        s.pinned_order -= 1
    score_record.pinned_order = 0
    await user_cache_service.invalidate_user_scores_cache(user_id, score_record.gamemode)
    await db.commit()


@router.post(
    "/score-pins/{score_id}/reorder",
    status_code=204,
    name="Reorder pinned score",
    description=(
        "\nReorder the display order of a pinned score. Provide only one of after_score_id or before_score_id."
    ),
    tags=["Scores"],
)
async def reorder_score_pin(
    db: Database,
    user_cache_service: UserCacheService,
    current_user: ClientUser,
    score_id: Annotated[int, Path(description="Score ID")],
    after_score_id: Annotated[int | None, Body(description="Place after this score")] = None,
    before_score_id: Annotated[int | None, Body(description="Place before this score")] = None,
):
    """Reorder a pinned score.

    Args:
        db: Database session dependency.
        user_cache_service: User cache service.
        current_user: The authenticated client user.
        score_id: The score ID to reorder.
        after_score_id: Place after this score ID.
        before_score_id: Place before this score ID.

    Raises:
        RequestError: If score not found, not pinned, or invalid parameters.
    """
    # Get user ID immediately to avoid lazy loading issues
    user_id = current_user.id

    score_record = (await db.exec(select(Score).where(Score.id == score_id, Score.user_id == user_id))).first()
    if not score_record:
        raise RequestError(ErrorType.SCORE_NOT_FOUND)

    if score_record.pinned_order == 0:
        raise RequestError(ErrorType.SCORE_NOT_PINNED)

    if (after_score_id is None) == (before_score_id is None):
        raise RequestError(
            ErrorType.INVALID_REQUEST,
            {"error": "Either after_score_id or before_score_id must be provided (but not both)"},
        )

    all_pinned_scores = (
        await db.exec(
            select(Score)
            .where(
                Score.user_id == current_user.id,
                Score.pinned_order > 0,
                Score.gamemode == score_record.gamemode,
            )
            .order_by(col(Score.pinned_order))
        )
    ).all()

    target_order = None
    reference_score_id = after_score_id or before_score_id

    reference_score = next((s for s in all_pinned_scores if s.id == reference_score_id), None)
    if not reference_score:
        detail = "After score not found" if after_score_id else "Before score not found"
        raise RequestError(ErrorType.SCORE_NOT_FOUND, {"error": detail})

    target_order = reference_score.pinned_order + 1 if after_score_id else reference_score.pinned_order

    current_order = score_record.pinned_order

    if current_order == target_order:
        return

    updates = []

    if current_order < target_order:
        for s in all_pinned_scores:
            if current_order < s.pinned_order <= target_order and s.id != score_id:
                updates.append((s.id, s.pinned_order - 1))
        if after_score_id:
            final_target = target_order - 1 if target_order > current_order else target_order
        else:
            final_target = target_order
    else:
        for s in all_pinned_scores:
            if target_order <= s.pinned_order < current_order and s.id != score_id:
                updates.append((s.id, s.pinned_order + 1))
        final_target = target_order

    for score_id, new_order in updates:
        await db.exec(select(Score).where(Score.id == score_id))
        score_to_update = (await db.exec(select(Score).where(Score.id == score_id))).first()
        if score_to_update:
            score_to_update.pinned_order = new_order

    score_record.pinned_order = final_target
    await user_cache_service.invalidate_user_scores_cache(user_id, score_record.gamemode)
    await db.commit()


@router.get(
    "/scores/{score_id}/download",
    name="Download score replay",
    description="Download the replay file for a specific score.",
    tags=["Scores"],
    dependencies=[Depends(RateLimiter(limiter=Limiter(Rate(10, Duration.MINUTE))))],
)
async def download_score_replay(
    score_id: int,
    db: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    storage_service: StorageService,
):
    """Download a score replay.

    Args:
        score_id: The score ID.
        db: Database session dependency.
        current_user: The authenticated user.
        storage_service: Storage service for file access.

    Returns:
        RedirectResponse: Redirect to the replay file URL.

    Raises:
        RequestError: If score or replay file is not found.
    """
    # Get user ID immediately to avoid lazy loading issues
    user_id = current_user.id

    score = (await db.exec(select(Score).where(Score.id == score_id))).first()
    if not score:
        raise RequestError(ErrorType.SCORE_NOT_FOUND)

    filepath = score.replay_filename

    if not await storage_service.is_exists(filepath):
        raise RequestError(ErrorType.REPLAY_FILE_NOT_FOUND)

    is_friend = (
        score.user_id == user_id
        or (
            await db.exec(
                select(exists()).where(
                    Relationship.user_id == user_id,
                    Relationship.target_id == score.user_id,
                    Relationship.type == RelationshipType.FOLLOW,
                )
            )
        ).first()
    )
    if not is_friend:
        replay_watched_count = (
            await db.exec(
                select(ReplayWatchedCount).where(
                    ReplayWatchedCount.user_id == score.user_id,
                    ReplayWatchedCount.year == date.today().year,
                    ReplayWatchedCount.month == date.today().month,
                )
            )
        ).first()
        if replay_watched_count is None:
            replay_watched_count = ReplayWatchedCount(
                user_id=score.user_id, year=date.today().year, month=date.today().month
            )
            db.add(replay_watched_count)
        replay_watched_count.count += 1
        await db.commit()

    hub.emit(
        ReplayDownloadedEvent(
            score_id=score_id,
            owner_user_id=score.user_id,
            downloader_user_id=user_id,
        )
    )
    return RedirectResponse(
        await storage_service.get_file_url(filepath), 301, headers={"Content-Type": "application/x-osu-replay"}
    )
