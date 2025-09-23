"""GeoIP initialisation and scheduling services."""

from __future__ import annotations

from .geoip_scheduler import schedule_geoip_updates
from .init_geoip import init_geoip

__all__ = ["init_geoip", "schedule_geoip_updates"]
