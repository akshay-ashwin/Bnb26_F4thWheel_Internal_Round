# Redis configuration

Redis is a cache and counter store, never the source of truth (invariant 3). `redis.conf` sets:

- **No persistence** (`save ""`, `appendonly no`): after a restart Redis is empty and the API rebuilds what it needs from Postgres.
- **`maxmemory 512mb` with `maxmemory-policy volatile-ttl`** (decision recorded in the Plan 01 review log). `volatile-ttl` only evicts keys that have a TTL. We rejected `allkeys-lru` because under memory pressure it could evict a rate-limit bucket or a `jti` (single-use token) marker without warning. Every key the API writes has a TTL except `drop:{id}:remaining`, which is recomputable from Postgres.
- **`protected-mode no` and `bind 0.0.0.0`** are only safe because Redis is reachable inside the compose network; the host port is published on `127.0.0.1` only.
