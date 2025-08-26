from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.database import PlaylistBestScore, Score
from app.database.playlist_best_score import get_position
from app.dependencies.database import with_db
from app.models.metadata_hub import MultiplayerRoomScoreSetEvent

from .base import RedisSubscriber

from sqlmodel import select

if TYPE_CHECKING:
    from app.signalr.hub import MetadataHub


CHANNEL = "osu-channel:score:processed"


class ScoreSubscriber(RedisSubscriber):
    def __init__(self):
        super().__init__()
        self.room_subscriber: dict[int, list[int]] = {}
        self.metadata_hub: "MetadataHub | None " = None
        self.subscribed = False
        self.handlers[CHANNEL] = [self._handler]

    async def subscribe_room_score(self, room_id: int, user_id: int):
        if room_id not in self.room_subscriber:
            await self.subscribe(CHANNEL)
            self.start()
        self.room_subscriber.setdefault(room_id, []).append(user_id)

    async def unsubscribe_room_score(self, room_id: int, user_id: int):
        if room_id in self.room_subscriber:
            try:
                self.room_subscriber[room_id].remove(user_id)
            except ValueError:
                pass
            if not self.room_subscriber[room_id]:
                del self.room_subscriber[room_id]

    async def _notify_room_score_processed(self, score_id: int):
        if not self.metadata_hub:
            return
        async with with_db() as session:
            score = await session.get(Score, score_id)
            if not score or not score.passed or score.room_id is None or score.playlist_item_id is None:
                return
            if not self.room_subscriber.get(score.room_id, []):
                return

            new_rank = None
            user_best = (
                await session.exec(
                    select(PlaylistBestScore).where(
                        PlaylistBestScore.user_id == score.user_id,
                        PlaylistBestScore.room_id == score.room_id,
                    )
                )
            ).first()
            if user_best and user_best.score_id == score_id:
                new_rank = await get_position(
                    user_best.room_id,
                    user_best.playlist_id,
                    user_best.score_id,
                    session,
                )

            event = MultiplayerRoomScoreSetEvent(
                room_id=score.room_id,
                playlist_item_id=score.playlist_item_id,
                score_id=score_id,
                user_id=score.user_id,
                total_score=score.total_score,
                new_rank=new_rank,
            )
            await self.metadata_hub.notify_room_score_processed(event)

    async def _handler(self, channel: str, data: str):
        score_id = json.loads(data)["ScoreId"]
        if self.metadata_hub:
            await self._notify_room_score_processed(score_id)
