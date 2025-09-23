"""
Redis 消息队列服务
用于实现实时消息推送和异步数据库持久化
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime
import uuid

from app.database.chat import ChatMessage, MessageType
from app.dependencies.database import get_redis, with_db
from app.log import logger
from app.utils import bg_tasks


class MessageQueue:
    """Redis 消息队列服务"""

    def __init__(self):
        self.redis = get_redis()
        self._processing = False
        self._batch_size = 50  # 批量处理大小
        self._batch_timeout = 1.0  # 批量处理超时时间（秒）
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    async def _run_in_executor(self, func, *args):
        """在线程池中运行同步 Redis 操作"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    async def start_processing(self):
        """启动消息处理任务"""
        if not self._processing:
            self._processing = True
            bg_tasks.add_task(self._process_message_queue)
            logger.info("Message queue processing started")

    async def stop_processing(self):
        """停止消息处理"""
        self._processing = False
        logger.info("Message queue processing stopped")

    async def enqueue_message(self, message_data: dict) -> str:
        """
        将消息加入 Redis 队列（实时响应）

        Args:
            message_data: 消息数据字典，包含所有必要的字段

        Returns:
            消息的临时 UUID
        """
        # 生成临时 UUID
        temp_uuid = str(uuid.uuid4())
        message_data["temp_uuid"] = temp_uuid
        message_data["timestamp"] = datetime.now().isoformat()
        message_data["status"] = "pending"  # pending, processing, completed, failed

        # 将消息存储到 Redis
        await self._run_in_executor(lambda: self.redis.hset(f"msg:{temp_uuid}", mapping=message_data))
        await self._run_in_executor(self.redis.expire, f"msg:{temp_uuid}", 3600)  # 1小时过期

        # 加入处理队列
        await self._run_in_executor(self.redis.lpush, "message_queue", temp_uuid)

        logger.info(f"Message enqueued with temp_uuid: {temp_uuid}")
        return temp_uuid

    async def get_message_status(self, temp_uuid: str) -> dict | None:
        """获取消息状态"""
        message_data = await self._run_in_executor(self.redis.hgetall, f"msg:{temp_uuid}")
        if not message_data:
            return None

        return message_data

    async def get_cached_messages(self, channel_id: int, limit: int = 50, since: int = 0) -> list[dict]:
        """
        从 Redis 获取缓存的消息

        Args:
            channel_id: 频道 ID
            limit: 限制数量
            since: 获取自此消息 ID 之后的消息

        Returns:
            消息列表
        """
        # 从 Redis 获取频道最近的消息 UUID 列表
        message_uuids = await self._run_in_executor(self.redis.lrange, f"channel:{channel_id}:messages", 0, limit - 1)

        messages = []
        for uuid_str in message_uuids:
            message_data = await self._run_in_executor(self.redis.hgetall, f"msg:{uuid_str}")
            if message_data:
                # 检查是否满足 since 条件
                if since > 0 and "message_id" in message_data:
                    if int(message_data["message_id"]) <= since:
                        continue

                messages.append(message_data)

        return messages[::-1]  # 返回时间顺序

    async def cache_channel_message(self, channel_id: int, temp_uuid: str, max_cache: int = 100):
        """将消息 UUID 缓存到频道消息列表"""
        # 添加到频道消息列表开头
        await self._run_in_executor(self.redis.lpush, f"channel:{channel_id}:messages", temp_uuid)
        # 限制缓存大小
        await self._run_in_executor(self.redis.ltrim, f"channel:{channel_id}:messages", 0, max_cache - 1)
        # 设置过期时间（24小时）
        await self._run_in_executor(self.redis.expire, f"channel:{channel_id}:messages", 86400)

    async def _process_message_queue(self):
        """异步处理消息队列，批量写入数据库"""
        while self._processing:
            try:
                # 批量获取消息
                message_uuids = []
                for _ in range(self._batch_size):
                    result = await self._run_in_executor(lambda: self.redis.brpop(["message_queue"], timeout=1))
                    if result:
                        message_uuids.append(result[1])
                    else:
                        break

                if message_uuids:
                    await self._process_message_batch(message_uuids)
                else:
                    # 没有消息时短暂等待
                    await asyncio.sleep(0.1)

            except Exception as e:
                logger.error(f"Error processing message queue: {e}")
                await asyncio.sleep(1)  # 错误时等待1秒再重试

    async def _process_message_batch(self, message_uuids: list[str]):
        """批量处理消息写入数据库"""
        async with with_db() as session:
            messages_to_insert = []

            for temp_uuid in message_uuids:
                try:
                    # 获取消息数据
                    message_data = await self._run_in_executor(self.redis.hgetall, f"msg:{temp_uuid}")
                    if not message_data:
                        continue

                    # 更新状态为处理中
                    await self._run_in_executor(self.redis.hset, f"msg:{temp_uuid}", "status", "processing")

                    # 创建数据库消息对象
                    msg = ChatMessage(
                        channel_id=int(message_data["channel_id"]),
                        content=message_data["content"],
                        sender_id=int(message_data["sender_id"]),
                        type=MessageType(message_data["type"]),
                        uuid=message_data.get("user_uuid"),  # 用户提供的 UUID（如果有）
                    )

                    messages_to_insert.append((msg, temp_uuid))

                except Exception as e:
                    logger.error(f"Error preparing message {temp_uuid}: {e}")
                    await self._run_in_executor(self.redis.hset, f"msg:{temp_uuid}", "status", "failed")

            if messages_to_insert:
                try:
                    # 批量插入数据库
                    for msg, temp_uuid in messages_to_insert:
                        session.add(msg)

                    await session.commit()

                    # 更新所有消息状态和真实 ID
                    for msg, temp_uuid in messages_to_insert:
                        await session.refresh(msg)
                        await self._run_in_executor(
                            lambda: self.redis.hset(
                                f"msg:{temp_uuid}",
                                mapping={
                                    "status": "completed",
                                    "message_id": str(msg.message_id),
                                    "created_at": msg.timestamp.isoformat() if msg.timestamp else "",
                                },
                            )
                        )

                        logger.info(f"Message {temp_uuid} persisted to DB with ID {msg.message_id}")

                except Exception as e:
                    logger.error(f"Error inserting messages to database: {e}")
                    await session.rollback()

                    # 标记所有消息为失败
                    for _, temp_uuid in messages_to_insert:
                        await self._run_in_executor(self.redis.hset, f"msg:{temp_uuid}", "status", "failed")


# 全局消息队列实例
message_queue = MessageQueue()


async def start_message_queue():
    """启动消息队列处理"""
    await message_queue.start_processing()


async def stop_message_queue():
    """停止消息队列处理"""
    await message_queue.stop_processing()
