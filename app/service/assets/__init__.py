"""Static asset and proxy related services."""

from __future__ import annotations

from .asset_proxy_helper import process_response_assets, should_process_asset_proxy
from .asset_proxy_service import (
    AssetProxyService,
    get_asset_proxy_service,
)
from .audio_proxy_service import AudioProxyService, get_audio_proxy_service
from .beatmap_download_service import BeatmapDownloadService, download_service

__all__ = [
    "AssetProxyService",
    "AudioProxyService",
    "BeatmapDownloadService",
    "download_service",
    "get_asset_proxy_service",
    "get_audio_proxy_service",
    "process_response_assets",
    "should_process_asset_proxy",
]
