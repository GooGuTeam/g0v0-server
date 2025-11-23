"""
基于 Redis 的实时消息系统
- 消息立即存储到 Redis 并实时返回
- 定时批量存储到数据库
- 支持消息状态同步和故障恢复
"""

import asyncio
from datetime import datetime
import json
from typing import Any

from app.database import ChatMessageDict
from app.database.chat import ChatMessage, ChatMessageModel, MessageType
from app.database.user import User, UserModel
from app.dependencies.database import get_redis_message, with_db
from app.log import logger
from app.utils import bg_tasks


class RedisMessageSystem:
    """Redis 消息系统"""

    def __init__(self):
        self.redis: Any = get_redis_message()
        self._batch_timer: asyncio.Task | None = None
        self._running = False
        self.batch_interval = 5.0  # 5秒批量存储一次
        self.max_batch_size = 100  # 每批最多处理100条消息

    async def send_message(
        self,
        channel_id: int,
        user: User,
        content: str,
        is_action: bool = False,
        user_uuid: str | None = None,
    ) -> "ChatMessageDict":
        """
        发送消息 - 立即存储到 Redis 并返回

        Args:
            channel_id: 频道ID
            user: 发送用户
            content: 消息内容
            is_action: 是否为动作消息
            user_uuid: 用户UUID

        Returns:
            ChatMessage: 消息响应对象
        """
        # 生成消息ID和时间戳
        message_id = await self._generate_message_id(channel_id)
        timestamp = datetime.now()

        # 确保用户ID存在
        if not user.id:
            raise ValueError("User ID is required")

        # 获取频道类型以判断是否需要存储到数据库
        async with with_db() as session:
            from app.database.chat import ChannelType, ChatChannel

            from sqlmodel import select

            channel_result = await session.exec(select(ChatChannel.type).where(ChatChannel.channel_id == channel_id))
            channel_type = channel_result.first()
            is_multiplayer = channel_type == ChannelType.MULTIPLAYER

        # 准备消息数据
        message_data: "ChatMessageDict" = {
            "message_id": message_id,
            "channel_id": channel_id,
            "sender_id": user.id,
            "content": content,
            "timestamp": timestamp,
            "type": MessageType.ACTION if is_action else MessageType.PLAIN,
            "uuid": user_uuid or "",
            "is_action": is_action,
        }

        # 立即存储到 Redis
        await self._store_to_redis(message_id, channel_id, message_data, is_multiplayer)

        # 创建响应对象
        async with with_db() as session:
            user_resp = await UserModel.transform(user, includes=User.LIST_INCLUDES)
            message_data["sender"] = user_resp

        if is_multiplayer:
            logger.info(
                f"Multiplayer message {message_id} sent to Redis cache for channel {channel_id},"
                " will not be persisted to database"
            )
        else:
            logger.info(f"Message {message_id} sent to Redis cache for channel {channel_id}")
        return message_data

    async def get_messages(self, channel_id: int, limit: int = 50, since: int = 0) -> list[ChatMessageDict]:
        """
        获取频道消息 - 优先从 Redis 获取最新消息

        Args:
            channel_id: 频道ID
            limit: 消息数量限制
            since: 起始消息ID

        Returns:
            List[ChatMessageDict]: 消息列表
        """
        messages: list["ChatMessageDict"] = []

        try:
            # 从 Redis 获取最新消息
            redis_messages = await self._get_from_redis(channel_id, limit, since)

            # 为每条消息构建响应对象
            async with with_db() as session:
                for msg_data in redis_messages:
                    # 获取发送者信息
                    sender = await session.get(User, msg_data["sender_id"])
                    if sender:
                        user_resp = await UserModel.transform(sender, includes=User.LIST_INCLUDES)

                        from app.database.chat import ChatMessageDict

                        message_resp: ChatMessageDict = {
                            "message_id": msg_data["message_id"],
                            "channel_id": msg_data["channel_id"],
                            "content": msg_data["content"],
                            "timestamp": datetime.fromisoformat(msg_data["timestamp"]),  # pyright: ignore[reportArgumentType]
                            "sender_id": msg_data["sender_id"],
                            "sender": user_resp,
                            "is_action": msg_data["type"] == MessageType.ACTION.value,
                            "uuid": msg_data.get("uuid") or None,
                            "type": MessageType(msg_data["type"]),
                        }
                        messages.append(message_resp)

            # 如果 Redis 消息不够，从数据库补充
            if len(messages) < limit and since == 0:
                await self._backfill_from_database(channel_id, messages, limit)

        except Exception as e:
            logger.error(f"Failed to get messages from Redis: {e}")
            # 回退到数据库查询
            messages = await self._get_from_database_only(channel_id, limit, since)

        return messages[:limit]

    async def _generate_message_id(self, channel_id: int) -> int:
        """生成唯一的消息ID - 确保全局唯一且严格递增"""
        # 使用全局计数器确保所有频道的消息ID都是严格递增的
        message_id = await self.redis.incr("global_message_id_counter")

        # 同时更新频道的最后消息ID，用于客户端状态同步
        await self.redis.set(f"channel:{channel_id}:last_msg_id", message_id)

        return message_id

    async def _store_to_redis(
        self, message_id: int, channel_id: int, message_data: ChatMessageDict, is_multiplayer: bool = False
    ):
        """存储消息到 Redis"""
        try:
            # 存储消息数据
            await self.redis.hset(
                f"msg:{channel_id}:{message_id}",
                mapping={k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in message_data.items()},
            )

            # 设置消息过期时间（7天）
            await self.redis.expire(f"msg:{channel_id}:{message_id}", 604800)

            # 清理可能存在的错误类型键，然后添加到频道消息列表（按时间排序）
            channel_messages_key = f"channel:{channel_id}:messages"

            # 更健壮的键类型检查和清理
            try:
                key_type = await self.redis.type(channel_messages_key)
                if key_type == "none":
                    # 键不存在，这是正常的
                    pass
                elif key_type != "zset":
                    # 键类型错误，需要清理
                    logger.warning(f"Deleting Redis key {channel_messages_key} with wrong type: {key_type}")
                    await self.redis.delete(channel_messages_key)

                    # 验证删除是否成功
                    verify_type = await self.redis.type(channel_messages_key)
                    if verify_type != "none":
                        logger.error(
                            f"Failed to delete problematic key {channel_messages_key}, type is still {verify_type}"
                        )
                        # 强制删除
                        await self.redis.unlink(channel_messages_key)

            except Exception as type_check_error:
                logger.warning(f"Failed to check key type for {channel_messages_key}: {type_check_error}")
                # 如果检查失败，尝试强制删除键以确保清理
                try:
                    await self.redis.delete(channel_messages_key)
                except Exception:
                    # 最后的努力：使用unlink
                    try:
                        await self.redis.unlink(channel_messages_key)
                    except Exception as final_error:
                        logger.error(f"Critical: Unable to clear problematic key {channel_messages_key}: {final_error}")

            # 添加到频道消息列表（sorted set）
            try:
                await self.redis.zadd(
                    channel_messages_key,
                    mapping={f"msg:{channel_id}:{message_id}": message_id},
                )
            except Exception as zadd_error:
                logger.error(f"Failed to add message to sorted set {channel_messages_key}: {zadd_error}")
                # 如果添加失败，再次尝试清理并重试
                await self.redis.delete(channel_messages_key)
                await self.redis.zadd(
                    channel_messages_key,
                    mapping={f"msg:{channel_id}:{message_id}": message_id},
                )

            # 保持频道消息列表大小（最多1000条）
            await self.redis.zremrangebyrank(channel_messages_key, 0, -1001)

            # 只有非多人房间消息才添加到待持久化队列
            if not is_multiplayer:
                await self.redis.lpush("pending_messages", f"{channel_id}:{message_id}")
                logger.debug(f"Message {message_id} added to persistence queue")
            else:
                logger.debug(f"Message {message_id} in multiplayer room, skipped persistence queue")

        except Exception as e:
            logger.error(f"Failed to store message to Redis: {e}")
            raise

    async def _get_from_redis(self, channel_id: int, limit: int = 50, since: int = 0) -> list[ChatMessageDict]:
        """从 Redis 获取消息"""
        try:
            # 获取消息键列表，按消息ID排序
            if since > 0:
                # 获取指定ID之后的消息（正序）
                message_keys = await self.redis.zrangebyscore(
                    f"channel:{channel_id}:messages",
                    since + 1,
                    "+inf",
                    start=0,
                    num=limit,
                )
            else:
                # 获取最新的消息（倒序获取，然后反转）
                message_keys = await self.redis.zrevrange(f"channel:{channel_id}:messages", 0, limit - 1)

            messages = []
            for key in message_keys:
                # 获取消息数据
                raw_data = await self.redis.hgetall(key)
                if raw_data:
                    # 解码数据
                    message_data: dict[str, Any] = {}
                    for k, v in raw_data.items():
                        # 尝试解析 JSON
                        try:
                            if k in ["grade_counts", "level"] or v.startswith(("{", "[")):
                                message_data[k] = json.loads(v)
                            elif k in ["message_id", "channel_id", "sender_id"]:
                                message_data[k] = int(v)
                            elif k == "is_multiplayer":
                                message_data[k] = v == "True"
                            elif k == "created_at":
                                message_data[k] = float(v)
                            else:
                                message_data[k] = v
                        except (json.JSONDecodeError, ValueError):
                            message_data[k] = v

                    messages.append(message_data)

            # 确保消息按ID正序排序（时间顺序）
            messages.sort(key=lambda x: x.get("message_id", 0))

            # 如果是获取最新消息（since=0），需要保持倒序（最新的在前面）
            if since == 0:
                messages.reverse()

            return messages

        except Exception as e:
            logger.error(f"Failed to get messages from Redis: {e}")
            return []

    async def _backfill_from_database(self, channel_id: int, existing_messages: list[ChatMessageDict], limit: int):
        """从数据库补充历史消息"""
        try:
            # 找到最小的消息ID
            min_id = float("inf")
            if existing_messages:
                for msg in existing_messages:
                    if msg["message_id"] is not None and msg["message_id"] < min_id:
                        min_id = msg["message_id"]

            needed = limit - len(existing_messages)

            if needed <= 0:
                return

            async with with_db() as session:
                from sqlmodel import col, select

                query = select(ChatMessage).where(ChatMessage.channel_id == channel_id)

                if min_id != float("inf"):
                    query = query.where(col(ChatMessage.message_id) < min_id)

                query = query.order_by(col(ChatMessage.message_id).desc()).limit(needed)

                db_messages = (await session.exec(query)).all()

                for msg in reversed(db_messages):  # 按时间正序插入
                    msg_resp = await ChatMessageModel.transform(msg, includes=["sender"])
                    existing_messages.insert(0, msg_resp)

        except Exception as e:
            logger.error(f"Failed to backfill from database: {e}")

    async def _get_from_database_only(self, channel_id: int, limit: int, since: int) -> list[ChatMessageDict]:
        """仅从数据库获取消息（回退方案）"""
        try:
            async with with_db() as session:
                from sqlmodel import col, select

                query = select(ChatMessage).where(ChatMessage.channel_id == channel_id)

                if since > 0:
                    # 获取指定ID之后的消息，按ID正序
                    query = query.where(col(ChatMessage.message_id) > since)
                    query = query.order_by(col(ChatMessage.message_id).asc()).limit(limit)
                else:
                    # 获取最新消息，按ID倒序（最新的在前面）
                    query = query.order_by(col(ChatMessage.message_id).desc()).limit(limit)

                messages = (await session.exec(query)).all()

                results = [await ChatMessageModel.transform(msg, includes=["sender"]) for msg in messages]

                # 如果是 since > 0，保持正序；否则反转为时间正序
                if since == 0:
                    results.reverse()

                return results

        except Exception as e:
            logger.error(f"Failed to get messages from database: {e}")
            return []

    async def _batch_persist_to_database(self):
        """批量持久化消息到数据库"""
        logger.info("Starting batch persistence to database")

        while self._running:
            try:
                # 获取待处理的消息
                message_keys = []
                for _ in range(self.max_batch_size):
                    key = await self.redis.brpop("pending_messages", timeout=1)
                    if key:
                        # key 是 (queue_name, value) 的元组
                        _, value = key
                        message_keys.append(value)
                    else:
                        break

                if message_keys:
                    await self._process_message_batch(message_keys)
                else:
                    await asyncio.sleep(self.batch_interval)

            except Exception as e:
                logger.error(f"Error in batch persistence: {e}")
                await asyncio.sleep(1)

        logger.info("Stopped batch persistence to database")

    async def _process_message_batch(self, message_keys: list[str]):
        """处理消息批次"""
        async with with_db() as session:
            for key in message_keys:
                try:
                    # 解析频道ID和消息ID
                    channel_id, message_id = map(int, key.split(":"))

                    # 从 Redis 获取消息数据
                    raw_data = await self.redis.hgetall(f"msg:{channel_id}:{message_id}")

                    if not raw_data:
                        continue

                    # 解码数据
                    message_data = {}
                    for k, v in raw_data.items():
                        message_data[k] = v

                    # 检查是否是多人房间消息，如果是则跳过数据库存储
                    is_multiplayer = message_data.get("is_multiplayer", "False") == "True"
                    if is_multiplayer:
                        # 多人房间消息不存储到数据库，直接标记为已跳过
                        await self.redis.hset(
                            f"msg:{channel_id}:{message_id}",
                            "status",
                            "skipped_multiplayer",
                        )
                        logger.debug(f"Message {message_id} in multiplayer room skipped from database storage")
                        continue

                    # 检查消息是否已存在于数据库
                    existing = await session.get(ChatMessage, int(message_id))
                    if existing:
                        continue

                    # 创建数据库消息 - 使用 Redis 生成的正数ID
                    db_message = ChatMessage(
                        message_id=int(message_id),  # 使用 Redis 系统生成的正数ID
                        channel_id=int(message_data["channel_id"]),
                        sender_id=int(message_data["sender_id"]),
                        content=message_data["content"],
                        timestamp=datetime.fromisoformat(message_data["timestamp"]),
                        type=MessageType(message_data["type"]),
                        uuid=message_data.get("uuid") or None,
                    )

                    session.add(db_message)

                    # 更新 Redis 中的状态
                    await self.redis.hset(
                        f"msg:{channel_id}:{message_id}",
                        "status",
                        "persisted",
                    )

                    logger.debug(f"Message {message_id} persisted to database")

                except Exception as e:
                    logger.error(f"Failed to process message {key}: {e}")

            # 提交批次
            try:
                await session.commit()
                logger.info(f"Batch of {len(message_keys)} messages committed to database")
            except Exception as e:
                logger.error(f"Failed to commit message batch: {e}")
                await session.rollback()

    def start(self):
        """启动系统"""
        if not self._running:
            self._running = True
            self._batch_timer = asyncio.create_task(self._batch_persist_to_database())
            # 启动时初始化消息ID计数器
            bg_tasks.add_task(self._initialize_message_counter)
            # 启动定期清理任务
            bg_tasks.add_task(self._periodic_cleanup)
            logger.info("Redis message system started")

    async def _initialize_message_counter(self):
        """初始化全局消息ID计数器，确保从数据库最大ID开始"""
        try:
            # 清理可能存在的问题键
            await self._cleanup_redis_keys()

            async with with_db() as session:
                from sqlmodel import func, select

                # 获取数据库中最大的消息ID
                result = await session.exec(select(func.max(ChatMessage.message_id)))
                max_id = result.one() or 0

                # 检查 Redis 中的计数器值
                current_counter = await self.redis.get("global_message_id_counter")
                current_counter = int(current_counter) if current_counter else 0

                # 设置计数器为两者中的最大值
                initial_counter = max(max_id, current_counter)
                await self.redis.set("global_message_id_counter", initial_counter)

                logger.info(f"Initialized global message ID counter to {initial_counter}")

        except Exception as e:
            logger.error(f"Failed to initialize message counter: {e}")
            # 如果初始化失败，设置一个安全的起始值
            await self.redis.setnx("global_message_id_counter", 1000000)

    async def _cleanup_redis_keys(self):
        """清理可能存在问题的 Redis 键"""
        try:
            # 扫描所有 channel:*:messages 键并检查类型
            keys_pattern = "channel:*:messages"
            keys = await self.redis.keys(keys_pattern)

            fixed_count = 0
            for key in keys:
                try:
                    key_type = await self.redis.type(key)
                    if key_type == "none":
                        # 键不存在，正常情况
                        continue
                    elif key_type != "zset":
                        logger.warning(f"Cleaning up Redis key {key} with wrong type: {key_type}")
                        await self.redis.delete(key)

                        # 验证删除是否成功
                        verify_type = await self.redis.type(key)
                        if verify_type != "none":
                            logger.error(f"Failed to delete problematic key {key}, trying unlink...")
                            await self.redis.unlink(key)

                        fixed_count += 1
                except Exception as cleanup_error:
                    logger.warning(f"Failed to cleanup key {key}: {cleanup_error}")
                    # 强制删除问题键
                    try:
                        await self.redis.delete(key)
                        fixed_count += 1
                    except Exception:
                        try:
                            await self.redis.unlink(key)
                            fixed_count += 1
                        except Exception as final_error:
                            logger.error(f"Critical: Unable to clear problematic key {key}: {final_error}")

            if fixed_count > 0:
                logger.info(f"Redis keys cleanup completed, fixed {fixed_count} keys")
            else:
                logger.debug("Redis keys cleanup completed, no issues found")

        except Exception as e:
            logger.error(f"Failed to cleanup Redis keys: {e}")

    async def _periodic_cleanup(self):
        """定期清理任务"""
        while self._running:
            try:
                # 每5分钟执行一次清理
                await asyncio.sleep(300)
                if not self._running:
                    break

                logger.debug("Running periodic Redis keys cleanup...")
                await self._cleanup_redis_keys()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic cleanup error: {e}")
                # 出错后等待1分钟再重试
                await asyncio.sleep(60)

    def stop(self):
        """停止系统"""
        if self._running:
            self._running = False
            if self._batch_timer:
                self._batch_timer.cancel()
                self._batch_timer = None
            logger.info("Redis message system stopped")


# 全局消息系统实例
redis_message_system = RedisMessageSystem()
