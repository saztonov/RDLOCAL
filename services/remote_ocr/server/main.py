"""FastAPI сервер для удалённого OCR (все данные через Supabase + R2)"""
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
    """Lifecycle: инициализация БД (воркер запускается отдельно через Celery)"""
    # Логируем конфигурацию при старте (без секретов)
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
                "has_openrouter_key": bool(settings.openrouter_api_key),
                "has_datalab_key": bool(settings.datalab_api_key),
            },
        },
    )

    init_db()

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
    zombie_task.cancel()
    unload_task.cancel()
    for task in (zombie_task, unload_task):
        try:
            await task
        except asyncio.CancelledError:
            pass

    _logger.info("Server shutting down", extra={"event": "server_shutdown"})


app = FastAPI(title="rd-remote-ocr", lifespan=lifespan)


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
    """Readiness check — проверяет Redis, Supabase и наличие OCR API-ключей."""
    checks: dict[str, bool] = {"redis": False, "supabase": False, "config": False}

    # Redis: ping через Celery broker
    try:
        from .celery_app import celery_app

        conn = celery_app.connection()
        conn.ensure_connection(max_retries=1, timeout=3)
        conn.close()
        checks["redis"] = True
    except Exception:
        _logger.warning("Readiness: Redis ping failed", exc_info=True)

    # Supabase: простой запрос
    try:
        from .storage_client import get_client

        client = get_client()
        client.table("jobs").select("id").limit(1).execute()
        checks["supabase"] = True
    except Exception:
        _logger.warning("Readiness: Supabase check failed", exc_info=True)

    # Config: хотя бы один OCR ключ
    checks["config"] = bool(settings.openrouter_api_key or settings.datalab_api_key)

    # Provider health (информационное, НЕ влияет на ready)
    providers: dict = {}
    if settings.datalab_api_key:
        try:
            import httpx

            resp = httpx.get("https://www.datalab.to/api/v1/status", timeout=5)
            providers["datalab"] = {"configured": True, "reachable": resp.status_code == 200}
        except Exception:
            providers["datalab"] = {"configured": True, "reachable": False}

    for engine_name in ("chandra", "qwen"):
        url = getattr(settings, f"{engine_name}_base_url", None)
        if url:
            try:
                import httpx

                resp = httpx.get(f"{url}/v1/models", timeout=5)
                providers[engine_name] = {"configured": True, "reachable": resp.status_code == 200}
            except Exception:
                providers[engine_name] = {"configured": True, "reachable": False}

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
    from .queue_checker import check_queue_capacity

    can_accept, current, max_size = check_queue_capacity()
    return {"can_accept": can_accept, "size": current, "max": max_size}


# Подключаем роутеры
app.include_router(jobs_router)
app.include_router(tree_router)
app.include_router(storage_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
