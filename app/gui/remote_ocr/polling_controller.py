"""Контроллер polling для Remote OCR"""

import logging
import time

logger = logging.getLogger(__name__)


class PollingControllerMixin:
    """Миксин для управления polling задач"""

    def _refresh_jobs(self, manual: bool = False):
        """Обновить список задач"""
        if self._is_fetching:
            return

        # При множественных ошибках сначала проверяем health
        if not manual and not self._try_health_check_before_poll():
            return

        self._is_fetching = True
        self._is_manual_refresh = manual

        if manual:
            self._force_full_refresh = True
            self.status_label.setText("🔄 Загрузка...")

        self._executor.submit(self._fetch_bg)

    def _fetch_bg(self):
        """Фоновая загрузка задач (полная или дельта через единый endpoint)."""
        client = self._get_client()
        if client is None:
            self._signals.jobs_error.emit("Ошибка клиента")
            return

        force_full = getattr(self, "_force_full_refresh", False)
        use_delta = (
            self._last_server_time
            and self._jobs_cache
            and not force_full
        )

        try:
            if use_delta:
                logger.debug(f"Fetching job changes since {self._last_server_time}")
                jobs, server_time = client.list_jobs(since=self._last_server_time)
                logger.debug(f"Fetched {len(jobs)} changed jobs")

                if jobs:
                    logger.info(f"Получено {len(jobs)} изменений с сервера")

                # Обновляем кеш изменёнными задачами
                for job in jobs:
                    self._jobs_cache[job.id] = job

                if server_time:
                    self._last_server_time = server_time

                # Отправляем полный список из кеша
                all_jobs = list(self._jobs_cache.values())
                all_jobs.sort(key=lambda j: (j.priority, j.created_at))
                self._signals.jobs_loaded.emit(all_jobs, server_time or self._last_server_time or "")
            else:
                logger.debug(f"Fetching full jobs list from {client.base_url}")
                jobs, server_time = client.list_jobs(document_id=None)
                logger.debug(f"Fetched {len(jobs)} jobs, server_time={server_time}")
                self._signals.jobs_loaded.emit(jobs, server_time)

        except Exception as e:
            logger.error(f"Ошибка получения задач: {e}", exc_info=True)
            if use_delta:
                # При ошибке delta — пробуем полную загрузку при следующем poll
                self._force_full_refresh = True
            self._signals.jobs_error.emit(str(e))

    def _on_jobs_loaded(self, jobs, server_time: str = ""):
        """Слот: список задач получен"""
        self._is_fetching = False
        self._force_full_refresh = False

        # Логируем изменения статусов задач
        for job in jobs:
            cached = self._jobs_cache.get(job.id)
            if cached and cached.status != job.status:
                logger.info(
                    f"Статус задачи {job.id[:8]}... изменился: "
                    f"{cached.status} -> {job.status} (progress={job.progress:.0%})"
                )

        # При первой полной загрузке инициализируем кеш и server_time
        if self._is_manual_refresh or not self._last_server_time:
            self._jobs_cache = {j.id: j for j in jobs}
            # Используем server_time от сервера для синхронизации
            if server_time:
                self._last_server_time = server_time
            logger.debug(f"Jobs cache initialized with {len(self._jobs_cache)} jobs, server_time={self._last_server_time}")

        # Добавляем оптимистично добавленные задачи, которых ещё нет в ответе сервера
        jobs_ids = {j.id for j in jobs}
        merged_jobs = list(jobs)
        current_time = time.time()

        for job_id, (job_info, timestamp) in list(self._optimistic_jobs.items()):
            if job_id in jobs_ids:
                logger.info(
                    f"Задача {job_id[:8]}... найдена в ответе сервера, "
                    "удаляем из оптимистичного списка"
                )
                self._optimistic_jobs.pop(job_id, None)
            elif current_time - timestamp > 60:
                logger.warning(
                    f"Задача {job_id[:8]}... в оптимистичном списке более минуты, "
                    "удаляем (таймаут)"
                )
                self._optimistic_jobs.pop(job_id, None)
            else:
                logger.debug(
                    f"Задача {job_id[:8]}... ещё не на сервере, добавляем оптимистично"
                )
                merged_jobs.insert(0, job_info)

        self._update_table(merged_jobs)
        self.status_label.setText("🟢 Подключено")
        self._consecutive_errors = 0

        self._has_active_jobs = any(
            j.status in ("queued", "processing") for j in merged_jobs
        )
        new_interval = (
            self.POLL_INTERVAL_PROCESSING
            if self._has_active_jobs
            else self.POLL_INTERVAL_IDLE
        )
        if self.refresh_timer.interval() != new_interval:
            self.refresh_timer.setInterval(new_interval)

    def _on_jobs_error(self, error_msg: str):
        """Слот: ошибка загрузки списка"""
        self._is_fetching = False
        self.status_label.setText("🔴 Сервер недоступен")
        self._consecutive_errors += 1

        backoff_interval = min(
            self.POLL_INTERVAL_ERROR * (2 ** min(self._consecutive_errors - 1, 3)),
            180000,
        )
        if self.refresh_timer.interval() != backoff_interval:
            self.refresh_timer.setInterval(backoff_interval)

    def _try_health_check_before_poll(self) -> bool:
        """При множественных ошибках проверяем health перед полным poll.

        Returns:
            True если сервер доступен и можно делать poll.
        """
        if self._consecutive_errors < 3:
            return True

        client = self._get_client()
        if client is None:
            return False

        try:
            resp = client._request_with_retry("get", "/health")
            if resp.status_code == 200:
                logger.info("Health check OK, сброс backoff")
                self._consecutive_errors = 0
                self._force_full_refresh = True
                self.refresh_timer.setInterval(self.POLL_INTERVAL_PROCESSING)
                return True
        except Exception:
            pass

        return False
