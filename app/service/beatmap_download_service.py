"""Beatmap download service.

Provides load balancing and health checking for beatmap download endpoints.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging

from app.models.error import ErrorType, RequestError

import httpx

logger = logging.getLogger(__name__)


@dataclass
class DownloadEndpoint:
    """Download endpoint configuration.

    Attributes:
        name: Endpoint name.
        base_url: Base URL of the endpoint.
        health_check_url: URL for health checking.
        url_template: Download URL template with {sid} and {type} placeholders.
        is_china: Whether this is a China region endpoint.
        priority: Priority (lower number = higher priority).
        timeout: Health check timeout in seconds.
    """

    name: str
    base_url: str
    health_check_url: str
    url_template: str  # Download URL template using {sid} and {type} placeholders
    is_china: bool = False
    priority: int = 0  # Priority - lower number means higher priority
    timeout: int = 10  # Health check timeout in seconds


@dataclass
class EndpointStatus:
    """Endpoint status information.

    Attributes:
        endpoint: The download endpoint.
        is_healthy: Whether the endpoint is healthy.
        last_check: Last health check timestamp.
        consecutive_failures: Number of consecutive failures.
        last_error: Last error message.
    """

    endpoint: DownloadEndpoint
    is_healthy: bool = True
    last_check: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None


class BeatmapDownloadService:
    """Beatmap download service with load balancing and health checking.

    Manages multiple download endpoints with automatic health monitoring
    and failover capabilities.
    """

    def __init__(self):
        # China region endpoints
        self.china_endpoints = [
            DownloadEndpoint(
                name="Sayobot",
                base_url="https://dl.sayobot.cn",
                health_check_url="https://dl.sayobot.cn/",
                url_template="https://dl.sayobot.cn/beatmaps/download/{type}/{sid}",
                is_china=True,
                priority=0,
                timeout=5,
            )
        ]

        # International endpoints
        self.international_endpoints = [
            DownloadEndpoint(
                name="Catboy",
                base_url="https://catboy.best",
                health_check_url="https://catboy.best/api",
                url_template="https://catboy.best/d/{sid}",
                is_china=False,
                priority=0,
                timeout=10,
            ),
            DownloadEndpoint(
                name="Nerinyan",
                base_url="https://api.nerinyan.moe",
                health_check_url="https://api.nerinyan.moe/health",
                url_template="https://api.nerinyan.moe/d/{sid}?noVideo={no_video}",
                is_china=False,
                priority=1,
                timeout=10,
            ),
            DownloadEndpoint(
                name="OsuDirect",
                base_url="https://osu.direct",
                health_check_url="https://osu.direct/api/status",
                url_template="https://osu.direct/api/d/{sid}?noVideo={no_video}",
                is_china=False,
                priority=2,
                timeout=10,
            ),
        ]

        # Endpoint status tracking
        self.endpoint_status: dict[str, EndpointStatus] = {}
        self._initialize_status()

        # Health check configuration
        self.health_check_interval = 600  # Health check interval in seconds
        self.max_consecutive_failures = 3  # Maximum consecutive failures
        self.health_check_running = False
        self.health_check_task = None  # Store health check task reference

        # HTTP client
        self.http_client = httpx.AsyncClient(timeout=30)

    def _initialize_status(self):
        """Initialize endpoint status."""
        all_endpoints = self.china_endpoints + self.international_endpoints
        for endpoint in all_endpoints:
            self.endpoint_status[endpoint.name] = EndpointStatus(endpoint=endpoint)

    async def start_health_check(self):
        """Start health check background task."""
        if self.health_check_running:
            return

        self.health_check_running = True
        self.health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info("Beatmap download service health check started")

    async def stop_health_check(self):
        """Stop health check background task."""
        self.health_check_running = False
        await self.http_client.aclose()
        logger.info("Beatmap download service health check stopped")

    async def _health_check_loop(self):
        """Health check loop."""
        while self.health_check_running:
            try:
                await self._check_all_endpoints()
                await asyncio.sleep(self.health_check_interval)
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
                await asyncio.sleep(5)  # Brief wait on error

    async def _check_all_endpoints(self):
        """Check health status of all endpoints."""
        all_endpoints = self.china_endpoints + self.international_endpoints

        # Check all endpoints concurrently
        tasks = []
        for endpoint in all_endpoints:
            task = asyncio.create_task(self._check_endpoint_health(endpoint))
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_endpoint_health(self, endpoint: DownloadEndpoint):
        """Check health status of a single endpoint."""
        status = self.endpoint_status[endpoint.name]

        try:
            async with httpx.AsyncClient(timeout=endpoint.timeout) as client:
                response = await client.get(endpoint.health_check_url)

                # Determine health status based on endpoint type
                is_healthy = False
                if endpoint.name == "Sayobot":
                    # Sayobot returns 200, 302 (Redirect), 304 (Not Modified) as healthy
                    is_healthy = response.status_code in [200, 302, 304]
                else:
                    # Other endpoints return 200 as healthy
                    is_healthy = response.status_code == 200

                if is_healthy:
                    # Health check successful
                    if not status.is_healthy:
                        logger.info(f"Endpoint {endpoint.name} is now healthy")

                    status.is_healthy = True
                    status.consecutive_failures = 0
                    status.last_error = None
                else:
                    raise httpx.HTTPStatusError(
                        f"Health check failed with status {response.status_code}",
                        request=response.request,
                        response=response,
                    )

        except Exception as e:
            # Health check failed
            status.consecutive_failures += 1
            status.last_error = str(e)

            if status.consecutive_failures >= self.max_consecutive_failures:
                if status.is_healthy:
                    logger.warning(
                        f"Endpoint {endpoint.name} marked as unhealthy after "
                        f"{status.consecutive_failures} consecutive failures: {e}"
                    )
                status.is_healthy = False

        finally:
            status.last_check = datetime.now()

    def get_healthy_endpoints(self, is_china: bool) -> list[DownloadEndpoint]:
        """Get list of healthy endpoints.

        Args:
            is_china: Whether to get China region endpoints.

        Returns:
            List of healthy endpoints sorted by priority.
        """
        endpoints = self.china_endpoints if is_china else self.international_endpoints

        healthy_endpoints = []
        for endpoint in endpoints:
            status = self.endpoint_status[endpoint.name]
            if status.is_healthy:
                healthy_endpoints.append(endpoint)

        # Sort by priority
        healthy_endpoints.sort(key=lambda x: x.priority)
        return healthy_endpoints

    def get_download_url(self, beatmapset_id: int, no_video: bool, is_china: bool) -> str:
        """Get download URL with load balancing and failover.

        Args:
            beatmapset_id: The beatmapset ID.
            no_video: Whether to exclude video.
            is_china: Whether to use China region endpoints.

        Returns:
            Download URL string.

        Raises:
            RequestError: If no endpoints are available.
        """
        healthy_endpoints = self.get_healthy_endpoints(is_china)

        if not healthy_endpoints:
            # No healthy endpoints available, log error and fallback to highest priority
            logger.error(f"No healthy endpoints available for is_china={is_china}")
            endpoints = self.china_endpoints if is_china else self.international_endpoints
            if not endpoints:
                raise RequestError(ErrorType.NO_DOWNLOAD_ENDPOINTS_AVAILABLE)
            endpoint = min(endpoints, key=lambda x: x.priority)
        else:
            # Use first healthy endpoint (already sorted by priority)
            endpoint = healthy_endpoints[0]

        # Generate URL based on endpoint type
        if endpoint.name == "Sayobot":
            video_type = "novideo" if no_video else "full"
            return endpoint.url_template.format(type=video_type, sid=beatmapset_id)
        elif endpoint.name == "Nerinyan" or endpoint.name == "OsuDirect":
            return endpoint.url_template.format(sid=beatmapset_id, no_video="true" if no_video else "false")
        elif endpoint.name == "Catboy":
            return endpoint.url_template.format(sid=f"{beatmapset_id}n" if no_video else beatmapset_id)
        else:
            # Default handling
            return endpoint.url_template.format(sid=beatmapset_id)

    def get_service_status(self) -> dict:
        """Get service status information.

        Returns:
            Dictionary containing service status and endpoint information.
        """
        status_info = {
            "service_running": self.health_check_running,
            "last_update": datetime.now().isoformat(),
            "endpoints": {},
        }

        for name, status in self.endpoint_status.items():
            status_info["endpoints"][name] = {
                "healthy": status.is_healthy,
                "last_check": status.last_check.isoformat() if status.last_check else None,
                "consecutive_failures": status.consecutive_failures,
                "last_error": status.last_error,
                "priority": status.endpoint.priority,
                "is_china": status.endpoint.is_china,
            }

        return status_info


# Global service instance
download_service = BeatmapDownloadService()
