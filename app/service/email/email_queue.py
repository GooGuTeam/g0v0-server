"""
邮件队列服务
用于异步发送邮件
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import smtplib
from typing import Any
import uuid

from app.config import settings
from app.log import logger
from app.utils import bg_tasks  # 添加同步Redis导入

import redis as sync_redis


class EmailQueue:
    """Redis 邮件队列服务"""

    def __init__(self):
        # 创建专门用于邮件队列的同步Redis客户端 (db=0)
        self.redis = sync_redis.from_url(settings.redis_url, decode_responses=True, db=0)
        self._processing = False
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._retry_limit = 3  # 重试次数限制

        # 邮件配置
        self.smtp_server = getattr(settings, "smtp_server", "localhost")
        self.smtp_port = getattr(settings, "smtp_port", 587)
        self.smtp_username = getattr(settings, "smtp_username", "")
        self.smtp_password = getattr(settings, "smtp_password", "")
        self.from_email = getattr(settings, "from_email", "noreply@example.com")
        self.from_name = getattr(settings, "from_name", "osu! server")

    async def _run_in_executor(self, func, *args):
        """在线程池中运行同步操作"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    async def start_processing(self):
        """启动邮件处理任务"""
        if not self._processing:
            self._processing = True
            bg_tasks.add_task(self._process_email_queue)
            logger.info("Email queue processing started")

    async def stop_processing(self):
        """停止邮件处理"""
        self._processing = False
        logger.info("Email queue processing stopped")

    async def enqueue_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        html_content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        将邮件加入队列等待发送

        Args:
            to_email: 收件人邮箱地址
            subject: 邮件主题
            content: 邮件纯文本内容
            html_content: 邮件HTML内容（如果有）
            metadata: 额外元数据（如密码重置ID等）

        Returns:
            邮件任务ID
        """
        email_id = str(uuid.uuid4())

        email_data = {
            "id": email_id,
            "to_email": to_email,
            "subject": subject,
            "content": content,
            "html_content": html_content if html_content else "",
            "metadata": json.dumps(metadata) if metadata else "{}",
            "created_at": datetime.now().isoformat(),
            "status": "pending",  # pending, sending, sent, failed
            "retry_count": "0",
        }

        # 将邮件数据存入Redis
        await self._run_in_executor(lambda: self.redis.hset(f"email:{email_id}", mapping=email_data))

        # 设置24小时过期（防止数据堆积）
        await self._run_in_executor(self.redis.expire, f"email:{email_id}", 86400)

        # 加入发送队列
        await self._run_in_executor(self.redis.lpush, "email_queue", email_id)

        logger.info(f"Email enqueued with id: {email_id} to {to_email}")
        return email_id

    async def get_email_status(self, email_id: str) -> dict[str, Any]:
        """
        获取邮件发送状态

        Args:
            email_id: 邮件任务ID

        Returns:
            邮件任务状态信息
        """
        email_data = await self._run_in_executor(self.redis.hgetall, f"email:{email_id}")

        # 解码Redis返回的字节数据
        if email_data:
            return {
                k.decode("utf-8") if isinstance(k, bytes) else k: v.decode("utf-8") if isinstance(v, bytes) else v
                for k, v in email_data.items()
            }

        return {"status": "not_found"}

    async def _process_email_queue(self):
        """处理邮件队列"""
        logger.info("Starting email queue processor")

        while self._processing:
            try:
                # 从队列获取邮件ID
                def brpop_operation():
                    return self.redis.brpop(["email_queue"], timeout=5)

                result = await self._run_in_executor(brpop_operation)

                if not result:
                    await asyncio.sleep(1)
                    continue

                # 解包返回结果（列表名和值）
                _, email_id = result
                if isinstance(email_id, bytes):
                    email_id = email_id.decode("utf-8")

                # 获取邮件数据
                email_data = await self.get_email_status(email_id)
                if email_data.get("status") == "not_found":
                    logger.warning(f"Email data not found for id: {email_id}")
                    continue

                # 更新状态为发送中
                await self._run_in_executor(self.redis.hset, f"email:{email_id}", "status", "sending")

                # 尝试发送邮件
                success = await self._send_email(email_data)

                if success:
                    # 更新状态为已发送
                    await self._run_in_executor(self.redis.hset, f"email:{email_id}", "status", "sent")
                    await self._run_in_executor(
                        self.redis.hset,
                        f"email:{email_id}",
                        "sent_at",
                        datetime.now().isoformat(),
                    )
                    logger.info(f"Email {email_id} sent successfully to {email_data.get('to_email')}")
                else:
                    # 计算重试次数
                    retry_count = int(email_data.get("retry_count", "0")) + 1

                    if retry_count <= self._retry_limit:
                        # 重新入队，稍后重试
                        await self._run_in_executor(
                            self.redis.hset,
                            f"email:{email_id}",
                            "retry_count",
                            str(retry_count),
                        )
                        await self._run_in_executor(self.redis.hset, f"email:{email_id}", "status", "pending")
                        await self._run_in_executor(
                            self.redis.hset,
                            f"email:{email_id}",
                            "last_retry",
                            datetime.now().isoformat(),
                        )

                        # 延迟重试（使用指数退避）
                        delay = 60 * (2 ** (retry_count - 1))  # 1分钟，2分钟，4分钟...

                        # 创建延迟任务
                        bg_tasks.add_task(self._delayed_retry, email_id, delay)

                        logger.warning(f"Email {email_id} will be retried in {delay} seconds (attempt {retry_count})")
                    else:
                        # 超过重试次数，标记为失败
                        await self._run_in_executor(self.redis.hset, f"email:{email_id}", "status", "failed")
                        logger.error(f"Email {email_id} failed after {retry_count} attempts")

            except Exception as e:
                logger.error(f"Error processing email queue: {e}")
                await asyncio.sleep(5)  # 出错后等待5秒

    async def _delayed_retry(self, email_id: str, delay: int):
        """延迟重试发送邮件"""
        await asyncio.sleep(delay)
        await self._run_in_executor(self.redis.lpush, "email_queue", email_id)
        logger.info(f"Re-queued email {email_id} for retry after {delay} seconds")

    async def _send_email(self, email_data: dict[str, Any]) -> bool:
        """
        实际发送邮件

        Args:
            email_data: 邮件数据

        Returns:
            是否发送成功
        """
        try:
            # 如果邮件发送功能被禁用，则只记录日志
            if not getattr(settings, "enable_email_sending", True):
                logger.info(f"[Mock Email] Would send to {email_data.get('to_email')}: {email_data.get('subject')}")
                return True

            # 创建邮件
            msg = MIMEMultipart("alternative")
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = email_data.get("to_email", "")
            msg["Subject"] = email_data.get("subject", "")

            # 添加纯文本内容
            content = email_data.get("content", "")
            if content:
                msg.attach(MIMEText(content, "plain", "utf-8"))

            # 添加HTML内容（如果有）
            html_content = email_data.get("html_content", "")
            if html_content:
                msg.attach(MIMEText(html_content, "html", "utf-8"))

            # 发送邮件
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.smtp_username and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_username, self.smtp_password)

                server.send_message(msg)

            return True

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False


# 全局邮件队列实例
email_queue = EmailQueue()


# 在应用启动时调用
async def start_email_processor():
    await email_queue.start_processing()


# 在应用关闭时调用
async def stop_email_processor():
    await email_queue.stop_processing()
