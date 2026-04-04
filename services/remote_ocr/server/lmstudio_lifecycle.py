"""Управление lifecycle моделей LM Studio — in-process координация.

Заменяет Redis-based координацию на threading-based.
Используется в embedded режиме (без Celery/Redis).

Delayed unload: при remaining==0 модель остаётся загруженной
на UNLOAD_GRACE_SECONDS. Если за это время придёт новая задача —
выгрузка отменяется.

Используется threading.Lock + in-memory set вместо Redis SET.
"""
from __future__ import annotations

import threading
import time

from .logging_config import get_logger
from .settings import settings

logger = get_logger(__name__)

# Grace period перед выгрузкой модели (секунды)
UNLOAD_GRACE_SECONDS = 120

# In-process state (заменяет Redis)
_lock = threading.Lock()
_active_jobs: dict[str, set[str]] = {}  # engine -> set of job_ids
_pending_unloads: dict[str, float] = {}  # engine -> timestamp when unload was scheduled


def _active_set(engine: str) -> set[str]:
    """Получить/создать set активных задач для движка."""
    if engine not in _active_jobs:
        _active_jobs[engine] = set()
    return _active_jobs[engine]


# ── Универсальные функции ───────────────────────────────────────────

def acquire_lmstudio(engine: str, job_id: str) -> int:
    """Зарегистрировать начало задачи для LM Studio движка. Возвращает счётчик."""
    with _lock:
        jobs = _active_set(engine)
        jobs.add(job_id)
        # Отменяем pending unload — новая задача пришла
        _pending_unloads.pop(engine, None)
        count = len(jobs)
    logger.info(
        f"{engine} acquire: job={job_id[:8]}, active_tasks={count}",
        extra={"event": f"{engine}_acquire", "job_id": job_id},
    )
    return count


def release_lmstudio(engine: str, job_id: str) -> int:
    """Снять регистрацию задачи. Возвращает оставшийся счётчик."""
    with _lock:
        jobs = _active_set(engine)
        jobs.discard(job_id)
        count = len(jobs)
    logger.info(
        f"{engine} release: job={job_id[:8]}, active_tasks={count}",
        extra={"event": f"{engine}_release", "job_id": job_id},
    )
    return count


def schedule_pending_unload(engine: str) -> None:
    """Запланировать отложенную выгрузку модели."""
    with _lock:
        count = len(_active_set(engine))
        if count == 0:
            _pending_unloads[engine] = time.time()
            logger.info(
                f"{engine}: pending unload запланирован (grace={UNLOAD_GRACE_SECONDS}s)",
                extra={"event": f"{engine}_pending_unload"},
            )


def check_and_unload_models() -> None:
    """Проверить pending unloads и выгрузить модели если grace period истёк.

    Вызывается из background loop (каждые 30 сек).
    """
    for engine in ("chandra", "qwen"):
        with _lock:
            pending_ts = _pending_unloads.get(engine)
            if pending_ts is None:
                continue

            elapsed = time.time() - pending_ts
            if elapsed < UNLOAD_GRACE_SECONDS:
                continue

            count = len(_active_set(engine))
            if count > 0:
                _pending_unloads.pop(engine, None)
                continue

            # Grace period истёк, нет активных — выгружаем
            _pending_unloads.pop(engine, None)

        # Выгрузку выполняем вне lock
        _do_unload_model(engine)


def _do_unload_model(engine: str) -> None:
    """Выполнить выгрузку модели LM Studio."""
    base_url = None
    if engine == "chandra":
        base_url = getattr(settings, "chandra_base_url", None)
    elif engine == "qwen":
        base_url = getattr(settings, "qwen_base_url", None) or getattr(settings, "chandra_base_url", None)

    if not base_url:
        return

    try:
        import httpx

        resp = httpx.get(f"{base_url}/api/v1/models", timeout=10.0)
        if resp.status_code != 200:
            return

        if engine == "chandra":
            from rd_core.ocr._chandra_common import CHANDRA_MODEL_KEY
            model_key_lower = CHANDRA_MODEL_KEY.lower()
        elif engine == "qwen":
            from rd_core.ocr._qwen_common import QWEN_MODEL_KEY
            model_key_lower = QWEN_MODEL_KEY.lower()
        else:
            model_key_lower = engine

        for m in resp.json().get("models", []):
            if model_key_lower in m.get("key", "").lower():
                for inst in m.get("loaded_instances", []):
                    httpx.post(
                        f"{base_url}/api/v1/models/unload",
                        json={"instance_id": inst["id"]},
                        timeout=30.0,
                    )
                    logger.info(
                        f"{engine}: модель выгружена после grace period: {inst['id']}",
                        extra={"event": f"{engine}_delayed_unload"},
                    )
                break
    except Exception as e:
        logger.warning(f"_do_unload_model({engine}): {e}")


# ── Обратная совместимость (Chandra) ────────────────────────────────

def acquire_chandra(job_id: str) -> int:
    return acquire_lmstudio("chandra", job_id)


def release_chandra(job_id: str) -> int:
    return release_lmstudio("chandra", job_id)


def acquire_qwen(job_id: str) -> int:
    return acquire_lmstudio("qwen", job_id)


def release_qwen(job_id: str) -> int:
    return release_lmstudio("qwen", job_id)
