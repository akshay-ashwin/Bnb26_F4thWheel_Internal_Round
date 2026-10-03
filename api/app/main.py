"""Placeholder app: just enough for the compose health check. Plan 03 replaces the structure."""

from datetime import UTC, datetime

from fastapi import FastAPI

app = FastAPI(title="Fair Drop API")


@app.get("/api/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "server_time": datetime.now(UTC).isoformat()}
