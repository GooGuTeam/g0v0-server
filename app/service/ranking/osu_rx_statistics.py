from __future__ import annotations

from app.config import settings
from app.const import BANCHOBOT_ID
from app.database.user.lazer_user import User
from app.database.user.statistics import UserStatistics
from app.dependencies.database import with_db
from app.models.score import GameMode

from sqlalchemy import exists
from sqlmodel import select


async def create_rx_statistics():
    async with with_db() as session:
        users = (await session.exec(select(User.id))).all()
        for i in users:
            if i == BANCHOBOT_ID:
                continue

            if settings.enable_rx:
                for mode in (
                    GameMode.OSURX,
                    GameMode.TAIKORX,
                    GameMode.FRUITSRX,
                ):
                    is_exist = (
                        await session.exec(
                            select(exists()).where(
                                UserStatistics.user_id == i,
                                UserStatistics.mode == mode,
                            )
                        )
                    ).first()
                    if not is_exist:
                        statistics_rx = UserStatistics(mode=mode, user_id=i)
                        session.add(statistics_rx)
            if settings.enable_ap:
                is_exist = (
                    await session.exec(
                        select(exists()).where(
                            UserStatistics.user_id == i,
                            UserStatistics.mode == GameMode.OSUAP,
                        )
                    )
                ).first()
                if not is_exist:
                    statistics_ap = UserStatistics(mode=GameMode.OSUAP, user_id=i)
                    session.add(statistics_ap)
        await session.commit()
