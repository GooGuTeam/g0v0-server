from __future__ import annotations

from urllib.parse import urlparse

from app.storage.base import StorageService

import aioboto3
from botocore.exceptions import ClientError


class AWSS3StorageService(StorageService):
    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        region_name: str,
        public_url_base: str | None = None,
    ):
        self.bucket_name = bucket_name
        self.public_url_base = public_url_base
        self.session = aioboto3.Session()
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region_name = region_name

    @property
    def endpoint_url(self) -> str | None:
        return None

    def _get_client(self):
        return self.session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region_name,
        )

    async def write_file(
        self,
        file_path: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        cache_control: str = "public, max-age=31536000",
    ) -> None:
        async with self._get_client() as client:
            await client.put_object(
                Bucket=self.bucket_name,
                Key=file_path,
                Body=content,
                ContentType=content_type,
                CacheControl=cache_control,
            )

    async def read_file(self, file_path: str) -> bytes:
        async with self._get_client() as client:
            try:
                response = await client.get_object(
                    Bucket=self.bucket_name,
                    Key=file_path,
                )
                async with response["Body"] as stream:
                    return await stream.read()
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "404":
                    raise FileNotFoundError(f"File not found: {file_path}")
                raise RuntimeError(f"Failed to read file from R2: {e}")

    async def delete_file(self, file_path: str) -> None:
        async with self._get_client() as client:
            try:
                await client.delete_object(
                    Bucket=self.bucket_name,
                    Key=file_path,
                )
            except ClientError as e:
                raise RuntimeError(f"Failed to delete file from R2: {e}")

    async def is_exists(self, file_path: str) -> bool:
        async with self._get_client() as client:
            try:
                await client.head_object(
                    Bucket=self.bucket_name,
                    Key=file_path,
                )
                return True
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "404":
                    return False
                raise RuntimeError(f"Failed to check file existence in R2: {e}")

    async def get_file_url(self, file_path: str) -> str:
        if self.public_url_base:
            return f"{self.public_url_base.rstrip('/')}/{file_path.lstrip('/')}"

        async with self._get_client() as client:
            try:
                url = await client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket_name, "Key": file_path},
                )
                return url
            except ClientError as e:
                raise RuntimeError(f"Failed to generate file URL: {e}")

    def get_file_name_by_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        path = parsed.path.lstrip("/")

        # 1. 如果是 public_url_base 拼接出来的
        if self.public_url_base and url.startswith(self.public_url_base.rstrip("/")):
            return path

        # 2. 如果是 S3 path-style: s3.amazonaws.com/<bucket>/<key>
        if parsed.netloc == "s3.amazonaws.com":
            parts = path.split("/", 1)
            return parts[1] if len(parts) > 1 else None

        # 3. 如果是 virtual-hosted-style: <bucket>.s3.<region>.amazonaws.com/<key>
        if ".s3." in parsed.netloc or parsed.netloc.endswith(".s3.amazonaws.com"):
            return path

        # 4. 直接返回 path
        return path or None
