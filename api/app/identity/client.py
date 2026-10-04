"""Who is calling: client IP, its /24 (/48 for IPv6) network, and a User-Agent hash.

Used by L2 (per-IP buckets), L6, L7 and entries, so the rule lives in one place:
  1. SIM_MODE only: a valid `X-Sim-Client-IP` header wins (the test harness substitute for real
     client addresses, design section 15). With SIM_MODE=false the header is ignored.
  2. Peer is a trusted proxy: walk `X-Forwarded-For` right to left and take the first address that
     is not itself a trusted proxy. Entries to the left are client-supplied and are never believed
     past the first untrusted hop.
  3. Otherwise the socket peer. (A direct caller cannot pick its own address with a header.)
"""

import hashlib
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from starlette.requests import Request

from app.config import Settings

_UA_MAX_CHARS = 512
IPAddress = IPv4Address | IPv6Address


@dataclass(frozen=True, slots=True)
class ClientInfo:
    ip: str
    network: str
    ua_hash: str


def _parse(value: str) -> IPAddress | None:
    try:
        addr = ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(addr, IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_trusted(addr: IPAddress, settings: Settings) -> bool:
    return any(
        addr in net for net in settings.trusted_proxy_networks if net.version == addr.version
    )


def resolve_ip(request: Request, settings: Settings) -> IPAddress:
    if settings.sim_mode:
        override = request.headers.get("x-sim-client-ip")
        if override:
            parsed = _parse(override)
            if parsed is not None:
                return parsed
    peer = _parse(request.client.host) if request.client else None
    if peer is None:
        return IPv4Address("0.0.0.0")  # noqa: S104 (a placeholder value, not a bind address)
    if not _is_trusted(peer, settings):
        return peer
    hops = ",".join(request.headers.getlist("x-forwarded-for")).split(",")
    for hop in reversed(hops):
        addr = _parse(hop)
        if addr is None:
            return peer  # garbage can only have been added left of every trusted hop
        if not _is_trusted(addr, settings):
            return addr
    return peer


def network_of(addr: IPAddress) -> str:
    prefix = 24 if addr.version == 4 else 48
    return str(ip_network(f"{addr}/{prefix}", strict=False))


def client_info(request: Request, settings: Settings) -> ClientInfo:
    addr = resolve_ip(request, settings)
    ua = request.headers.get("user-agent", "")[:_UA_MAX_CHARS]
    return ClientInfo(
        ip=str(addr),
        network=network_of(addr),
        ua_hash=hashlib.sha256(ua.encode("utf-8", "replace")).hexdigest(),
    )
