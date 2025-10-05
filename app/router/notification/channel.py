from typing import Annotated, Any, Literal, Self

from app.database.chat import (
    ChannelType,
    ChatChannel,
    ChatChannelResp,
    ChatMessage,
    SilenceUser,
    UserSilenceResp,
)
from app.database.user import User, UserResp
from app.dependencies.database import Database, Redis
from app.dependencies.param import BodyOrForm
from app.dependencies.user import get_current_user
from app.router.v2 import api_v2_router as router

from .server import server

from fastapi import Depends, HTTPException, Path, Query, Security
from pydantic import BaseModel, Field, model_validator
from sqlmodel import col, select


class UpdateResponse(BaseModel):
    presence: list[ChatChannelResp] = Field(default_factory=list)
    silences: list[Any] = Field(default_factory=list)


@router.get(
    "/chat/updates",
    response_model=UpdateResponse,
    name="获取更新",
    description="获取当前用户所在频道的最新的禁言情况。",
    tags=["聊天"],
)
async def get_update(
    session: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
    redis: Redis,
    history_since: Annotated[int | None, Query(description="获取自此禁言 ID 之后的禁言记录")] = None,
    since: Annotated[int | None, Query(description="获取自此消息 ID 之后的禁言记录")] = None,
    includes: Annotated[
        list[str],
        Query(alias="includes[]", description="要包含的更新类型"),
    ] = ["presence", "silences"],
):
    resp = UpdateResponse()
    if "presence" in includes:
        channel_ids = server.get_user_joined_channel(current_user.id)
        for channel_id in channel_ids:
            # 使用明确的查询避免延迟加载
            db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == channel_id))).first()
            if db_channel:
                # 提取必要的属性避免惰性加载
                channel_type = db_channel.type

                resp.presence.append(
                    await ChatChannelResp.from_db(
                        db_channel,
                        session,
                        current_user,
                        redis,
                        server.channels.get(channel_id, []) if channel_type != ChannelType.PUBLIC else None,
                    )
                )
    if "silences" in includes:
        if history_since:
            silences = (await session.exec(select(SilenceUser).where(col(SilenceUser.id) > history_since))).all()
            resp.silences.extend([UserSilenceResp.from_db(silence) for silence in silences])
        elif since:
            msg = await session.get(ChatMessage, since)
            if msg:
                silences = (
                    await session.exec(select(SilenceUser).where(col(SilenceUser.banned_at) > msg.timestamp))
                ).all()
                resp.silences.extend([UserSilenceResp.from_db(silence) for silence in silences])
    return resp


@router.put(
    "/chat/channels/{channel}/users/{user}",
    response_model=ChatChannelResp,
    name="加入频道",
    description="加入指定的公开/房间频道。",
    tags=["聊天"],
)
async def join_channel(
    session: Database,
    channel: Annotated[str, Path(..., description="频道 ID/名称")],
    user: Annotated[str, Path(..., description="用户 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.write_manage"])],
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="You are restricted from sending messages")

    # 使用明确的查询避免延迟加载
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.name == channel))).first()

    if db_channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return await server.join_channel(current_user, db_channel, session)


@router.delete(
    "/chat/channels/{channel}/users/{user}",
    status_code=204,
    name="离开频道",
    description="将用户移出指定的公开/房间频道。",
    tags=["聊天"],
)
async def leave_channel(
    session: Database,
    channel: Annotated[str, Path(..., description="频道 ID/名称")],
    user: Annotated[str, Path(..., description="用户 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.write_manage"])],
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="You are restricted from sending messages")

    # 使用明确的查询避免延迟加载
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.name == channel))).first()

    if db_channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    await server.leave_channel(current_user, db_channel, session)
    return


@router.get(
    "/chat/channels",
    response_model=list[ChatChannelResp],
    name="获取频道列表",
    description="获取所有公开频道。",
    tags=["聊天"],
)
async def get_channel_list(
    session: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
    redis: Redis,
):
    channels = (await session.exec(select(ChatChannel).where(ChatChannel.type == ChannelType.PUBLIC))).all()
    results = []
    for channel in channels:
        # 提取必要的属性避免惰性加载
        channel_id = channel.channel_id
        channel_type = channel.type

        results.append(
            await ChatChannelResp.from_db(
                channel,
                session,
                current_user,
                redis,
                server.channels.get(channel_id, []) if channel_type != ChannelType.PUBLIC else None,
            )
        )
    return results


class GetChannelResp(BaseModel):
    channel: ChatChannelResp
    users: list[UserResp] = Field(default_factory=list)


@router.get(
    "/chat/channels/{channel}",
    response_model=GetChannelResp,
    name="获取频道信息",
    description="获取指定频道的信息。",
    tags=["聊天"],
)
async def get_channel(
    session: Database,
    channel: Annotated[str, Path(..., description="频道 ID/名称")],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
    redis: Redis,
):
    # 使用明确的查询避免延迟加载
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.name == channel))).first()

    if db_channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    # 立即提取需要的属性
    channel_id = db_channel.channel_id
    channel_type = db_channel.type
    channel_name = db_channel.name

    users = []
    if channel_type == ChannelType.PM:
        user_ids = channel_name.split("_")[1:]
        if len(user_ids) != 2:
            raise HTTPException(status_code=404, detail="Target user not found")
        for id_ in user_ids:
            if int(id_) == current_user.id:
                continue
            target_user = await session.get(User, int(id_))
            if target_user is None or await target_user.is_restricted(session):
                raise HTTPException(status_code=404, detail="Target user not found")
            users.extend([target_user, current_user])
            break

    return GetChannelResp(
        channel=await ChatChannelResp.from_db(
            db_channel,
            session,
            current_user,
            redis,
            server.channels.get(channel_id, []) if channel_type != ChannelType.PUBLIC else None,
        )
    )


class CreateChannelReq(BaseModel):
    class AnnounceChannel(BaseModel):
        name: str
        description: str

    message: str | None = None
    type: Literal["ANNOUNCE", "PM"] = "PM"
    target_id: int | None = None
    target_ids: list[int] | None = None
    channel: AnnounceChannel | None = None

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.type == "PM":
            if self.target_id is None:
                raise ValueError("target_id must be set for PM channels")
        else:
            if self.target_ids is None or self.channel is None or self.message is None:
                raise ValueError("target_ids, channel, and message must be set for ANNOUNCE channels")
        return self


@router.post(
    "/chat/channels",
    response_model=ChatChannelResp,
    name="创建频道",
    description="创建一个新的私聊/通知频道。如果存在私聊频道则重新加入。",
    tags=["聊天"],
)
async def create_channel(
    session: Database,
    req: Annotated[CreateChannelReq, Depends(BodyOrForm(CreateChannelReq))],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.write_manage"])],
    redis: Redis,
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="You are restricted from sending messages")

    if req.type == "PM":
        target = await session.get(User, req.target_id)
        if not target or await target.is_restricted(session):
            raise HTTPException(status_code=404, detail="Target user not found")
        is_can_pm, block = await target.is_user_can_pm(current_user, session)
        if not is_can_pm:
            raise HTTPException(status_code=403, detail=block)

        channel = await ChatChannel.get_pm_channel(
            current_user.id,
            req.target_id,  # pyright: ignore[reportArgumentType]
            session,
        )
        channel_name = f"pm_{current_user.id}_{req.target_id}"
    else:
        channel_name = req.channel.name if req.channel else "Unnamed Channel"
        result = await session.exec(select(ChatChannel).where(ChatChannel.name == channel_name))
        channel = result.first()

    if channel is None:
        channel = ChatChannel(
            name=channel_name,
            description=req.channel.description if req.channel else "Private message channel",
            type=ChannelType.PM if req.type == "PM" else ChannelType.ANNOUNCE,
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        await session.refresh(current_user)
    if req.type == "PM":
        await session.refresh(target)  # pyright: ignore[reportPossiblyUnboundVariable]
        await server.batch_join_channel([target, current_user], channel, session)  # pyright: ignore[reportPossiblyUnboundVariable]
    else:
        target_users = await session.exec(select(User).where(col(User.id).in_(req.target_ids or [])))
        await server.batch_join_channel([*target_users, current_user], channel, session)

    await server.join_channel(current_user, channel, session)

    # 提取必要的属性避免惰性加载
    channel_id = channel.channel_id

    return await ChatChannelResp.from_db(
        channel,
        session,
        current_user,
        redis,
        server.channels.get(channel_id, []),
        include_recent_messages=True,
    )
