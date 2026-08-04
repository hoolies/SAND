"""FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sand.api.errors import error_detail
from sand.api.routes.charts import router as charts_router
from sand.api.routes.chat import router as chat_router
from sand.api.routes.datasets import router as datasets_router
from sand.api.routes.export import router as export_router
from sand.api.routes.join import router as join_router
from sand.api.routes.query import router as query_router
from sand.core.config import get_settings
from sand.db.pool import close_all
from sand.llm.openai_compat import OpenAICompatClient

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    close_all()


app = FastAPI(
    title="SAND",
    description=(
        "Spreadsheets Are Not Databases. "
        "Error responses use detail={code, message, ...}. "
        "Common statuses: 400 bad_request, 401 unauthorized, 404 not_found, 409 conflict, "
        "413 limit_exceeded, 423 locked, 502 llm_upstream, 503 llm_not_configured|llm_unreachable, "
        "504 timeout, 410 deprecated."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(datasets_router, prefix="/datasets", tags=["datasets"])
app.include_router(query_router, prefix="/query", tags=["query"])
app.include_router(join_router, prefix="/query", tags=["join"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(charts_router, prefix="/charts", tags=["charts"])
app.include_router(export_router, prefix="/export", tags=["export"])

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": error_detail("validation_error", str(exc.errors()))},
    )


@app.middleware("http")
async def optional_api_token(request: Request, call_next):
    settings = get_settings()
    token = (settings.api_token or "").strip()
    if not token:
        return await call_next(request)
    if request.url.path in {"/", "/health"} or request.url.path.startswith("/static"):
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    header_token = request.headers.get("X-SAND-Token", "").strip()
    if bearer != token and header_token != token:
        return JSONResponse(
            status_code=401,
            content={"detail": error_detail("unauthorized", "Missing or invalid API token")},
        )
    return await call_next(request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


def _probe_llm(settings) -> bool | None:
    """Return True/False if configured, else None when not configured."""
    client = OpenAICompatClient(settings)
    if not client.is_configured:
        return None
    url = settings.llm_base_url.rstrip("/") + "/models"
    try:
        with httpx.Client(timeout=1.5) as http:
            resp = http.get(
                url,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {},
            )
            return resp.status_code < 500
    except Exception:
        return False


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    llm = OpenAICompatClient(settings)
    reachable = _probe_llm(settings)
    return {
        "status": "ok",
        "llm_configured": llm.is_configured,
        "llm_reachable": reachable,
        "llm_model": settings.llm_model if llm.is_configured else None,
        "auth_required": bool(settings.api_token),
    }
