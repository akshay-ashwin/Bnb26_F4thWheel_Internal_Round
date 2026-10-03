"""Client IP, /24 network and user-agent hash (shared by the limiter, risk rules and entries)."""

from __future__ import annotations

import hashlib
import ipaddress
from functools import lru_cache

from starlette.requests import Request

from app.config import Settings


@lru_cache(maxsize=8)
def _trusted(cidrs: tuple[str, ...]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(c, strict=False) for c in cidrs)


def _parse(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def client_ip(request: Request, settings: Settings) -> str:
    """The caller's IP.

    1. SIM_MODE only: `X-Sim-Client-IP` (a test-harness stand-in for real source addresses).
    2. If the socket peer is a trusted proxy: the right-most untrusted `X-Forwarded-For` hop.
    3. Otherwise the socket peer. Never used to validate a session (IPs change on mobile).
    """
    if settings.sim_mode:
        sim = request.headers.get("x-sim-client-ip")
        if sim and _parse(sim) is not None:
            return sim.strip()
    peer = request.client.host if request.client else "0.0.0.0"  # noqa: S104 - sentinel, not a bind
    peer_ip = _parse(peer)
    nets = _trusted(settings.trusted_proxy_cidrs)
    if peer_ip is not None and any(peer_ip in n for n in nets):
        forwarded = request.headers.get("x-forwarded-for", "")
        for hop in reversed([h for h in forwarded.split(",") if h.strip()]):
            ip = _parse(hop)
            if ip is not None and not any(ip in n for n in nets):
                return str(ip)
    return peer


def network_of(ip: str) -> str:
    """/24 for IPv4, /48 for IPv6 (the unit the risk rules cluster on)."""
    parsed = _parse(ip)
    if parsed is None:
        return "unknown"
    prefix = 24 if parsed.version == 4 else 48
    return str(ipaddress.ip_network(f"{parsed}/{prefix}", strict=False))


def ua_hash(request: Request) -> str:
    ua = request.headers.get("user-agent", "")[:512]
    return hashlib.sha256(ua.encode()).hexdigest()
