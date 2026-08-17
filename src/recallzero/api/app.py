from __future__ import annotations

from importlib.resources import files

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from recallzero import __version__
from recallzero.api.routes import router
from recallzero.config import Settings, get_settings
from recallzero.pipeline import build_pipeline


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="RecallZero API",
        version=__version__,
        description="Evidence-grounded vehicle complaint clustering, risk ranking, and recall backtesting.",
    )
    app.state.settings = settings
    app.state.pipeline = build_pipeline(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> HTMLResponse:
        path = files("recallzero.web.static").joinpath("index.html")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    return app


app = create_app()
