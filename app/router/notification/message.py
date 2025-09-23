from __future__ import annotations

from app.database import ChatMessageResp
from app.database.chat import (
    ChannelType,
    ChatChannel,
    ChatChannelResp,
    ChatMessage,
    MessageType,
    SilenceUser,
    UserSilenceResp,
)
from app.database.user.lazer_user import User
from app.dependencies.database import Database, get_redis
from app.dependencies.param import BodyOrForm
from app.dependencies.user import get_current_user
from app.log import logger
from app.models.notification import ChannelMessage, ChannelMessageTeam
from app.router.v2 import api_v2_router as router
from app.service.messaging.redis_message_system import redis_message_system

from .banchobot import bot
from .server import server

from fastapi import Depends, HTTPException, Path, Query, Security
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlmodel import col, select


class KeepAliveResp(BaseModel):
    silences: list[UserSilenceResp] = Field(default_factory=list)


@router.post(
    "/chat/ack",
    name="保持连接",
    response_model=KeepAliveResp,
    description="保持公共频道的连接。同时返回最近的禁言列表。",
    tags=["聊天"],
)
async def keep_alive(
    session: Database,
    history_since: int | None = Query(None, description="获取自此禁言 ID 之后的禁言记录"),
    since: int | None = Query(None, description="获取自此消息 ID 之后的禁言记录"),
    current_user: User = Security(get_current_user, scopes=["chat.read"]),
):
    resp = KeepAliveResp()
    if history_since:
        silences = (await session.exec(select(SilenceUser).where(col(SilenceUser.id) > history_since))).all()
        resp.silences.extend([UserSilenceResp.from_db(silence) for silence in silences])
    elif since:
        msg = await session.get(ChatMessage, since)
        if msg:
            silences = (await session.exec(select(SilenceUser).where(col(SilenceUser.banned_at) > msg.timestamp))).all()
            resp.silences.extend([UserSilenceResp.from_db(silence) for silence in silences])

    return resp


class MessageReq(BaseModel):
    message: str
    is_action: bool = False
    uuid: str | None = None


@router.post(
    "/chat/channels/{channel}/messages",
    response_model=ChatMessageResp,
    name="发送消息",
    description="发送消息到指定频道。",
    tags=["聊天"],
)
async def send_message(
    session: Database,
    channel: str = Path(..., description="频道 ID/名称"),
    req: MessageReq = Depends(BodyOrForm(MessageReq)),
    current_user: User = Security(get_current_user, scopes=["chat.write"]),
):
    # 使用明确的查询来获取 channel，避免延迟加载
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.name == channel))).first()

    if db_channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    # 立即提取所有需要的属性，避免后续延迟加载
    channel_id = db_channel.channel_id
    channel_type = db_channel.type
    channel_name = db_channel.name
    user_id = current_user.id

    # 对于多人游戏房间，在发送消息前进行Redis键检查
    if channel_type == ChannelType.MULTIPLAYER:
        try:
            from app.dependencies.database import get_redis

            redis = get_redis()
            key = f"channel:{channel_id}:messages"
            key_type = await redis.type(key)
            if key_type not in ["none", "zset"]:
                logger.warning(f"Fixing Redis key {key} with wrong type: {key_type}")
                await redis.delete(key)
        except Exception as e:
            logger.warning(f"Failed to check/fix Redis key for channel {channel_id}: {e}")

    # 使用 Redis 消息系统发送消息 - 立即返回
    resp = await redis_message_system.send_message(
        channel_id=channel_id,
        user=current_user,
        content=req.message,
        is_action=req.is_action,
        user_uuid=req.uuid,
    )

    # 立即广播消息给所有客户端
    is_bot_command = req.message.startswith("!")
    await server.send_message_to_channel(resp, is_bot_command and channel_type == ChannelType.PUBLIC)

    # 处理机器人命令
    if is_bot_command:
        await bot.try_handle(current_user, db_channel, req.message, session)

    await session.refresh(current_user)
    # 为通知系统创建临时 ChatMessage 对象（仅适用于私聊和团队频道）
    if channel_type in [ChannelType.PM, ChannelType.TEAM]:
        temp_msg = ChatMessage(
            message_id=resp.message_id,  # 使用 Redis 系统生成的ID
            channel_id=channel_id,
            content=req.message,
            sender_id=user_id,
            type=MessageType.ACTION if req.is_action else MessageType.PLAIN,
            uuid=req.uuid,
        )

        if channel_type == ChannelType.PM:
            user_ids = channel_name.split("_")[1:]
            await server.new_private_notification(
                ChannelMessage.init(temp_msg, current_user, [int(u) for u in user_ids], channel_type)
            )
        elif channel_type == ChannelType.TEAM:
            await server.new_private_notification(ChannelMessageTeam.init(temp_msg, current_user))

    return resp


@router.get(
    "/chat/channels/{channel}/messages",
    response_model=list[ChatMessageResp],
    name="获取消息",
    description="获取指定频道的消息列表（统一按时间正序返回）。",
    tags=["聊天"],
)
async def get_message(
    session: Database,
    channel: str,
    limit: int = Query(50, ge=1, le=50, description="获取消息的数量"),
    since: int = Query(0, ge=0, description="获取自此消息 ID 之后的消息（向前加载新消息）"),
    until: int | None = Query(None, description="获取自此消息 ID 之前的消息（向后翻历史）"),
    current_user: User = Security(get_current_user, scopes=["chat.read"]),
):
    # 1) 查频道
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.name == channel))).first()

    if db_channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel_id = db_channel.channel_id

    try:
        messages = await redis_message_system.get_messages(channel_id, limit, since)
        if len(messages) >= 2 and messages[0].message_id > messages[-1].message_id:
            messages.reverse()
        return messages
    except Exception as e:
        logger.warning(f"Failed to get messages from Redis system: {e}")

    base = select(ChatMessage).where(ChatMessage.channel_id == channel_id)

    if since > 0 and until is None:
        # 向前加载新消息 → 直接 ASC
        query = base.where(col(ChatMessage.message_id) > since).order_by(col(ChatMessage.message_id).asc()).limit(limit)
        rows = (await session.exec(query)).all()
        resp = [await ChatMessageResp.from_db(m, session) for m in rows]
        # 已经 ASC，无需反转
        return resp

    # until 分支（向后翻历史）
    if until is not None:
        # 用 DESC 取最近的更早消息，再反转为 ASC
        query = (
            base.where(col(ChatMessage.message_id) < until).order_by(col(ChatMessage.message_id).desc()).limit(limit)
        )
        rows = (await session.exec(query)).all()
        rows = list(rows)
        rows.reverse()  # 反转为 ASC
        resp = [await ChatMessageResp.from_db(m, session) for m in rows]
        return resp

    query = base.order_by(col(ChatMessage.message_id).desc()).limit(limit)
    rows = (await session.exec(query)).all()
    rows = list(rows)
    rows.reverse()  # 反转为 ASC
    resp = [await ChatMessageResp.from_db(m, session) for m in rows]
    return resp
    return resp


@router.put(
    "/chat/channels/{channel}/mark-as-read/{message}",
    status_code=204,
    name="标记消息为已读",
    description="标记指定消息为已读。",
    tags=["聊天"],
)
async def mark_as_read(
    session: Database,
    channel: str = Path(..., description="频道 ID/名称"),
    message: int = Path(..., description="消息 ID"),
    current_user: User = Security(get_current_user, scopes=["chat.read"]),
):
    # 使用明确的查询获取 channel，避免延迟加载
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.name == channel))).first()

    if db_channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    # 立即提取需要的属性
    channel_id = db_channel.channel_id
    await server.mark_as_read(channel_id, current_user.id, message)


class PMReq(BaseModel):
    target_id: int
    message: str
    is_action: bool = False
    uuid: str | None = None


class NewPMResp(BaseModel):
    channel: ChatChannelResp
    message: ChatMessageResp
    new_channel_id: int


@router.post(
    "/chat/new",
    name="创建私聊频道",
    description="创建一个新的私聊频道。",
    tags=["聊天"],
)
async def create_new_pm(
    session: Database,
    req: PMReq = Depends(BodyOrForm(PMReq)),
    current_user: User = Security(get_current_user, scopes=["chat.write"]),
    redis: Redis = Depends(get_redis),
):
    user_id = current_user.id
    target = await session.get(User, req.target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target user not found")
    is_can_pm, block = await target.is_user_can_pm(current_user, session)
    if not is_can_pm:
        raise HTTPException(status_code=403, detail=block)

    channel = await ChatChannel.get_pm_channel(user_id, req.target_id, session)
    if channel is None:
        channel = ChatChannel(
            name=f"pm_{user_id}_{req.target_id}",
            description="Private message channel",
            type=ChannelType.PM,
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        await session.refresh(target)
        await session.refresh(current_user)

    await server.batch_join_channel([target, current_user], channel, session)
    channel_resp = await ChatChannelResp.from_db(
        channel, session, current_user, redis, server.channels[channel.channel_id]
    )
    msg = ChatMessage(
        channel_id=channel.channel_id,
        content=req.message,
        sender_id=user_id,
        type=MessageType.ACTION if req.is_action else MessageType.PLAIN,
        uuid=req.uuid,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    await session.refresh(current_user)
    await session.refresh(channel)
    message_resp = await ChatMessageResp.from_db(msg, session, current_user)
    await server.send_message_to_channel(message_resp)
    return NewPMResp(
        channel=channel_resp,
        message=message_resp,
        new_channel_id=channel_resp.channel_id,
    )
