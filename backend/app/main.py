import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .audio import MAX_BYTES
from .audio_routes import router as audio_router
from .auth import LoginThrottle, WorkspaceAccess, protect_api, router as auth_router
from .commitment_routes import router as commitment_router
from .database import Base, database_path, open_database, upgrade_promise_dates
from .ledger import backfill_ledger
from .insight_routes import router as insight_router
from .lifecycle_routes import router as lifecycle_router
from .memory_routes import router as memory_router
from .notification_routes import router as notification_router
from .notifications import start_scheduler
from .routes import router


def create_app(path: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.access = WorkspaceAccess.configured()
        app.state.login_throttle = LoginThrottle()
        engine = open_database(path or database_path())
        try:
            Base.metadata.create_all(engine)
            upgrade_promise_dates(engine)
            backfill_ledger(engine)
            app.state.engine = engine
            scheduler = start_scheduler(engine)
            try:
                yield
            finally:
                scheduler.shutdown()
        finally:
            engine.dispose()

    private = os.getenv("PROMISE_KEEPER_AUTH_REQUIRED") == "1" or bool(os.getenv("PROMISE_KEEPER_PASSWORD"))
    app = FastAPI(title="Promise Keeper", lifespan=lifespan,
                  openapi_url=None if private else "/openapi.json")
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(audio_router)
    app.include_router(commitment_router)
    app.include_router(memory_router)
    app.include_router(lifecycle_router)
    app.include_router(notification_router)
    app.include_router(insight_router)

    @app.middleware("http")
    async def limit_audio_upload(request, call_next):
        if request.method == "POST" and request.url.path.rstrip("/").endswith("/audio-transcript"):
            limit = MAX_BYTES + 65536
            length = request.headers.get("content-length", "")
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                return JSONResponse({"detail": "A valid upload size is required."}, status_code=411)
            if int(length) > limit:
                return JSONResponse({"detail": "Choose a recording up to 100 MB."}, status_code=413)
        return await call_next(request)

    app.middleware("http")(protect_api)
    if os.getenv("PROMISE_KEEPER_SERVE_FRONTEND") == "1":
        frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
