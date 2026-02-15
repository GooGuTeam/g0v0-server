from functools import lru_cache
import ipaddress
from typing import Annotated

from app.config import settings
from app.helpers import GeoIPHelper

from fast_depends import Depends as DIDepends
from fastapi import Depends, Request


@lru_cache
def get_geoip_helper() -> GeoIPHelper:
    """
    Get GeoIP helper instance with LRU cache to ensure singleton pattern
    """
    return GeoIPHelper(
        dest_dir=settings.geoip_dest_dir,
        license_key=settings.maxmind_license_key,
        editions=["City", "ASN"],
        max_age_days=8,
        timeout=60.0,
    )


def get_client_ip(request: Request) -> str:
    """
    Get the client's real IP address
    Supports IPv4 and IPv6, considering proxies, load balancers, etc.
    """
    headers = request.headers

    # 1. Cloudflare specific headers
    cf_ip = headers.get("CF-Connecting-IP")
    if cf_ip:
        ip = cf_ip.strip()
        if is_valid_ip(ip):
            return ip

    true_client_ip = headers.get("True-Client-IP")
    if true_client_ip:
        ip = true_client_ip.strip()
        if is_valid_ip(ip):
            return ip

    # 2. Standard proxy headers
    forwarded_for = headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For may contain multiple IPs, take the first valid one
        for ip_str in forwarded_for.split(","):
            ip = ip_str.strip()
            if is_valid_ip(ip) and not is_private_ip(ip):
                return ip

    real_ip = headers.get("X-Real-IP")
    if real_ip:
        ip = real_ip.strip()
        if is_valid_ip(ip):
            return ip

    # 3. Fallback to request.client.host, but validate it first
    client_ip = request.client.host if request.client else "127.0.0.1"
    return client_ip if is_valid_ip(client_ip) else "127.0.0.1"


IPAddress = Annotated[str, Depends(get_client_ip), DIDepends(get_client_ip)]
GeoIPService = Annotated[GeoIPHelper, Depends(get_geoip_helper), DIDepends(get_geoip_helper)]


def is_valid_ip(ip_str: str) -> bool:
    """
    Validate if the IP address is valid (supports IPv4 and IPv6)
    """
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False


def is_private_ip(ip_str: str) -> bool:
    """
    Check if the IP address is private
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private
    except ValueError:
        return False


def normalize_ip(ip_str: str) -> str:
    """
    Normalize IP address format
    For IPv6, convert to compressed format
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        if isinstance(ip, ipaddress.IPv6Address):
            return ip.compressed
        else:
            return str(ip)
    except ValueError:
        return ip_str
