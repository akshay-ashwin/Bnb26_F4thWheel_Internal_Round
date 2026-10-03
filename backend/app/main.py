from fastapi import FastAPI

from app.api.fair_routes import admin as fair_admin_router
from app.api.fair_routes import router as fair_router
from app.api.routes import router
from app.utils.errors import install_error_handlers

app = FastAPI(title="Fair Drop API")
install_error_handlers(app)
app.include_router(router)
app.include_router(fair_router)
app.include_router(fair_admin_router)
