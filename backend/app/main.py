"""SteadyOps control-plane entry point.

    cd backend && python -m uvicorn app.main:app --port 8010
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import Settings
from .runtime import build_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(settings: Settings | None = None, runtime=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        rt = runtime or build_runtime(settings)
        app.state.rt = rt
        if settings.run_loop:
            rt.orchestrator.start()
        yield
        rt.orchestrator.stop()

    app = FastAPI(title="SteadyOps", version="1.0.0", lifespan=lifespan,
                  description="Safety-first incident response and automated recovery")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])

    @app.exception_handler(Exception)
    async def safe_errors(request, exc):  # never leak stack traces or config
        logging.getLogger("steadyops.api").exception("unhandled API error")
        return JSONResponse({"detail": "internal error"}, status_code=500)

    app.include_router(router)

    dist = settings.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
