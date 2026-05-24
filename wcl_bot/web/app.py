"""FastAPI app exposing the matcher / extractor / note formatter over HTTP.

Local-only: no auth, no rate limiting. Designed to be served on the user's
Proxmox LAN behind whatever reverse proxy they want (or directly).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from wcl_bot.web.routes import router

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Lazy WCLClient creation on first request to avoid touching credentials
    # at import time. State lives on app.state and is reused across requests
    # so the underlying httpx connection pool is shared.
    from wcl_bot.wcl import WCLClient
    load_dotenv()
    app.state.wcl = WCLClient()
    log.info("WCL client initialized.")
    try:
        yield
    finally:
        await app.state.wcl.close()
        log.info("WCL client closed.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="WCL Healer CD Note Builder",
        description="Discover matching kills, extract CD timings, emit raid notes.",
        lifespan=_lifespan,
    )
    app.include_router(router, prefix="/api")
    if STATIC_DIR.exists():
        # html=True makes / serve index.html automatically.
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
