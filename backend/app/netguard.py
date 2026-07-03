"""
Network safety guard — keep server-side fetches/probes off private infrastructure.

The hunt pipeline resolves and connects to domains derived from user queries and
scraped listings (careers-page scraping, SMTP RCPT probing). Without a guard,
a crafted query/company could point those connections at internal hosts
(localhost, 169.254.x, 10.x, …) — a classic SSRF. `resolves_public` rejects any
host that resolves to a non-public address.
"""

import ipaddress
import socket

__all__ = ["resolves_public"]


def resolves_public(host: str) -> bool:
    """True only if every A/AAAA record for `host` is a routable public address."""
    host = (host or "").strip().rstrip(".")
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for *_, sockaddr in infos:
        ip = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True
