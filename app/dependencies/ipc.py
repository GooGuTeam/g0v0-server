"""Provides IPCClient dependency for FastAPI routes when v2 IPC is enabled.

This module imports the IPCClient class and get_ipc_client function from app.v2_ipc, and defines an IPCClient type that can be used as a dependency in FastAPI routes. The IPCClient type is an Annotated type that includes both the original IPCClient class and the necessary dependencies to create an instance of it.
"""  # noqa: E501

from typing import Annotated

from app.config import settings
from app.v2_ipc import (
    IPCClient as OriginalIPCClient,
    get_ipc_client,
)

from fast_depends import Depends as FastDepends
from fastapi import Depends


def _get_ipc_client() -> OriginalIPCClient:
    if not settings.enable_v2_ipc:
        raise RuntimeError("V2 IPC is not enabled in the configuration.")
    return get_ipc_client()


IPCClient = Annotated[OriginalIPCClient, Depends(_get_ipc_client), FastDepends(_get_ipc_client)]
