"""WebSocket endpoints для real-time обновлений."""
from __future__ import annotations

import asyncio
import json
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["websocket"])

# Подключённые WebSocket клиенты
_clients: Set[WebSocket] = set()


async def broadcast_job_event(event: dict) -> None:
    """Отправить событие всем подключённым клиентам."""
    if not _clients:
        return
    message = json.dumps(event, ensure_ascii=False)
    disconnected = set()
    for ws in _clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    _clients -= disconnected


def on_job_event_sync(event: dict) -> None:
    """Callback из job manager (sync) — планирует broadcast в event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(broadcast_job_event(event))
    except RuntimeError:
        pass


@router.websocket("/ws/jobs")
async def websocket_jobs(websocket: WebSocket):
    """WebSocket для real-time прогресса OCR задач.

    Клиент подключается и получает push-уведомления:
    - {"type": "progress", "job_id": "...", "progress": 0.5, "message": "..."}
    - {"type": "done", "job_id": "...", "status": "done"}
    - {"type": "error", "job_id": "...", "message": "..."}
    """
    await websocket.accept()
    _clients.add(websocket)
    logger.info(f"WebSocket client connected (total: {len(_clients)})")

    try:
        while True:
            # Держим соединение открытым, ожидая сообщения от клиента (ping/pong)
            data = await websocket.receive_text()
            # Клиент может отправить ping
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _clients.discard(websocket)
        logger.info(f"WebSocket client disconnected (total: {len(_clients)})")
