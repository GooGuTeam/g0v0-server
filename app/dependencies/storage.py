from typing import Annotated, cast

from app.config import (
    AWSS3StorageSettings,
    CloudflareR2Settings,
    LocalStorageSettings,
    StorageServiceType,
    settings,
)
from app.log import system_logger
from app.storage import StorageService as OriginStorageService
from app.storage.cloudflare_r2 import AWSS3StorageService, CloudflareR2StorageService
from app.storage.local import LocalStorageService

from fast_depends import Depends as FastDepends
from fastapi import Depends

storage: OriginStorageService | None = None
logger = system_logger("Storage")


def init_storage_service():
    global storage
    logger.info(f"Initializing storage service: {settings.storage_service}")
    if settings.storage_service == StorageServiceType.LOCAL:
        storage_settings = cast(LocalStorageSettings, settings.storage_settings)
        storage = LocalStorageService(
            storage_path=storage_settings.local_storage_path,
        )
        logger.info(f"Local storage initialized at {storage_settings.local_storage_path}")
    elif settings.storage_service == StorageServiceType.CLOUDFLARE_R2:
        storage_settings = cast(CloudflareR2Settings, settings.storage_settings)
        storage = CloudflareR2StorageService(
            account_id=storage_settings.r2_account_id,
            access_key_id=storage_settings.r2_access_key_id,
            secret_access_key=storage_settings.r2_secret_access_key,
            bucket_name=storage_settings.r2_bucket_name,
            public_url_base=storage_settings.r2_public_url_base,
        )
        logger.info(
            f"Cloudflare R2 storage initialized for bucket {storage_settings.r2_bucket_name}; "
            f"public_url_configured={storage_settings.r2_public_url_base is not None}"
        )
    elif settings.storage_service == StorageServiceType.AWS_S3:
        storage_settings = cast(AWSS3StorageSettings, settings.storage_settings)
        storage = AWSS3StorageService(
            access_key_id=storage_settings.s3_access_key_id,
            secret_access_key=storage_settings.s3_secret_access_key,
            bucket_name=storage_settings.s3_bucket_name,
            public_url_base=storage_settings.s3_public_url_base,
            region_name=storage_settings.s3_region_name,
        )
        logger.info(
            f"AWS S3 storage initialized for bucket {storage_settings.s3_bucket_name} "
            f"in region {storage_settings.s3_region_name}; "
            f"public_url_configured={storage_settings.s3_public_url_base is not None}"
        )
    else:
        logger.error(f"Unsupported storage service configured: {settings.storage_service}")
        raise ValueError(f"Unsupported storage service: {settings.storage_service}")
    return storage


def get_storage_service():
    if storage is None:
        return init_storage_service()
    return storage


StorageService = Annotated[OriginStorageService, Depends(get_storage_service), FastDepends(get_storage_service)]
