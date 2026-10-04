"""Manual benchmark (not collected by pytest): p50/p95/p99 of POST /auth/otp/verify at N concurrent.

    docker compose run --rm --no-deps api python tests/bench_verify.py http://api:8000 200 3

Needs a running api with SIM_MODE=true (it reads dev_otp). Each round uses fresh phone numbers, so
every verify creates a new user and session (the expensive path). The OTP requests are not timed.
"""

import asyncio
import json
import random
import statistics
import sys
import time
from collections import Counter
from urllib.parse import urlsplit

import httpx


async def raw_post(host: str, port: int, path: str, payload: dict[str, str]) -> int:
    """One HTTP/1.1 POST on its own connection. httpx/httpcore stalls for seconds with a couple of
    hundred simultaneous requests (a client artefact), so the timed phase uses plain streams."""
    body = json.dumps(payload).encode()
    reader, writer = await asyncio.open_connection(host, port)
    lines = [
        f"POST {path} HTTP/1.1",
        f"Host: {host}",
        "Content-Type: application/json",
        f"Content-Length: {len(body)}",
        "Connection: close",
        "",
        "",
    ]
    writer.write("\r\n".join(lines).encode() + body)
    await writer.drain()
    status_line = await reader.readline()
    await reader.read()  # until the server closes
    writer.close()
    return int(status_line.split()[1])


async def one_round(
    client: httpx.AsyncClient, n: int, target: tuple[str, int]
) -> tuple[list[float], Counter[int]]:
    base = random.randint(9_000_000_000, 9_999_999_000)  # noqa: S311 (not a secret)
    phones = [f"+91{base + i}" for i in range(n)]

    async def request(i: int) -> tuple[str, str, str]:
        device = f"bench-device-{base}-{i}"
        for _ in range(10):  # untimed phase: ride out a tripped Redis breaker (2 s cool-off)
            r = await client.post(
                "/api/auth/otp/request", json={"phone": phones[i], "device_id": device}
            )
            if r.status_code != 503:
                break
            await asyncio.sleep(0.5)
        if r.status_code != 200:
            raise SystemExit(f"otp/request failed: {r.status_code} {r.text[:200]}")
        body = r.json()
        return body["request_id"], body["dev_otp"], device

    # Requests are spread out a little: this benchmark times verify, not the request burst.
    issued: list[tuple[str, str, str]] = []
    for start in range(0, n, 25):
        issued += await asyncio.gather(*(request(i) for i in range(start, min(start + 25, n))))

    gate = asyncio.Event()
    latencies: list[float] = []
    statuses: Counter[int] = Counter()

    async def verify(item: tuple[str, str, str]) -> None:
        rid, otp, device = item
        await gate.wait()
        t0 = time.perf_counter()
        code = await raw_post(
            *target,
            "/api/auth/otp/verify",
            {"request_id": rid, "otp": otp, "device_id": device},
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        statuses[code] += 1

    tasks = [asyncio.create_task(verify(item)) for item in issued]
    await asyncio.sleep(0.2)
    gate.set()
    await asyncio.gather(*tasks)
    return latencies, statuses


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * len(ordered) + 0.5)) - 1)]


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://api:8000"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    parts = urlsplit(url)
    target = (parts.hostname or "127.0.0.1", parts.port or 80)
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        for k in range(1, rounds + 1):
            lat, st = await one_round(client, n, target)
            print(
                f"round {k}: n={n} statuses={dict(st)} p50={statistics.median(lat):.0f} ms"
                f" p95={pct(lat, 95):.0f} ms p99={pct(lat, 99):.0f} ms max={max(lat):.0f} ms"
            )


if __name__ == "__main__":
    asyncio.run(main())
