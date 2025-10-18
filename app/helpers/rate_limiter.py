"""
Rate limiter for osu! API requests to avoid abuse detection.
根据 osu! API v2 的速率限制设计：
- 默认：每分钟最多 1200 次请求
- 突发：短时间内最多 200 次额外请求
- 建议：每分钟不超过 60 次请求以避免滥用检测
"""

import asyncio
from collections import deque
import time

from app.log import logger


class RateLimiter:
    """osu! API 速率限制器"""

    def __init__(
        self,
        max_requests_per_minute: int = 60,  # 保守的限制
        burst_limit: int = 10,  # 短时间内的突发限制
        burst_window: float = 10.0,  # 突发窗口（秒）
    ):
        self.max_requests_per_minute = max_requests_per_minute
        self.burst_limit = burst_limit
        self.burst_window = burst_window

        # 跟踪请求时间戳
        self.request_times: deque[float] = deque()
        self.burst_times: deque[float] = deque()

        # 锁确保线程安全
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取请求许可，如果超过限制则等待"""
        async with self._lock:
            current_time = time.time()

            # 清理过期的请求记录
            self._cleanup_old_requests(current_time)

            # 检查是否需要等待
            wait_time = self._calculate_wait_time(current_time)

            if wait_time > 0:
                logger.opt(colors=True).info(
                    f"<yellow>[RateLimiter]</yellow> Rate limit reached, waiting {wait_time:.2f}s"
                )
                await asyncio.sleep(wait_time)
                current_time = time.time()
                self._cleanup_old_requests(current_time)

            # 记录当前请求
            self.request_times.append(current_time)
            self.burst_times.append(current_time)

            logger.opt(colors=True).debug(
                f"<green>[RateLimiter]</green> Request granted. "
                f"Recent requests: {len(self.request_times)}/min, "
                f"{len(self.burst_times)}/{self.burst_window}s"
            )

    def _cleanup_old_requests(self, current_time: float) -> None:
        """清理过期的请求记录"""
        # 清理1分钟前的请求
        minute_ago = current_time - 60.0
        while self.request_times and self.request_times[0] < minute_ago:
            self.request_times.popleft()

        # 清理突发窗口外的请求
        burst_window_ago = current_time - self.burst_window
        while self.burst_times and self.burst_times[0] < burst_window_ago:
            self.burst_times.popleft()

    def _calculate_wait_time(self, current_time: float) -> float:
        """计算需要等待的时间"""
        # 检查每分钟限制
        if len(self.request_times) >= self.max_requests_per_minute:
            # 需要等到最老的请求超过1分钟
            oldest_request = self.request_times[0]
            wait_for_minute_limit = oldest_request + 60.0 - current_time
        else:
            wait_for_minute_limit = 0.0

        # 检查突发限制
        if len(self.burst_times) >= self.burst_limit:
            # 需要等到最老的突发请求超过突发窗口
            oldest_burst = self.burst_times[0]
            wait_for_burst_limit = oldest_burst + self.burst_window - current_time
        else:
            wait_for_burst_limit = 0.0

        return max(wait_for_minute_limit, wait_for_burst_limit, 0.0)

    def get_status(self) -> dict[str, int | float]:
        """获取当前速率限制状态"""
        current_time = time.time()
        self._cleanup_old_requests(current_time)

        return {
            "requests_this_minute": len(self.request_times),
            "max_requests_per_minute": self.max_requests_per_minute,
            "burst_requests": len(self.burst_times),
            "burst_limit": self.burst_limit,
            "next_reset_in_seconds": (60.0 - (current_time - self.request_times[0]) if self.request_times else 0.0),
        }


# 全局速率限制器实例
osu_api_rate_limiter = RateLimiter(
    max_requests_per_minute=50,  # 保守设置，低于建议的60
    burst_limit=8,  # 短时间内最多8个请求
    burst_window=10.0,  # 10秒窗口
)
