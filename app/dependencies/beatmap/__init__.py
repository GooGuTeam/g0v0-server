from __future__ import annotations

from app.service.assets.beatmap_download_service import download_service


def get_beatmap_download_service():
    """获取谱面下载服务实例"""
    return download_service
