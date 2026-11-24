"""SSRF guards for supplier / outbound HTTP URLs."""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse

from src.config import settings


_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


class UnsafeURLError(ValueError):
    """Raised when a URL fails SSRF validation."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    for net in _BLOCKED_NETWORKS:
        try:
            if ip in net:
                return True
        except Exception:
            continue
    return False


def validate_outbound_url(url: str, *, allow_http_in_dev: bool = True) -> str:
    """
    Allow only public https:// (http:// only when ENVIRONMENT=development).
    Resolves hostname and rejects private / loopback / link-local / metadata IPs.
    Returns the normalized URL string.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeURLError("URL is required")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeURLError("URL hostname is required")

    is_dev = (settings.ENVIRONMENT or "").strip().lower() in ("development", "dev", "test", "testing")
    if scheme == "https":
        pass
    elif scheme == "http" and allow_http_in_dev and is_dev:
        pass
    else:
        raise UnsafeURLError("Only https:// URLs are allowed (http:// only in development)")

    # Block literal IPs in private ranges immediately
    try:
        literal = ipaddress.ip_address(host)
        if _is_blocked_ip(literal):
            raise UnsafeURLError(f"Blocked IP address: {host}")
        return raw
    except ValueError:
        pass  # hostname, not an IP literal

    if host in ("localhost", "metadata.google.internal"):
        raise UnsafeURLError(f"Blocked hostname: {host}")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeURLError(f"DNS resolution failed for {host}: {e}") from e

    if not infos:
        raise UnsafeURLError(f"No addresses resolved for {host}")

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise UnsafeURLError(f"Hostname {host} resolves to blocked IP {ip_str}")

    return raw


def validate_supplier_config_urls(config: Optional[dict]) -> None:
    """Validate base_url inside a supplier config dict if present."""
    if not config:
        return
    base = config.get("base_url")
    if base:
        validate_outbound_url(str(base))
