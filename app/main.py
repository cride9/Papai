"""
app/main.py — FastAPI application factory.

Assembles all routers, middleware, and the startup/shutdown lifespan.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.chroma_client import init_chroma
from app.services.auth_service import bootstrap_admin

# Routers
from app.routers import auth, chat, admin, websocket, documents

log = logging.getLogger(__name__)

# ── Logging setup ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB tables, ChromaDB, bootstrap admin. Shutdown: cleanup."""
    log.info("=" * 60)
    log.info("Starting Pápai Parts Backend...")
    log.info("=" * 60)

    # Initialize database tables
    init_db()
    log.info("Database initialized.")

    # Bootstrap admin user if needed
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        bootstrap_admin(db)
    finally:
        db.close()

    # Initialize ChromaDB
    init_chroma()

    log.info(f"Server ready at http://{settings.HOST}:{settings.PORT}")
    yield

    log.info("Shutting down...")


# ── App factory ───────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Pápai Parts — Katalógus Asszisztens",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(admin.router)
    app.include_router(websocket.router)
    app.include_router(documents.router)

    # Static files (SPA frontend)
    import os as _os
    frontend_dist = _os.path.join(_os.path.dirname(__file__), "..", "frontend", "dist")
    if _os.path.exists(frontend_dist):
        app.mount("/assets", StaticFiles(directory=_os.path.join(frontend_dist, "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """Serve the SPA — fallback to index.html for client-side routing."""
            file_path = _os.path.join(frontend_dist, full_path)
            if full_path and _os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(_os.path.join(frontend_dist, "index.html"))
    else:
        # No built SPA — serve the raw index.html from frontend/
        frontend_raw = _os.path.join(_os.path.dirname(__file__), "..", "frontend")
        if _os.path.exists(frontend_raw):
            @app.get("/")
            async def serve_index():
                return FileResponse(_os.path.join(frontend_raw, "index.html"))

    return app


# Module-level app instance for uvicorn
app = create_app()
