from __future__ import annotations

from app.const import BANCHOBOT_ID
from app.database.user.lazer_user import User
from app.database.user.statistics import UserStatistics
from app.dependencies.database import with_db
from app.models.score import GameMode

from sqlmodel import exists, select


async def create_banchobot():
    async with with_db() as session:
        is_exist = (await session.exec(select(exists()).where(User.id == BANCHOBOT_ID))).first()
        if not is_exist:
            banchobot = User(
                username="BanchoBot",
                email="banchobot@ppy.sh",
                is_bot=True,
                pw_bcrypt="0",
                id=BANCHOBOT_ID,
                avatar_url="https://a.ppy.sh/3",
                country_code="SH",
                website="https://twitter.com/banchoboat",
            )
            session.add(banchobot)
            statistics = UserStatistics(user_id=BANCHOBOT_ID, mode=GameMode.OSU)
            session.add(statistics)
            await session.commit()
