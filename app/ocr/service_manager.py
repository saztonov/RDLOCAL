"""Менеджер Docker-контейнера local-ocr.

Пр��веряет/запускает контейнер, выполняет health-check,
предоставляет base_url для RemoteOCRClient.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 18100
_HEALTH_RETRIES = 20
_HEALTH_INTERVAL = 0.5  # секунд между попы��ками
_COMPOSE_PROJECT_DIR = Path(__file__).parent.parent.parent  # корень проекта


class LocalOcrServiceManager:
    """Управление Docker-контейнером local-ocr."""

    def __init__(self, port: int = _DEFAULT_PORT, compose_dir: Path | None = None):
        self._port = port
        self._compose_dir = compose_dir or _COMPOSE_PROJECT_DIR
        self._base_url = f"http://127.0.0.1:{port}"
        self._running = False

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def is_running(self) -> bool:
        return self._running

    def ensure_running(self) -> bool:
        """Убедиться что контейнер жив. Запустить если нет.

        Returns:
            True если сервис дост��пен.
        """
        # Сначала проверяем — может уже запущен
        if self._health_check(retries=2):
            self._running = True
            logger.info(f"Local OCR service already running at {self._base_url}")
            return True

        # Пробуем запустить через docker compose
        if not self._docker_compose_up():
            return False

        # Ждём health
        if self._health_check(retries=_HEALTH_RETRIES):
            self._running = True
            logger.info(f"Local OCR service started at {self._base_url}")
            return True

        logger.error("Local OCR service failed to start (health check timeout)")
        return False

    def _health_check(self, retries: int = _HEALTH_RETRIES) -> bool:
        """Проверить доступность сервиса."""
        for attempt in range(retries):
            try:
                resp = httpx.get(
                    f"{self._base_url}/health",
                    timeout=2.0,
                )
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            except Exception as e:
                logger.debug(f"Health check attempt {attempt + 1}/{retries}: {e}")

            if attempt < retries - 1:
                time.sleep(_HEALTH_INTERVAL)

        return False

    def _docker_compose_up(self) -> bool:
        """Запустить docker compose up -d local-ocr."""
        try:
            result = subprocess.run(
                ["docker", "compose", "up", "-d", "local-ocr"],
                cwd=str(self._compose_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info("docker compose up -d local-ocr: OK")
                return True
            else:
                logger.error(
                    f"docker compose up failed (rc={result.returncode}):\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )
                return False
        except FileNotFoundError:
            logger.error("Docker not found. Install Docker Desktop.")
            return False
        except subprocess.TimeoutExpired:
            logger.error("docker compose up timed out (120s)")
            return False
        except Exception as e:
            logger.error(f"docker compose up error: {e}")
            return False

    def is_container_running(self) -> bool:
        """Проверить работает ли контейнер через docker ps."""
        try:
            result = subprocess.run(
                ["docker", "compose", "ps", "--status", "running", "--services"],
                cwd=str(self._compose_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            services = result.stdout.strip().splitlines()
            return "local-ocr" in services
        except Exception:
            return False
