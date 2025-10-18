#!/usr/bin/env python3
"""
download_achievements.py

批量下载 osu! 客户端使用的成就图标（原始图标 + @2x 高清图标）。
"""

from __future__ import annotations

from pathlib import Path

import httpx


def download_achievement_images(achievements_path: Path) -> None:
    """Download all used achievement images (one by one, from osu!)."""
    achievements_path.mkdir(parents=True, exist_ok=True)
    images: list[str] = []

    for resolution in ("", "@2x"):
        for mode in ("osu", "taiko", "fruits", "mania"):
            # 仅 osu!std 有 9 & 10 星 pass/fc 成就
            limit = 10 if mode == "osu" else 8
            for star_rating in range(1, limit + 1):
                images.append(f"{mode}-skill-pass-{star_rating}{resolution}.png")
                images.append(f"{mode}-skill-fc-{star_rating}{resolution}.png")

        for combo in (500, 750, 1000, 2000):
            images.append(f"osu-combo-{combo}{resolution}.png")

        for mod in (
            "suddendeath",
            "hidden",
            "perfect",
            "hardrock",
            "doubletime",
            "flashlight",
            "easy",
            "nofail",
            "nightcore",
            "halftime",
            "spunout",
        ):
            images.append(f"all-intro-{mod}{resolution}.png")

    base_url = "https://assets.ppy.sh/medals/client/"

    for name in images:
        url = base_url + name
        resp = httpx.get(url)
        if resp.status_code != 200:
            print(f"❌ Failed to download {url}")
            continue
        (achievements_path / name).write_bytes(resp.content)
        print(f"✅ Saved {name}")


if __name__ == "__main__":
    target_dir = Path("achievement_images")
    download_achievement_images(target_dir)
    print(f"All done! Images saved under {target_dir.resolve()}")
