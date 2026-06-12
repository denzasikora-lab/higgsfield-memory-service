from __future__ import annotations

from fastapi import FastAPI

from src.api.routes import memory_router, public_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Higgsfield Memory Service",
        version="0.1.0",
        description="Contract-first scaffold for an AI-agent memory service.",
    )
    app.include_router(public_router)
    app.include_router(memory_router)
    return app


app = create_app()
