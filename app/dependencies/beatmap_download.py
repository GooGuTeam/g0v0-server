from typing import Annotated

from app.service.beatmap_download_service import BeatmapDownloadService, download_service

from fast_depends import Depends as FastDepends
from fastapi import Depends

DownloadService = Annotated[
    BeatmapDownloadService, Depends(lambda: download_service), FastDepends(lambda: download_service)
]
