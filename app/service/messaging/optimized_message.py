"""
优化的消息服务
结合 Redis 缓存和异步数据库写入实现实时消息传送
"""

from __future__ import annotations

from app.database.chat import (
    ChannelType,
    ChatMessageResp,
    MessageType,
)
from app.database.user.lazer_user import User
from app.log import logger
from app.service.messaging.message_queue import message_queue

from sqlalchemy.ext.asyncio import AsyncSession


class OptimizedMessageService:
    """优化的消息服务"""

    def __init__(self):
        self.message_queue = message_queue

    async def send_message_fast(
        self,
        channel_id: int,
        channel_type: ChannelType,
        channel_name: str,
        content: str,
        sender: User,
        is_action: bool = False,
        user_uuid: str | None = None,
        session: AsyncSession | None = None,
    ) -> ChatMessageResp:
        """
        快速发送消息（先缓存到 Redis，异步写入数据库）

        Args:
            channel_id: 频道 ID
            channel_type: 频道类型
            channel_name: 频道名称
            content: 消息内容
            sender: 发送者
            is_action: 是否为动作消息
            user_uuid: 用户提供的 UUID
            session: 数据库会话（可选，用于一些验证）

        Returns:
            消息响应对象
        """

        # 准备消息数据
        message_data = {
            "channel_id": str(channel_id),
            "content": content,
            "sender_id": str(sender.id),
            "type": MessageType.ACTION.value if is_action else MessageType.PLAIN.value,
            "user_uuid": user_uuid or "",
            "channel_type": channel_type.value,
            "channel_name": channel_name,
        }

        # 立即将消息加入 Redis 队列（实时响应）
        temp_uuid = await self.message_queue.enqueue_message(message_data)

        # 缓存到频道消息列表
        await self.message_queue.cache_channel_message(channel_id, temp_uuid)

        # 创建临时响应对象（简化版本，用于立即响应）
        from datetime import datetime

        from app.database.user.lazer_user import UserResp

        # 创建基本的用户响应对象
        user_resp = UserResp(
            id=sender.id,
            username=sender.username,
            country_code=getattr(sender, "country_code", "XX"),
            # 基本字段，其他复杂字段可以后续异步加载
        )

        temp_response = ChatMessageResp(
            message_id=0,  # 临时 ID，等数据库写入后会更新
            channel_id=channel_id,
            content=content,
            timestamp=datetime.now(),
            sender_id=sender.id,
            sender=user_resp,
            is_action=is_action,
            uuid=user_uuid,
        )
        temp_response.temp_uuid = temp_uuid  # 添加临时 UUID 用于后续更新

        logger.info(f"Message sent to channel {channel_id} with temp_uuid {temp_uuid}")
        return temp_response

    async def get_cached_messages(self, channel_id: int, limit: int = 50, since: int = 0) -> list[dict]:
        """
        获取缓存的消息

        Args:
            channel_id: 频道 ID
            limit: 限制数量
            since: 获取自此消息 ID 之后的消息

        Returns:
            消息列表
        """
        return await self.message_queue.get_cached_messages(channel_id, limit, since)

    async def get_message_status(self, temp_uuid: str) -> dict | None:
        """
        获取消息状态

        Args:
            temp_uuid: 临时消息 UUID

        Returns:
            消息状态信息
        """
        return await self.message_queue.get_message_status(temp_uuid)

    async def wait_for_message_persisted(self, temp_uuid: str, timeout: int = 30) -> dict | None:  # noqa: ASYNC109
        """
        等待消息持久化到数据库

        Args:
            temp_uuid: 临时消息 UUID
            timeout: 超时时间（秒）

        Returns:
            完成后的消息状态
        """
        import asyncio

        for _ in range(timeout * 10):  # 每100ms检查一次
            status = await self.get_message_status(temp_uuid)
            if status and status.get("status") in ["completed", "failed"]:
                return status
            await asyncio.sleep(0.1)

        return None


# 全局优化消息服务实例
optimized_message_service = OptimizedMessageService()
