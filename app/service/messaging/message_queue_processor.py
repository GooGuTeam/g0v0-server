"""
消息队列处理服务
专门处理 Redis 消息队列的异步写入数据库
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json

from app.database.chat import ChatMessage, MessageType
from app.dependencies.database import get_redis_message, with_db
from app.log import logger


class MessageQueueProcessor:
    """消息队列处理器"""

    def __init__(self):
        self.redis_message = get_redis_message()
        self.executor = ThreadPoolExecutor(max_workers=2)
        self._processing = False
        self._queue_task = None

    async def _redis_exec(self, func, *args, **kwargs):
        """在线程池中执行 Redis 操作"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, lambda: func(*args, **kwargs))

    async def cache_message(self, channel_id: int, message_data: dict, temp_uuid: str):
        """将消息缓存到 Redis"""
        try:
            # 存储消息数据
            await self._redis_exec(self.redis_message.hset, f"msg:{temp_uuid}", mapping=message_data)
            await self._redis_exec(self.redis_message.expire, f"msg:{temp_uuid}", 3600)  # 1小时过期

            # 加入频道消息列表
            await self._redis_exec(self.redis_message.lpush, f"channel:{channel_id}:messages", temp_uuid)
            await self._redis_exec(self.redis_message.ltrim, f"channel:{channel_id}:messages", 0, 99)  # 保持最新100条
            await self._redis_exec(self.redis_message.expire, f"channel:{channel_id}:messages", 86400)  # 24小时过期

            # 加入异步处理队列
            await self._redis_exec(self.redis_message.lpush, "message_write_queue", temp_uuid)

            logger.info(f"Message cached to Redis: {temp_uuid}")
        except Exception as e:
            logger.error(f"Failed to cache message to Redis: {e}")

    async def get_cached_messages(self, channel_id: int, limit: int = 50, since: int = 0) -> list[dict]:
        """从 Redis 获取缓存的消息"""
        try:
            message_uuids = await self._redis_exec(
                self.redis_message.lrange,
                f"channel:{channel_id}:messages",
                0,
                limit - 1,
            )

            messages = []
            for temp_uuid in message_uuids:
                # 解码 UUID 如果它是字节类型
                if isinstance(temp_uuid, bytes):
                    temp_uuid = temp_uuid.decode("utf-8")

                raw_data = await self._redis_exec(self.redis_message.hgetall, f"msg:{temp_uuid}")
                if raw_data:
                    # 解码 Redis 返回的字节数据
                    message_data = {
                        k.decode("utf-8") if isinstance(k, bytes) else k: v.decode("utf-8")
                        if isinstance(v, bytes)
                        else v
                        for k, v in raw_data.items()
                    }

                    # 检查 since 条件
                    if since > 0 and message_data.get("message_id"):
                        if int(message_data["message_id"]) <= since:
                            continue
                    messages.append(message_data)

            return messages[::-1]  # 按时间顺序返回
        except Exception as e:
            logger.error(f"Failed to get cached messages: {e}")
            return []

    async def update_message_status(self, temp_uuid: str, status: str, message_id: int | None = None):
        """更新消息状态"""
        try:
            update_data = {"status": status}
            if message_id:
                update_data["message_id"] = str(message_id)
                update_data["db_timestamp"] = datetime.now().isoformat()

            await self._redis_exec(self.redis_message.hset, f"msg:{temp_uuid}", mapping=update_data)
        except Exception as e:
            logger.error(f"Failed to update message status: {e}")

    async def get_message_status(self, temp_uuid: str) -> dict | None:
        """获取消息状态"""
        try:
            raw_data = await self._redis_exec(self.redis_message.hgetall, f"msg:{temp_uuid}")
            if not raw_data:
                return None

            # 解码 Redis 返回的字节数据
            return {
                k.decode("utf-8") if isinstance(k, bytes) else k: v.decode("utf-8") if isinstance(v, bytes) else v
                for k, v in raw_data.items()
            }
        except Exception as e:
            logger.error(f"Failed to get message status: {e}")
            return None

    async def _process_message_queue(self):
        """处理消息队列，异步写入数据库"""
        logger.info("Message queue processing started")

        while self._processing:
            try:
                # 批量获取消息
                message_uuids = []
                for _ in range(20):  # 批量处理20条消息
                    result = await self._redis_exec(self.redis_message.brpop, ["message_write_queue"], timeout=1)
                    if result:
                        # result是 (queue_name, value) 的元组，需要解码
                        uuid_value = result[1]
                        if isinstance(uuid_value, bytes):
                            uuid_value = uuid_value.decode("utf-8")
                        message_uuids.append(uuid_value)
                    else:
                        break

                if not message_uuids:
                    await asyncio.sleep(0.5)
                    continue

                # 批量写入数据库
                await self._process_message_batch(message_uuids)

            except Exception as e:
                logger.error(f"Error in message queue processing: {e}")
                await asyncio.sleep(1)

        logger.info("Message queue processing stopped")

    async def _process_message_batch(self, message_uuids: list[str]):
        """批量处理消息写入数据库"""
        async with with_db() as session:
            for temp_uuid in message_uuids:
                try:
                    # 获取消息数据并解码
                    raw_data = await self._redis_exec(self.redis_message.hgetall, f"msg:{temp_uuid}")
                    if not raw_data:
                        continue

                    # 解码 Redis 返回的字节数据
                    message_data = {
                        k.decode("utf-8") if isinstance(k, bytes) else k: v.decode("utf-8")
                        if isinstance(v, bytes)
                        else v
                        for k, v in raw_data.items()
                    }

                    if message_data.get("status") != "pending":
                        continue

                    # 更新状态为处理中
                    await self.update_message_status(temp_uuid, "processing")

                    # 创建数据库消息
                    msg = ChatMessage(
                        channel_id=int(message_data["channel_id"]),
                        content=message_data["content"],
                        sender_id=int(message_data["sender_id"]),
                        type=MessageType(message_data["type"]),
                        uuid=message_data.get("user_uuid") or None,
                    )

                    session.add(msg)
                    await session.commit()
                    await session.refresh(msg)

                    # 更新成功状态，包含临时消息ID映射
                    await self.update_message_status(temp_uuid, "completed", msg.message_id)

                    # 如果有临时消息ID，存储映射关系并通知客户端更新
                    if message_data.get("temp_message_id"):
                        temp_msg_id = int(message_data["temp_message_id"])
                        await self._redis_exec(
                            self.redis_message.set,
                            f"temp_to_real:{temp_msg_id}",
                            str(msg.message_id),
                            ex=3600,  # 1小时过期
                        )

                        # 发送消息ID更新通知到频道
                        channel_id = int(message_data["channel_id"])
                        await self._notify_message_update(channel_id, temp_msg_id, msg.message_id, message_data)

                    logger.info(
                        f"Message {temp_uuid} persisted to DB with ID {msg.message_id}, "
                        f"temp_id: {message_data.get('temp_message_id')}"
                    )

                except Exception as e:
                    logger.error(f"Failed to process message {temp_uuid}: {e}")
                    await self.update_message_status(temp_uuid, "failed")

    async def _notify_message_update(
        self,
        channel_id: int,
        temp_message_id: int,
        real_message_id: int,
        message_data: dict,
    ):
        """通知客户端消息ID已更新"""
        try:
            # 这里我们需要通过 SignalR 发送消息更新通知
            # 但为了避免循环依赖，我们将通过 Redis 发布消息更新事件
            update_event = {
                "event": "chat.message.update",
                "data": {
                    "channel_id": channel_id,
                    "temp_message_id": temp_message_id,
                    "real_message_id": real_message_id,
                    "timestamp": message_data.get("timestamp"),
                },
            }

            # 发布到 Redis 频道，让 SignalR 服务处理
            await self._redis_exec(
                self.redis_message.publish,
                f"chat_updates:{channel_id}",
                json.dumps(update_event),
            )

            logger.info(f"Published message update: temp_id={temp_message_id}, real_id={real_message_id}")

        except Exception as e:
            logger.error(f"Failed to notify message update: {e}")

    def start_processing(self):
        """启动消息队列处理"""
        if not self._processing:
            self._processing = True
            self._queue_task = asyncio.create_task(self._process_message_queue())
            logger.info("Message queue processor started")

    def stop_processing(self):
        """停止消息队列处理"""
        if self._processing:
            self._processing = False
            if self._queue_task:
                self._queue_task.cancel()
                self._queue_task = None
            logger.info("Message queue processor stopped")

    def __del__(self):
        """清理资源"""
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=False)


# 全局消息队列处理器实例
message_queue_processor = MessageQueueProcessor()


def start_message_processing():
    """启动消息队列处理"""
    message_queue_processor.start_processing()


def stop_message_processing():
    """停止消息队列处理"""
    message_queue_processor.stop_processing()


async def cache_message_to_redis(channel_id: int, message_data: dict, temp_uuid: str):
    """将消息缓存到 Redis - 便捷接口"""
    await message_queue_processor.cache_message(channel_id, message_data, temp_uuid)


async def get_cached_messages(channel_id: int, limit: int = 50, since: int = 0) -> list[dict]:
    """从 Redis 获取缓存的消息 - 便捷接口"""
    return await message_queue_processor.get_cached_messages(channel_id, limit, since)


async def get_message_status(temp_uuid: str) -> dict | None:
    """获取消息状态 - 便捷接口"""
    return await message_queue_processor.get_message_status(temp_uuid)
