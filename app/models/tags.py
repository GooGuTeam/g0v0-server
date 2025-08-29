from __future__ import annotations

import json
import os

from app.log import logger
from app.path import STATIC_DIR

from pydantic import BaseModel


class BeatmapTags(BaseModel):
    id: int
    name: str = ""
    description: str = ""
    ruleset_id: int | None = None


ALL_TAGS: dict[int, BeatmapTags] = {}


def load_tags() -> None:
    if len(ALL_TAGS) > 0:
        return
    if not os.path.exists(STATIC_DIR / "beatmap_tags.json"):
        logger.warning("beatmap tags description file does not exist, using no tags")
        return
    tags_list = json.loads((STATIC_DIR / "beatmap_tags.json").read_text())
    for tag in tags_list:
        if tag["id"] in ALL_TAGS:
            logger.error("find duplicated beatmap tag id")
            logger.info(f"tag {ALL_TAGS[tag['id']].name} and tag {tag['name']} have the same tag id")
            raise ValueError("duplicated tag id found")
        ALL_TAGS[tag["id"]] = BeatmapTags.model_validate(tag)


def get_tag_by_id(id: int) -> BeatmapTags:
    load_tags()
    tag = ALL_TAGS.get(id)
    if tag is None:
        logger.error(f"tag id {id} not found")
        raise ValueError("tag id not found")
    return tag


def get_all_tags() -> list[BeatmapTags]:
    load_tags()
    return list(ALL_TAGS.values())
