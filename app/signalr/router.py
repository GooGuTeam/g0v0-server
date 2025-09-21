from __future__ import annotations

import asyncio
import json
import time
from typing import Literal
import uuid

from app.database import User as DBUser
from app.dependencies import get_current_user
from app.dependencies.database import DBFactory, get_db_factory
from app.dependencies.user import get_current_user_and_token
from app.log import logger
from app.models.signalr import NegotiateResponse, Transport

from .hub import Hubs
from .packet import PROTOCOLS, SEP

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket
from fastapi.security import SecurityScopes

router = APIRouter(prefix="/signalr", include_in_schema=False)
logger.warning(
    "The Python version of SignalR server is deprecated. "
    "Maybe it will be removed or be fixed to continuously use in the future"
)


@router.post("/{hub}/negotiate", response_model=NegotiateResponse)
async def negotiate(
    hub: Literal["spectator", "multiplayer", "metadata"],
    negotiate_version: int = Query(1, alias="negotiateVersion"),
    user: DBUser = Depends(get_current_user),
):
    connectionId = str(user.id)
    connectionToken = f"{connectionId}:{uuid.uuid4()}"
    Hubs[hub].add_waited_client(
        connection_token=connectionToken,
        timestamp=int(time.time()),
    )
    return NegotiateResponse(
        connectionId=connectionId,
        connectionToken=connectionToken,
        negotiateVersion=negotiate_version,
        availableTransports=[Transport(transport="WebSockets")],
    )


@router.websocket("/{hub}")
async def connect(
    hub: Literal["spectator", "multiplayer", "metadata"],
    websocket: WebSocket,
    id: str,
    authorization: str = Header(...),
    factory: DBFactory = Depends(get_db_factory),
):
    token = authorization[7:]
    user_id = id.split(":")[0]
    hub_ = Hubs[hub]
    if id not in hub_:
        await websocket.close(code=1008)
        return
    try:
        async for session in factory():
            if (
                user_and_token := await get_current_user_and_token(
                    session, SecurityScopes(scopes=["*"]), token_pw=token
                )
            ) is None or str(user_and_token[0].id) != user_id:
                await websocket.close(code=1008)
                return
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()

    # handshake
    handshake = await websocket.receive()
    message = handshake.get("bytes") or handshake.get("text")
    if not message:
        await websocket.close(code=1008)
        return
    handshake_payload = json.loads(message[:-1])
    error = ""
    protocol = handshake_payload.get("protocol", "json")

    client = None
    try:
        client = await hub_.add_client(
            connection_id=user_id,
            connection_token=id,
            connection=websocket,
            protocol=PROTOCOLS[protocol],
        )
    except KeyError:
        error = f"Protocol '{protocol}' is not supported."
    except TimeoutError:
        error = f"Connection {id} has waited too long."
    except ValueError as e:
        error = str(e)
    payload = {"error": error} if error else {}
    # finish handshake
    await websocket.send_bytes(json.dumps(payload).encode() + SEP)
    if error or not client:
        await websocket.close(code=1008)
        return

    connected_clients = hub_.get_before_clients(user_id, id)
    for connected_client in connected_clients:
        await hub_.kick_client(connected_client)

    await hub_.clean_state(client, False)
    task = asyncio.create_task(hub_.on_connect(client))
    hub_.tasks.add(task)
    task.add_done_callback(hub_.tasks.discard)
    await hub_._listen_client(client)
    try:
        await websocket.close()
    except Exception:
        ...
