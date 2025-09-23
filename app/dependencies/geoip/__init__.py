"""GeoIP dependency utilities."""

from __future__ import annotations

from functools import lru_cache
import ipaddress

from app.config import settings
from app.helpers import GeoIPHelper


@lru_cache
def get_geoip_helper() -> GeoIPHelper:
    """
    获取 GeoIP 帮助类实例
    使用 lru_cache 确保单例模式
    """
    return GeoIPHelper(
        dest_dir=settings.geoip_dest_dir,
        license_key=settings.maxmind_license_key,
        editions=["City", "ASN"],
        max_age_days=8,
        timeout=60.0,
    )


def get_client_ip(request) -> str:
    """
    获取客户端真实 IP 地址
    支持 IPv4 和 IPv6，考虑代理、负载均衡器等情况
    """
    headers = request.headers

    # 1. Cloudflare 专用头部
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

    # 2. 标准代理头部
    forwarded_for = headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For 可能包含多个 IP，取第一个有效的
        for ip_str in forwarded_for.split(","):
            ip = ip_str.strip()
            if is_valid_ip(ip) and not is_private_ip(ip):
                return ip

    real_ip = headers.get("X-Real-IP")
    if real_ip:
        ip = real_ip.strip()
        if is_valid_ip(ip):
            return ip

    # 3. 回退到客户端 IP
    client_ip = request.client.host if request.client else "127.0.0.1"
    return client_ip if is_valid_ip(client_ip) else "127.0.0.1"


def is_valid_ip(ip_str: str) -> bool:
    """
    验证 IP 地址是否有效（支持 IPv4 和 IPv6）
    """
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False


def is_private_ip(ip_str: str) -> bool:
    """
    判断是否为私有 IP 地址
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private
    except ValueError:
        return False


def normalize_ip(ip_str: str) -> str:
    """
    标准化 IP 地址格式
    对于 IPv6，转换为压缩格式
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        if isinstance(ip, ipaddress.IPv6Address):
            return ip.compressed
        else:
            return str(ip)
    except ValueError:
        return ip_str
