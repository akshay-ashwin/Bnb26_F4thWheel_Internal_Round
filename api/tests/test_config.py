import pytest
from pydantic import ValidationError

from app.config import Settings

GOOD = "x" * 32


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://u:pw-pw-pw-pw@h/d",
        "phone_pepper": GOOD,
        "session_secret": GOOD,
        "token_signing_key": GOOD,
        "admin_key": GOOD,
        "sim_telemetry_key": GOOD,
        "uvicorn_workers": 4,
        "api_reload": False,
        "db_pool_max": 30,
    }
    return Settings(**(values | overrides))  # type: ignore[arg-type]


def test_defaults_are_valid_and_budget_is_130() -> None:
    assert make().db_connection_budget == 4 * 30 + 10


def test_prod_with_sim_mode_refuses_to_start() -> None:
    with pytest.raises(ValidationError, match=r"APP_ENV=prod with SIM_MODE=true"):
        make(app_env="prod", sim_mode=True)


def test_short_or_missing_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="ADMIN_KEY"):
        make(admin_key="short")
    with pytest.raises(ValidationError, match="SIM_TELEMETRY_KEY"):
        make(sim_mode=True, sim_telemetry_key="")


def test_pool_math_is_enforced() -> None:
    with pytest.raises(ValidationError, match="pool math"):
        make(uvicorn_workers=8, db_pool_max=30)
    make(uvicorn_workers=8, db_pool_max=20)  # 170 < 197


def test_reload_mode_counts_one_worker() -> None:
    assert make(api_reload=True, uvicorn_workers=8).effective_workers == 1


def test_public_summary_has_no_secrets() -> None:
    text = str(make().public_summary())
    assert GOOD not in text and "pw-pw" not in text
