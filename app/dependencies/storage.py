from typing import Annotated, cast

from app.config import (
    AWSS3StorageSettings,
    CloudflareR2Settings,
    LocalStorageSettings,
    StorageServiceType,
    settings,
)
from app.storage import StorageService as OriginStorageService
from app.storage.cloudflare_r2 import AWSS3StorageService, CloudflareR2StorageService
from app.storage.local import LocalStorageService

from fastapi import Depends

storage: OriginStorageService | None = None


def init_storage_service():
    global storage
    if settings.storage_service == StorageServiceType.LOCAL:
        storage_settings = cast(LocalStorageSettings, settings.storage_settings)
        storage = LocalStorageService(
            storage_path=storage_settings.local_storage_path,
        )
    elif settings.storage_service == StorageServiceType.CLOUDFLARE_R2:
        storage_settings = cast(CloudflareR2Settings, settings.storage_settings)
        storage = CloudflareR2StorageService(
            account_id=storage_settings.r2_account_id,
            access_key_id=storage_settings.r2_access_key_id,
            secret_access_key=storage_settings.r2_secret_access_key,
            bucket_name=storage_settings.r2_bucket_name,
            public_url_base=storage_settings.r2_public_url_base,
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
    else:
        raise ValueError(f"Unsupported storage service: {settings.storage_service}")
    return storage


def get_storage_service():
    if storage is None:
        return init_storage_service()
    return storage


StorageService = Annotated[OriginStorageService, Depends(get_storage_service)]
