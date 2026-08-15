"""HTTP layer. Thin by design — it validates, delegates, and shapes the response.

Every route below is a few lines because the work lives in `services/` and `domain/`; the ordering
logic in particular is a plain unit-testable module with no web framework anywhere near it.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.adapters.factory import build_planner, provider_status
from app.config import PLAN_CACHE_SIZE, PROMPT_VERSION, STATIC_DIR
from app.domain.models import PlanResponse
from app.observability import ProgressTracker, configure_logging
from app.ports.planner import PlannerError
from app.services.pdf_validator import PdfRejected
from app.services.plan_store import InMemoryPlanStore
from app.services.planning_service import PlanningService

configure_logging()
logger = logging.getLogger("planner")

app = FastAPI(
    title="AI Engineering Planner",
    version="1.0.0",
    description="Turns a PRD (PDF) into an ordered engineering plan that AI coding agents can execute.",
)

store = InMemoryPlanStore(capacity=PLAN_CACHE_SIZE)
service = PlanningService(store)
progress = ProgressTracker()


@app.exception_handler(PdfRejected)
async def _pdf_rejected(_: Request, exc: PdfRejected) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


@app.exception_handler(PlannerError)
async def _planner_error(_: Request, exc: PlannerError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"status": "ok", "prompt_version": PROMPT_VERSION, **provider_status()}


@app.post("/api/plan", response_model=PlanResponse)
def create_plan(
    response: Response,
    file: UploadFile = File(..., description="The PRD, as a PDF"),
    refresh: bool = Query(False, description="Bypass the cache and regenerate"),
    provider: str | None = Query(None, description="Override the provider for this call"),
    trace: str | None = Query(None, description="Client-generated id to poll /api/progress with"),
) -> PlanResponse:
    raw = file.file.read()
    filename = file.filename or "upload.pdf"

    # One event, two sinks — the terminal and the tracker the browser polls. They are fed from the
    # same callback so what the UI shows and what the log says cannot disagree.
    def on_stage(stage: str, detail: str) -> None:
        logger.info("%-14s %s", stage, detail)
        if trace:
            progress.stage(trace, stage, detail)

    if trace:
        progress.start(trace)
    logger.info("%-14s %s (%d KB)", "received", filename, len(raw) // 1024)

    try:
        plan = service.create_plan(
            raw=raw,
            filename=filename,
            planner=build_planner(provider),
            prompt_version=PROMPT_VERSION,
            refresh=refresh,
            on_stage=on_stage,
        )
    except (PdfRejected, PlannerError) as exc:
        # Mark the trace failed before the exception handlers turn it into JSON, otherwise a poller
        # sits on the last successful stage forever.
        logger.warning("%-14s %s", "failed", exc.message)
        if trace:
            progress.finish(trace, error=exc.message)
        raise

    if trace:
        progress.finish(trace)
    response.headers["X-Plan-Cache"] = plan.meta.cache.upper()
    return plan


@app.get("/api/progress/{trace_id}")
def get_progress(trace_id: str) -> JSONResponse:
    """What stage that upload is in right now. Polled by the UI once a second."""
    record = progress.get(trace_id)
    if record is None:
        return JSONResponse(status_code=404, content={"stage": "unknown", "trace_id": trace_id})
    return JSONResponse(content=record)


@app.get("/api/plans")
def list_plans() -> dict[str, object]:
    return {
        "plans": [
            {
                "plan_id": plan.plan_id,
                "source_filename": plan.meta.source_filename,
                "task_count": plan.meta.task_count,
                "created_at": plan.meta.cached_at,
                "provider": plan.meta.provider,
            }
            for plan in store.list()
        ]
    }


@app.get("/api/plans/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: str) -> PlanResponse:
    plan = store.peek(plan_id)
    if plan is None:
        raise PlannerError(f"No plan with id {plan_id!r} is in memory.", status_code=404)
    return plan


@app.get("/api/plans/{plan_id}/markdown", response_class=PlainTextResponse)
def get_plan_markdown(plan_id: str) -> Response:
    plan = store.peek(plan_id)
    if plan is None:
        raise PlannerError(f"No plan with id {plan_id!r} is in memory.", status_code=404)
    return PlainTextResponse(
        plan.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="plan-{plan_id}.md"'},
    )


@app.get("/api/cache")
def cache_stats() -> dict[str, object]:
    return store.stats()


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
