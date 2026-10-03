from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, current_auth, require_admin
from app.db.session import get_db
from app.schemas.drops import DropOut
from app.schemas.fair import (
    AdmissionTokenOut, CommitOut, CreateDropIn, DemoUsersIn, DemoUsersOut, DrawOut, FairnessOut, FreezeOut,
    FrozenEntriesOut, ResultsOut, RevealOut, TransitionOut,
)
from app.services import claim as claim_svc
from app.services import drops as drops_svc
from app.services import fair as fair_svc

router = APIRouter()
admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


# ---- admin: lifecycle ---------------------------------------------------------------------------------------
@admin.post("/drops", response_model=DropOut, status_code=201)
async def create_drop(body: CreateDropIn, db: AsyncSession = Depends(get_db)) -> DropOut:
    drop = await fair_svc.create_drop(db, body.name, body.total_seats, body.mode)
    return await drops_svc.drop_view(db, drop.id)


@admin.post("/drops/{drop_id}/commit", response_model=CommitOut)
async def commit_seed(drop_id: int, db: AsyncSession = Depends(get_db)) -> CommitOut:
    return await fair_svc.commit_seed(db, drop_id)


@admin.post("/drops/{drop_id}/freeze", response_model=FreezeOut)
async def freeze(drop_id: int, db: AsyncSession = Depends(get_db)) -> FreezeOut:
    return await fair_svc.freeze(db, drop_id)


@admin.post("/drops/{drop_id}/reveal", response_model=RevealOut)
async def reveal(drop_id: int, db: AsyncSession = Depends(get_db)) -> RevealOut:
    return await fair_svc.reveal(db, drop_id)


@admin.post("/drops/{drop_id}/draw", response_model=DrawOut)
async def draw(drop_id: int, db: AsyncSession = Depends(get_db)) -> DrawOut:
    return await fair_svc.run_draw(db, drop_id)


@admin.post("/drops/{drop_id}/open-claims", response_model=TransitionOut)
async def open_claims(drop_id: int, db: AsyncSession = Depends(get_db)) -> TransitionOut:
    return await fair_svc.open_claims(db, drop_id)


@admin.post("/drops/{drop_id}/close", response_model=TransitionOut)
async def close(drop_id: int, db: AsyncSession = Depends(get_db)) -> TransitionOut:
    return await fair_svc.close(db, drop_id)


@admin.post("/demo/users", response_model=DemoUsersOut, status_code=201)
async def demo_users(body: DemoUsersIn, db: AsyncSession = Depends(get_db)) -> DemoUsersOut:
    return await fair_svc.demo_users(db, body.count, body.drop_id, body.enter)


# ---- public verification ------------------------------------------------------------------------------------
@router.get("/api/drops/{drop_id}/fairness", response_model=FairnessOut)
async def fairness(drop_id: int, db: AsyncSession = Depends(get_db)) -> FairnessOut:
    return await fair_svc.fairness(db, drop_id)


@router.get("/api/drops/{drop_id}/fairness/entries", response_model=FrozenEntriesOut)
async def frozen_entries(
    drop_id: int, offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
) -> FrozenEntriesOut:
    return await fair_svc.frozen_entries(db, drop_id, offset, limit)


@router.get("/api/drops/{drop_id}/fairness/results", response_model=ResultsOut)
async def results(
    drop_id: int, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
) -> ResultsOut:
    return await fair_svc.results(db, drop_id, offset, limit)


# ---- winners ------------------------------------------------------------------------------------------------
@router.post("/api/drops/{drop_id}/admission-token", response_model=AdmissionTokenOut)
async def admission_token(
    drop_id: int, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_db)
) -> AdmissionTokenOut:
    return await fair_svc.issue_token(db, drop_id, auth.user, auth.session_id)
