from __future__ import annotations

from datetime import UTC, timedelta
import json
from math import ceil

from app.const import BANCHOBOT_ID
from app.database import Room, Score
from app.database.beatmap.daily_challenge import DailyChallengeStats
from app.database.room.playlists import Playlist
from app.database.score.playlist_best_score import PlaylistBestScore
from app.database.user.lazer_user import User
from app.dependencies.database import get_redis, with_db
from app.dependencies.scheduler import get_scheduler
from app.log import logger
from app.models.beatmap.mods import APIMod, get_available_mods
from app.models.room import RoomCategory
from app.models.signalr.metadata_hub import DailyChallengeInfo
from app.utils import are_same_weeks, utcnow

from .room import create_playlist_room

from sqlmodel import col, select


async def create_daily_challenge_room(
    beatmap: int,
    ruleset_id: int,
    duration: int,
    required_mods: list[APIMod] = [],
    allowed_mods: list[APIMod] = [],
) -> Room:
    async with with_db() as session:
        today = utcnow().date()
        return await create_playlist_room(
            session=session,
            name=str(today),
            host_id=BANCHOBOT_ID,
            playlist=[
                Playlist(
                    id=0,
                    room_id=0,
                    owner_id=BANCHOBOT_ID,
                    ruleset_id=ruleset_id,
                    beatmap_id=beatmap,
                    required_mods=required_mods,
                    allowed_mods=allowed_mods,
                )
            ],
            category=RoomCategory.DAILY_CHALLENGE,
            duration=duration,
        )


@get_scheduler().scheduled_job("cron", hour=0, minute=0, second=0, id="daily_challenge")
async def daily_challenge_job():
    from app.signalr.hub import MetadataHubs

    now = utcnow()
    redis = get_redis()
    key = f"daily_challenge:{now.date()}"
    if not await redis.exists(key):
        return
    async with with_db() as session:
        room = (
            await session.exec(
                select(Room).where(
                    Room.category == RoomCategory.DAILY_CHALLENGE,
                    col(Room.ends_at) > utcnow(),
                )
            )
        ).first()
        if room:
            return

    try:
        beatmap = await redis.hget(key, "beatmap")  # pyright: ignore[reportGeneralTypeIssues]
        ruleset_id = await redis.hget(key, "ruleset_id")  # pyright: ignore[reportGeneralTypeIssues]
        required_mods = await redis.hget(key, "required_mods")  # pyright: ignore[reportGeneralTypeIssues]
        allowed_mods = await redis.hget(key, "allowed_mods")  # pyright: ignore[reportGeneralTypeIssues]

        if beatmap is None or ruleset_id is None:
            logger.warning(
                f"[DailyChallenge] Missing required data for daily challenge {now}. Will try again in 5 minutes."
            )
            get_scheduler().add_job(
                daily_challenge_job,
                "date",
                run_date=utcnow() + timedelta(minutes=5),
            )
            return

        beatmap_int = int(beatmap)
        ruleset_id_int = int(ruleset_id)

        required_mods_list = []
        allowed_mods_list = []
        if required_mods:
            required_mods_list = json.loads(required_mods)
        if allowed_mods:
            allowed_mods_list = json.loads(allowed_mods)
        else:
            allowed_mods_list = get_available_mods(ruleset_id_int, required_mods_list)

        next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        room = await create_daily_challenge_room(
            beatmap=beatmap_int,
            ruleset_id=ruleset_id_int,
            required_mods=required_mods_list,
            allowed_mods=allowed_mods_list,
            duration=int((next_day - now - timedelta(minutes=2)).total_seconds() / 60),
        )
        await MetadataHubs.broadcast_call("DailyChallengeUpdated", DailyChallengeInfo(room_id=room.id))
        logger.success(f"[DailyChallenge] Added today's daily challenge: {beatmap=}, {ruleset_id=}, {required_mods=}")
        return
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(f"[DailyChallenge] Error processing daily challenge data: {e} Will try again in 5 minutes.")
    except Exception as e:
        logger.exception(f"[DailyChallenge] Unexpected error in daily challenge job: {e} Will try again in 5 minutes.")
    get_scheduler().add_job(
        daily_challenge_job,
        "date",
        run_date=utcnow() + timedelta(minutes=5),
    )


@get_scheduler().scheduled_job("cron", hour=0, minute=1, second=0, id="daily_challenge_last_top")
async def process_daily_challenge_top():
    async with with_db() as session:
        now = utcnow()
        room = (
            await session.exec(
                select(Room).where(
                    Room.category == RoomCategory.DAILY_CHALLENGE,
                    col(Room.ends_at) > now - timedelta(days=1),
                    col(Room.ends_at) < now,
                )
            )
        ).first()
        participated_users = []
        if room is not None:
            scores = (
                await session.exec(
                    select(PlaylistBestScore)
                    .where(
                        PlaylistBestScore.room_id == room.id,
                        PlaylistBestScore.playlist_id == 0,
                        col(PlaylistBestScore.score).has(col(Score.passed).is_(True)),
                    )
                    .order_by(col(PlaylistBestScore.total_score).desc())
                )
            ).all()
            total_score_count = len(scores)
            s = []
            for i, score in enumerate(scores):
                stats = await session.get(DailyChallengeStats, score.user_id)
                if stats is None:  # not execute
                    continue
                if stats.last_update is None or stats.last_update.replace(tzinfo=UTC).date() != now.date():
                    if total_score_count < 10 or ceil(i + 1 / total_score_count) <= 0.1:
                        stats.top_10p_placements += 1
                    if total_score_count < 2 or ceil(i + 1 / total_score_count) <= 0.5:
                        stats.top_50p_placements += 1
                s.append(s)
                participated_users.append(score.user_id)
                stats.last_update = now
            await session.commit()
            del s

        user_ids = (await session.exec(select(User.id).where(col(User.id).not_in(participated_users)))).all()
        for id in user_ids:
            stats = await session.get(DailyChallengeStats, id)
            if stats is None:  # not execute
                continue
            stats.daily_streak_current = 0
            if stats.last_weekly_streak and not are_same_weeks(
                stats.last_weekly_streak.replace(tzinfo=UTC), now - timedelta(days=7)
            ):
                stats.weekly_streak_current = 0
            stats.last_update = now
        await session.commit()
