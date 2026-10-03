"""Abuse configuration: layer switches, bucket limits, thresholds and risk rules.

Shape of `PUT /admin/abuse/config`: `{layers: {L1..L8: bool}, thresholds: {...}}`; `limits` and
`rules` are accepted too. The document is persisted through an optional `persist` callback
(the backend passes one that writes `app_settings['abuse_config']` in Postgres, the source of
truth) and cached in Redis with a version counter. Each worker re-reads the version at most
once per second, so a change reaches every worker within about a second. If Redis is down the
worker keeps its last known config.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

CONFIG_KEY = "abuse:cfg"
VERSION_KEY = "abuse:cfg:ver"
REFRESH_S = 1.0

LAYERS = tuple(f"L{i}" for i in range(1, 9))

# [rate_per_s, burst]. L1 pools are global per endpoint group. Authenticated sessions get
# their own pool, so anonymous junk can never use up the capacity reserved for signed-in users.
DEFAULT_LIMITS: dict[str, Any] = {
    "L1": {
        "otp": {"anon": [500, 1000]},
        "read": {"anon": [20000, 40000]},
        "entries": {"auth": [3000, 6000], "anon": [50, 100]},
        "me": {"auth": [15000, 30000], "anon": [100, 200]},
        "claim": {"auth": [2000, 4000], "anon": [50, 100]},
        "step_up": {"auth": [500, 1000], "anon": [20, 40]},
        "other": {"anon": [1000, 2000]},
    },
    # Signed-in traffic from one IP gets a larger budget than anonymous traffic: many genuine
    # users share one IP behind campus Wi-Fi or mobile carrier NAT, and they are already
    # limited per session. The /24 limit is sized for ~2,000 users polling every 2 s.
    "L2": {"ip_anon": [20, 40], "ip_auth": [100, 200], "net24": [1500, 3000]},
    "L3": {
        "session": [3, 6],
        # Two tabs share one session; each polls at the server's fastest pace (1 s), plus
        # refreshes. 4/s with burst 8 is 2x that, so genuine multi-tab users never hit it.
        "session_me": [4, 8],
        "user": [6, 12],
    },
}

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "step_up_score": 60,
    "cooldown_violations": 20,
    "cooldown_window_ms": 60_000,
    "cooldown_ms": 30_000,
    "slow_ttl_ms": 30_000,
    "fallback_max_keys": 100_000,
    # L6 (OTP) limits: [count, window_s]
    "otp_per_phone": [3, 600],
    "otp_per_device_phones": [3, 600],
    "otp_per_ip": [60, 60],
    "otp_per_net24": [300, 60],
    "otp_prefix_distinct": [20, 60],
    "otp_prefix_block_s": 60,
}

# L7 rules (design section 8). Points are additive, capped at 100; no single rule reaches the
# step-up threshold on its own.
DEFAULT_RULES: dict[str, Any] = {
    "R_DEVICE": {"enabled": True, "points": 40, "users": 3},
    "R_SUBNET": {
        "enabled": True,
        "points": 25,
        "new_users": 20,
        "asn_new_users": 100,
        "window_s": 60,
    },
    "R_FAST_OTP": {"enabled": True, "points": 15, "ms": 2000},
    "R_UA": {"enabled": True, "points": 10, "users": 50, "window_s": 60},
}


class ConfigError(ValueError):
    """Raised for an invalid config document (maps to VALIDATION_ERROR)."""


@dataclass
class AbuseConfig:
    layers: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(LAYERS, True))
    limits: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_LIMITS))
    thresholds: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_THRESHOLDS))
    rules: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_RULES))

    def on(self, layer: str) -> bool:
        return self.layers.get(layer, True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layers": self.layers,
            "limits": self.limits,
            "thresholds": self.thresholds,
            "rules": self.rules,
        }

    def merged(self, payload: dict[str, Any]) -> AbuseConfig:
        """Return a new config with `payload` merged in. Unknown layers or bad types raise."""
        if not isinstance(payload, dict):
            raise ConfigError("config must be an object")
        new = AbuseConfig(
            dict(self.layers),
            copy.deepcopy(self.limits),
            copy.deepcopy(self.thresholds),
            copy.deepcopy(self.rules),
        )
        layers = payload.get("layers", {})
        if not isinstance(layers, dict):
            raise ConfigError("layers must be an object")
        for k, v in layers.items():
            if k not in LAYERS or not isinstance(v, bool):
                raise ConfigError(f"bad layer switch: {k}")
            new.layers[k] = v
        for section in ("limits", "thresholds", "rules"):
            part = payload.get(section, {})
            if not isinstance(part, dict):
                raise ConfigError(f"{section} must be an object")
            _deep_update(getattr(new, section), part, section)
        return new

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AbuseConfig:
        return cls().merged(d)


def _deep_update(dst: dict[str, Any], src: dict[str, Any], path: str) -> None:
    for k, v in src.items():
        if k not in dst:
            raise ConfigError(f"unknown key {path}.{k}")
        if isinstance(dst[k], dict):
            if not isinstance(v, dict):
                raise ConfigError(f"{path}.{k} must be an object")
            _deep_update(dst[k], v, f"{path}.{k}")
        elif isinstance(dst[k], list):
            if not (
                isinstance(v, list)
                and len(v) == len(dst[k])
                and all(isinstance(x, int | float) and x > 0 for x in v)
            ):
                raise ConfigError(f"{path}.{k} must be a list of {len(dst[k])} positive numbers")
            dst[k] = list(v)
        elif isinstance(dst[k], bool):
            if not isinstance(v, bool):
                raise ConfigError(f"{path}.{k} must be a boolean")
            dst[k] = v
        else:
            if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
                raise ConfigError(f"{path}.{k} must be a non-negative number")
            dst[k] = v


Persist = Callable[[dict[str, Any]], Awaitable[None]]


class ConfigStore:
    """Per-worker view of the shared config. `current()` never does I/O on the request path."""

    def __init__(
        self,
        redis: Redis | None,
        persist: Persist | None = None,
        initial: AbuseConfig | None = None,
    ) -> None:
        self.redis, self.persist = redis, persist
        self._cfg = initial or AbuseConfig()
        self._version = -1
        self._checked = 0.0

    def current(self) -> AbuseConfig:
        return self._cfg

    def due(self) -> bool:
        return self.redis is not None and time.monotonic() - self._checked >= REFRESH_S

    async def refresh(self) -> None:
        """Pick up a newer version from Redis. Errors keep the last known config."""
        self._checked = time.monotonic()
        if self.redis is None:
            return
        try:
            ver = await self.redis.get(VERSION_KEY)
            if ver is None or int(ver) == self._version:
                return
            raw = await self.redis.get(CONFIG_KEY)
            if raw is not None:
                self._cfg = AbuseConfig.from_dict(json.loads(raw))
            self._version = int(ver)
        except (RedisError, OSError, ValueError):
            return

    async def update(self, payload: dict[str, Any]) -> AbuseConfig:
        """Validate, persist (Postgres via `persist`), publish to Redis, apply locally."""
        new = self._cfg.merged(payload)
        if self.persist is not None:
            await self.persist(new.to_dict())
        if self.redis is not None:
            try:
                await self.redis.set(CONFIG_KEY, json.dumps(new.to_dict()))
                self._version = int(await self.redis.incr(VERSION_KEY))
            except (RedisError, OSError):
                pass
        self._cfg = new
        return new
