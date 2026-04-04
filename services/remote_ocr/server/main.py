"""FastAPI сервер для OCR — embedded режим (без Celery/Redis).

Job manager встроен в процесс FastAPI, OCR задачи выполняются
в отдельных multiprocessing.Process для изоляции памяти.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .logging_config import get_logger, setup_logging
from .routes.jobs import router as jobs_router
from .routes.storage import router as storage_router
from .routes.tree import router as tree_router
from .settings import settings
from .storage import init_db

# Инициализация логирования при импорте модуля
setup_logging()

_logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: инициализация БД, job manager, фоновые задачи."""
    _logger.info(
        "Server starting with configuration",
        extra={
            "event": "server_startup",
            "config": {
                "max_concurrent_jobs": settings.max_concurrent_jobs,
                "ocr_threads_per_job": settings.ocr_threads_per_job,
                "max_global_ocr_requests": settings.max_global_ocr_requests,
                "task_soft_timeout": settings.task_soft_timeout,
                "task_hard_timeout": settings.task_hard_timeout,
                "max_queue_size": settings.max_queue_size,
                "chandra_base_url": settings.chandra_base_url,
                "qwen_base_url": settings.qwen_base_url or "(fallback → chandra)",
            },
        },
    )

    init_db()

    # Инициализация embedded job manager с WebSocket callback
    from .embedded_job_manager_singleton import init_job_manager
    from .routes.websocket import on_job_event_sync

    manager = init_job_manager(on_job_event=on_job_event_sync)

    # Восстановление queued задач из Supabase после рестарта
    reloaded = manager.reload_queued_jobs()
    if reloaded:
        _logger.info(f"Reloaded {reloaded} jobs from Supabase after restart")

    # Фоновый poll loop для job manager (замена Celery polling)
    async def _poll_loop():
        from .storage import update_job_status

        while True:
            try:
                messages = manager.poll()
                for msg in messages:
                    job_id = msg.get("job_id", "")
                    msg_type = msg.get("type", "")
                    if msg_type == "progress":
                        progress = msg.get("progress", 0)
                        status_message = msg.get("message", "")
                        update_job_status(
                            job_id, "processing",
                            progress=progress,
                            status_message=status_message,
                        )
                    elif msg_type == "error":
                        error_msg = msg.get("message", "Unknown error")
                        update_job_status(
                            job_id, "error",
                            error_message=error_msg,
                            status_message="❌ Ошибка обработки",
                        )
                    # "done" — finalize() уже обновил статус в Supabase
            except Exception:
                _logger.exception("Poll loop error")
            await asyncio.sleep(0.5)

    poll_task = asyncio.create_task(_poll_loop())

    # Запуск фонового zombie detector
    from .zombie_detector import zombie_detector_loop

    zombie_task = asyncio.create_task(zombie_detector_loop())

    # Запуск фонового LM Studio unload checker
    async def _unload_checker_loop():
        from .lmstudio_lifecycle import check_and_unload_models

        while True:
            try:
                await asyncio.sleep(30)
                await asyncio.to_thread(check_and_unload_models)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _logger.warning(f"Unload checker error: {exc}")
                await asyncio.sleep(60)

    unload_task = asyncio.create_task(_unload_checker_loop())

    yield

    # Остановка фоновых задач
    for task in (poll_task, zombie_task, unload_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Остановка job manager
    manager.shutdown()

    _logger.info("Server shutting down", extra={"event": "server_shutdown"})


app = FastAPI(title="rd-ocr-server", lifespan=lifespan)

# CORS для web-клиента
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://llm.fvds.ru",
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Middleware для логирования времени выполнения всех запросов."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        method = request.method
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"

        try:
            response = await call_next(request)
            duration_ms = int((time.time() - start_time) * 1000)

            log_data = {
                "event": "http_request",
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "client_ip": client_ip,
            }

            if response.status_code >= 400:
                _logger.warning(f"{method} {path} -> {response.status_code}", extra=log_data)
            else:
                _logger.info(f"{method} {path} -> {response.status_code}", extra=log_data)

            return response

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            _logger.exception(
                f"Request failed: {method} {path}",
                extra={
                    "event": "http_request_error",
                    "method": method,
                    "path": path,
                    "duration_ms": duration_ms,
                    "client_ip": client_ip,
                    "exception_type": type(e).__name__,
                },
            )
            raise


app.add_middleware(RequestTimingMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    _logger.error(
        f"Validation error on {request.method} {request.url.path}: {exc.errors()}"
    )
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


@app.get("/health")
def health() -> dict:
    """Health check"""
    return {"ok": True}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    """Readiness check — проверяет Supabase, OCR API и job manager."""
    checks: dict[str, bool] = {"supabase": False, "config": False, "job_manager": False}

    # Supabase: простой запрос
    try:
        from .storage_client import get_client

        client = get_client()
        client.table("jobs").select("id").limit(1).execute()
        checks["supabase"] = True
    except Exception:
        _logger.warning("Readiness: Supabase check failed", exc_info=True)

    # Config: chandra_base_url обязателен
    checks["config"] = bool(settings.chandra_base_url)

    # Job manager: инициализирован
    try:
        from .embedded_job_manager_singleton import get_job_manager
        manager = get_job_manager()
        checks["job_manager"] = True
    except Exception:
        pass

    # Provider health (информационное, НЕ влияет на ready)
    providers: dict = {}

    for engine_name in ("chandra", "qwen"):
        url = getattr(settings, f"{engine_name}_base_url", None)
        if not url and engine_name == "qwen":
            url = settings.chandra_base_url
        if url:
            import httpx

            openai_ok = False
            native_ok = False
            try:
                resp = httpx.get(f"{url}/v1/models", timeout=5)
                openai_ok = resp.status_code == 200
            except Exception:
                pass
            try:
                resp = httpx.get(f"{url}/api/v1/models", timeout=5)
                native_ok = resp.status_code == 200
            except Exception:
                pass
            providers[engine_name] = {
                "configured": True,
                "openai_reachable": openai_ok,
                "native_reachable": native_ok,
            }

    ready = all(checks.values())
    result = {"ready": ready, "checks": checks}
    if providers:
        result["providers"] = providers
    return JSONResponse(
        status_code=200 if ready else 503,
        content=result,
    )


@app.get("/queue")
def queue_status() -> dict:
    """Queue status для мониторинга backpressure"""
    from .embedded_job_manager_singleton import get_job_manager

    manager = get_job_manager()
    return manager.get_status()


# Подключаем роутеры
app.include_router(jobs_router)
app.include_router(tree_router)
app.include_router(storage_router)

from .routes.annotations import router as annotations_router
from .routes.pdf_info import router as pdf_info_router
from .routes.websocket import router as websocket_router

app.include_router(annotations_router)
app.include_router(pdf_info_router)
app.include_router(websocket_router)


# Static SPA serving: web/dist если существует
import os
from pathlib import Path

_web_dist = Path(__file__).parent.parent.parent.parent / "web" / "dist"
if not _web_dist.exists():
    # Fallback: в Docker образе может быть в /app/web/dist
    _web_dist = Path("/app/web/dist")

if _web_dist.exists():
    from fastapi.staticfiles import StaticFiles

    # SPA fallback: все неизвестные пути -> index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA fallback — отдаёт index.html для клиентского роутинга."""
        from fastapi.responses import FileResponse

        file_path = _web_dist / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_web_dist / "index.html")

    # Статика (CSS, JS, assets)
    app.mount("/assets", StaticFiles(directory=str(_web_dist / "assets")), name="assets")

    _logger.info(f"Serving SPA from {_web_dist}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
