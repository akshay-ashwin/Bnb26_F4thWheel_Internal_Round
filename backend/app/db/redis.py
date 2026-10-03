from redis.asyncio import Redis

from app.config import get_settings

redis_client: Redis = Redis.from_url(get_settings().redis_url, socket_connect_timeout=1)
