import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

# Must be set before the app is imported. Tests TRUNCATE tables, so only *_test DBs are allowed.
_url = os.environ.get("TEST_DATABASE_URL") or "postgresql+asyncpg://fairdrop:fairdrop@localhost:5432/fairdrop_test"
assert _url.rsplit("/", 1)[-1].endswith("_test"), "Refusing to run tests against a non-test database"
os.environ["DATABASE_URL"] = _url
os.environ.setdefault("SIM_MODE", "true")
os.environ["ADMIN_API_KEY"] = "test-admin"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.utils.security import keyed_hash  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
ADMIN = {"X-Admin-Key": "test-admin"}


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND_DIR, check=True)


@pytest_asyncio.fixture(autouse=True)
async def clean_db(migrated_db):
    async with SessionLocal() as db:
        await db.execute(text("TRUNCATE users CASCADE"))  # cascades sessions/entries/allocations/idempotency
        await db.execute(text("DELETE FROM allocations"))
        await db.execute(text("DELETE FROM drops WHERE name <> 'Fair Drop Demo'"))
        await db.execute(text("UPDATE seats SET status = 'available'"))
        await db.commit()
    yield


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine():
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def demo_drop_id() -> int:
    async with SessionLocal() as db:
        return (await db.execute(text("SELECT id FROM drops WHERE name = 'Fair Drop Demo'"))).scalar_one()


async def make_drop(seats: int, mode: str = "fifo", status: str = "open") -> int:
    async with SessionLocal() as db:
        drop_id = (
            await db.execute(
                text(
                    "INSERT INTO drops (name, total_seats, mode, status, entry_start, entry_end) "
                    "VALUES ('t', :n, :m, :s, :a, :b) RETURNING id"
                ),
                {"n": seats, "m": mode, "s": status, "a": datetime.now(UTC) - timedelta(hours=1),
                 "b": datetime.now(UTC) + timedelta(hours=1)},
            )
        ).scalar_one()
        await db.execute(
            text("INSERT INTO seats (drop_id, seat_number) SELECT :d, n FROM generate_series(1, :n) n"),
            {"d": drop_id, "n": seats},
        )
        await db.commit()
    return drop_id


async def make_users(n: int, drop_id: int | None = None) -> list[dict]:
    """Bulk-create users with sessions (and optionally entries). Returns auth headers + ids."""
    async with SessionLocal() as db:
        expires = datetime.now(UTC) + timedelta(hours=1)
        users = []
        for i in range(n):
            users.append({"p": f"ph{i}-{os.urandom(4).hex()}", "pub": f"u_{os.urandom(5).hex()}", "tok": f"tok{i}-{os.urandom(8).hex()}"})
        ids = (
            await db.execute(
                text("INSERT INTO users (public_id, phone_hash) SELECT * FROM unnest(CAST(:pub AS text[]), CAST(:ph AS text[])) RETURNING id"),
                {"pub": [u["pub"] for u in users], "ph": [u["p"] for u in users]},
            )
        ).scalars().all()
        for u, uid in zip(users, ids):
            u["id"] = uid
        await db.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) SELECT * FROM unnest(CAST(:u AS bigint[]), CAST(:t AS text[]), CAST(:e AS timestamptz[]))"),
            {"u": ids, "t": [keyed_hash("sess:" + u["tok"]) for u in users], "e": [expires] * n},
        )
        if drop_id is not None:
            # distinct, increasing created_at so FIFO order == list order
            base = datetime.now(UTC) - timedelta(minutes=30)
            await db.execute(
                text("INSERT INTO entries (drop_id, user_id, created_at) SELECT :d, u, t FROM unnest(CAST(:u AS bigint[]), CAST(:t AS timestamptz[])) AS x(u, t)"),
                {"d": drop_id, "u": ids, "t": [base + timedelta(milliseconds=i) for i in range(n)]},
            )
        await db.commit()
    for u in users:
        u["headers"] = {"Authorization": f"Bearer {u['tok']}"}
    return users
