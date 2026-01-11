from typing import NamedTuple, TypedDict


class VersionInfo(TypedDict):
    version: str
    release_date: str
    hashes: dict[str, str]


class VersionList(TypedDict):
    name: str
    versions: list[VersionInfo]


class VersionCheckResult(NamedTuple):
    is_valid: bool
    client_name: str = ""
    version: str = ""
    os: str = ""

    def __bool__(self) -> bool:
        return self.is_valid

    def __str__(self) -> str:
        if self.is_valid:
            return f"{self.client_name} {self.version} ({self.os})"
        return "Invalid Client Version"
