"""Client identity resolution for rate limits, login logs and trusted devices."""

import asyncio
from functools import lru_cache
import ipaddress
import re
from typing import Annotated

from app.config import settings
from app.const import CLOUDFLARE_PROXY_NETWORKS
from app.helpers import GeoIPHelper
from app.log import log

from fast_depends import Depends as FastDepends
from fastapi import Depends, Request
import httpx

logger = log("ClientIdentity")

ProxyAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
ProxyNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_MAX_IDENTITY_WARNINGS = 8
_warned_identity_peers: set[str] = set()
_warned_rewritten_by_server = False

# Publishers use plain text, JSON (AWS ip-ranges) or CSV, and list either CIDRs or
# bare addresses (Bunny's edge list), so pull every address token out instead of
# assuming one shape; bare tokens become single-host networks.
_SOURCE_TOKEN_PATTERN = re.compile(r"[0-9A-Fa-f:.]{2,45}(?:/\d{1,3})?")
_SOURCE_TIMEOUT_SECONDS = 5.0
_source_networks: dict[str, tuple[ProxyNetwork, ...]] = {}
_merged_networks: tuple[ProxyNetwork, ...] | None = None


@lru_cache
def get_geoip_helper() -> GeoIPHelper:
    """
    Get GeoIP helper instance with LRU cache to ensure singleton pattern
    """
    return GeoIPHelper(
        dest_dir=settings.geoip_dest_dir,
        editions=["Country", "ASN"],
        max_age_days=8,
        timeout=60.0,
    )


def _parse_networks(values: list[str] | tuple[str, ...]) -> tuple[ProxyNetwork, ...]:
    networks: list[ProxyNetwork] = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value.strip(), strict=False))
        except ValueError:
            logger.warning("Ignoring malformed proxy range: {}", value)
    return tuple(networks)


@lru_cache
def _static_trusted_proxy_networks() -> tuple[ProxyNetwork, ...]:
    return _parse_networks(settings.trusted_proxies)


def trusted_proxy_networks() -> tuple[ProxyNetwork, ...]:
    global _merged_networks
    if _merged_networks is None:
        fetched = dict.fromkeys(network for networks in _source_networks.values() for network in networks)
        _merged_networks = _static_trusted_proxy_networks() + tuple(fetched)
    return _merged_networks


@lru_cache
def cloudflare_proxy_networks() -> tuple[ProxyNetwork, ...]:
    return _parse_networks(CLOUDFLARE_PROXY_NETWORKS)


def _parse_source_networks(payload: str) -> tuple[ProxyNetwork, ...]:
    networks: list[ProxyNetwork] = []
    for candidate in _SOURCE_TOKEN_PATTERN.findall(payload):
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    return tuple(dict.fromkeys(networks))


async def _fetch_source(client: httpx.AsyncClient, url: str) -> tuple[ProxyNetwork, ...] | None:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except Exception as error:
        logger.warning("Trusted proxy source {} failed: {}", url, type(error).__name__)
        return None
    networks = _parse_source_networks(response.text)
    if not networks:
        logger.warning("Trusted proxy source {} returned no CIDR ranges", url)
        return None
    return networks


async def refresh_trusted_proxies() -> None:
    """Reload trusted proxy ranges from the configured publishers.

    Sources are fetched concurrently and cached per URL, so a single failing
    publisher keeps serving its previous ranges instead of widening trust.
    """
    sources = tuple(settings.trusted_proxy_sources)
    if not sources:
        return
    async with httpx.AsyncClient(timeout=_SOURCE_TIMEOUT_SECONDS, follow_redirects=True) as client:
        results = await asyncio.gather(*(_fetch_source(client, url) for url in sources))

    for url, networks in zip(sources, results, strict=True):
        if networks is not None:
            _source_networks[url] = networks
        elif url in _source_networks:
            logger.warning("Keeping {} cached range(s) for {}", len(_source_networks[url]), url)
    for url in set(_source_networks) - set(sources):
        del _source_networks[url]

    global _merged_networks
    _merged_networks = None

    unique = {network for networks in _source_networks.values() for network in networks}
    if unique:
        logger.info(
            "Loaded {} trusted proxy range(s) ({} after de-duplication) from {}",
            sum(len(networks) for networks in _source_networks.values()),
            len(unique),
            ", ".join(_source_networks),
        )
    if any(network.prefixlen == 0 for networks in _source_networks.values() for network in networks):
        logger.warning("A trusted proxy source published a catch-all range; clients can forge their identity")


def _contains(address: ProxyAddress, networks: tuple[ProxyNetwork, ...]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def _parse_address(value: str | None) -> ProxyAddress | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    # Proxies on dual-stack sockets report IPv4 peers as ::ffff:a.b.c.d.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _format(address: ProxyAddress) -> str:
    return address.compressed if isinstance(address, ipaddress.IPv6Address) else str(address)


def _forwarded_hops(value: str | None) -> list[ProxyAddress]:
    hops: list[ProxyAddress] = []
    for item in (value or "").split(","):
        address = _parse_address(item)
        if address is not None:
            hops.append(address)
    return hops


def _cloudflare_client(request: Request) -> str | None:
    """CF-Connecting-IP is authoritative because the edge overwrites it."""
    cloudflare_ip = _parse_address(request.headers.get("cf-connecting-ip"))
    return _format(cloudflare_ip) if cloudflare_ip is not None else None


def _warn_identity_collapse(peer: ProxyAddress) -> None:
    key = _format(peer)
    if key in _warned_identity_peers or len(_warned_identity_peers) >= _MAX_IDENTITY_WARNINGS:
        return
    _warned_identity_peers.add(key)
    logger.warning(
        "Client identity falls back to proxy address {} without forwarding headers; "
        "rate limits and trusted devices are shared between its clients. Point TRUSTED_PROXIES "
        "at the proxy and have it send X-Forwarded-For or X-Real-IP.",
        key,
    )


def _warn_rewritten_by_server() -> None:
    global _warned_rewritten_by_server
    if _warned_rewritten_by_server:
        return
    _warned_rewritten_by_server = True
    logger.warning(
        "The ASGI server already substituted the client address, so this application cannot "
        "verify which proxy opened the connection. Start uvicorn with --no-proxy-headers and "
        "let TRUSTED_PROXIES drive the resolution."
    )


def log_client_identity_configuration() -> None:
    """Startup self-check for the chain that separates one client from another."""
    static = _static_trusted_proxy_networks()
    networks = trusted_proxy_networks()
    if not networks:
        logger.warning(
            "TRUSTED_PROXIES is empty: forwarding headers are ignored, so every client behind "
            "a proxy shares one identity and trusted devices cannot be told apart"
        )
        return
    if any(network.prefixlen == 0 for network in networks):
        logger.warning("TRUSTED_PROXIES trusts every address; clients can forge their own identity")
    logger.info("Trusted proxy ranges: {}", ", ".join(str(network) for network in static) or "none")
    if settings.trusted_proxy_sources:
        logger.info(
            "Trusted proxy sources: {} ({} fetched range(s), refresh every {} min)",
            ", ".join(settings.trusted_proxy_sources),
            len(networks) - len(static),
            settings.trusted_proxy_refresh_minutes,
        )


def get_client_ip(request: Request) -> str:
    """Resolve the connecting client, honouring forwarding headers only from trusted proxies.

    Uvicorn proxy handling must stay disabled (``--no-proxy-headers``) so that
    ``request.client`` still carries the peer that opened the connection:
    a rewritten peer cannot be validated against TRUSTED_PROXIES.
    """
    peer = _parse_address(request.client.host if request.client else None)
    if peer is None:
        return "127.0.0.1"

    hops = _forwarded_hops(request.headers.get("x-forwarded-for"))
    if not _contains(peer, trusted_proxy_networks()):
        if any(hop == peer for hop in hops):
            _warn_rewritten_by_server()
        # Forwarding headers from an untrusted peer are attacker-controlled.
        return _format(peer)

    if hops:
        if _contains(hops[-1], cloudflare_proxy_networks()):
            # Cloudflare appends its edge to X-Forwarded-For but lets clients
            # prefill the leading entries, so the edge header is the only truth.
            cloudflare_ip = _cloudflare_client(request)
            if cloudflare_ip is not None:
                return cloudflare_ip
        for hop in reversed(hops):
            if not _contains(hop, trusted_proxy_networks()):
                return _format(hop)
        return _format(hops[0])

    real_ip = _parse_address(request.headers.get("x-real-ip"))
    if real_ip is not None:
        if _contains(real_ip, cloudflare_proxy_networks()):
            cloudflare_ip = _cloudflare_client(request)
            if cloudflare_ip is not None:
                return cloudflare_ip
        elif not _contains(real_ip, trusted_proxy_networks()):
            return _format(real_ip)

    if not peer.is_loopback:
        _warn_identity_collapse(peer)
    return _format(peer)


IPAddress = Annotated[str, Depends(get_client_ip), FastDepends(get_client_ip)]
GeoIPService = Annotated[GeoIPHelper, Depends(get_geoip_helper), FastDepends(get_geoip_helper)]


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
