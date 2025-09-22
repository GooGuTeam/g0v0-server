from __future__ import annotations

import datetime
import json
import os
import re
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Beatmap
from app.dependencies.database import get_redis, with_db
from app.dependencies.fetcher import get_fetcher
from app.log import logger
from app.models.beatmap.mods import APIMod, get_available_mods
from app.models.score import GameMode

logger.remove()


def mod_inp(name: str, default: list[APIMod]) -> list[APIMod]:
    mods_inp = input(f"Enter {name} mods (JSON APIMod Array) >>> ")
    while True:
        if mods_inp.strip() == "":
            mods = default
            break
        try:
            mods = json.loads(mods_inp)
        except json.JSONDecodeError:
            mods_inp = input(f"Invalid input. Enter {name} mods (JSON APIMod) >>> ")
            continue
        if not isinstance(mods, list):
            mods_inp = input(f"Invalid input. Enter {name} mods (JSON APIMod) >>> ")
            continue
        break
    return mods


async def main():
    async with with_db() as session:
        redis = get_redis()
        fetcher = await get_fetcher()

        today = datetime.date.today()
        input_date = input(f"Enter a date ({today}) >>> ")
        while True:
            if not input_date:
                input_date = str(today)
            elif not re.match(r"^\d{4}-\d{2}-\d{2}$", input_date):
                input_date = input(f"Invalid date format. Enter a date ({today}) >>> ")
                continue
            break

        beatmap_inp = input("Enter a beatmap ID >>> ")
        while True:
            if beatmap_inp.isdigit():
                beatmap_id = int(beatmap_inp)
                break
            beatmap_inp = input("Invalid input. Enter a beatmap ID >>> ")
        beatmap = await Beatmap.get_or_fetch(session, fetcher, beatmap_id)
        ruleset_inp = input(f"Enter ruleset ID ({int(beatmap.mode)}) >>> ")
        while True:
            if not ruleset_inp:
                ruleset_inp = str(int(beatmap.mode))
            elif not ruleset_inp.isdigit():
                ruleset_inp = input(f"Invalid input. Enter ruleset ID ({int(beatmap.mode)}) >>> ")
                continue
            ruleset_id = int(ruleset_inp)
            if beatmap.mode != GameMode.OSU and ruleset_id != int(beatmap.mode):
                ruleset_inp = input(f"Invalid input. Enter ruleset ID ({int(beatmap.mode)}) >>> ")
                continue
            break

        required_mods = mod_inp("required", [])
        allowed_mods = mod_inp("allowed", get_available_mods(ruleset_id, required_mods))

        await redis.hset(  # pyright: ignore[reportGeneralTypeIssues]
            f"daily_challenge:{input_date}",
            mapping={
                "beatmap": beatmap.id,
                "ruleset_id": ruleset_id,
                "required_mods": json.dumps(required_mods),
                "allowed_mods": json.dumps(allowed_mods),
            },
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
