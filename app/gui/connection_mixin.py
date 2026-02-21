"""Миксин управления соединением для MainWindow."""

import logging

logger = logging.getLogger(__name__)


class ConnectionMixin:
    """Управление сетевым соединением, офлайн-режимом и синхронизацией."""

    def _setup_connection_manager(self):
        """Инициализировать менеджер соединения"""
        from app.gui.connection_manager import ConnectionManager

        self.connection_manager = ConnectionManager(self)

        # Устанавливаем callback для проверки соединения
        def check_connection() -> bool:
            """Проверить доступность интернета и сервера"""
            import socket

            import httpx

            # 1. Быстрая проверка через Remote OCR сервер
            try:
                if self.remote_ocr_panel:
                    client = self.remote_ocr_panel._get_client()
                    if client and client.health():
                        return True
            except Exception:
                pass

            # 2. Fallback: проверка базового интернета через DNS
            try:
                socket.create_connection(("8.8.8.8", 53), timeout=3)
                return True
            except (socket.timeout, socket.error, OSError):
                pass

            # 3. Fallback: проверка через HTTP
            try:
                with httpx.Client(timeout=3) as client:
                    response = client.get("https://www.google.com/generate_204")
                    return response.status_code == 204
            except Exception:
                pass

            return False

        self.connection_manager.set_check_callback(check_connection)

        # Подключаем сигналы
        self.connection_manager.connection_lost.connect(self._on_connection_lost)
        self.connection_manager.connection_restored.connect(self._on_connection_restored)
        self.connection_manager.status_changed.connect(self._on_connection_status_changed)

        # Запускаем мониторинг
        self.connection_manager.start_monitoring()

    def _on_connection_lost(self):
        """Обработчик потери соединения (вызывается только при переходе из CONNECTED)"""
        from app.gui.toast import show_toast

        logger.warning("Соединение потеряно")
        show_toast(
            self,
            "⚠️ Работа в офлайн режиме. Изменения будут синхронизированы при восстановлении.",
            duration=5000,
        )

    def _on_connection_restored(self):
        """Обработчик восстановления соединения"""
        from app.gui.sync_queue import get_sync_queue
        from app.gui.toast import show_toast

        logger.info("Соединение восстановлено")
        queue = get_sync_queue()
        pending_count = queue.size()

        if pending_count > 0:
            show_toast(
                self,
                f"✅ Онлайн. Синхронизация {pending_count} изменений...",
                duration=3000,
            )
        else:
            show_toast(self, "✅ Онлайн", duration=2000)

        # Запускаем синхронизацию отложенных операций
        self._sync_pending_operations()

    def _on_connection_status_changed(self, status):
        """Обработчик изменения статуса соединения"""
        from app.gui.connection_manager import ConnectionStatus

        if status == ConnectionStatus.CHECKING:
            self._connection_status_label.setText("⚪ Проверка...")
            self._connection_status_label.setStyleSheet("color: #888; font-size: 9pt;")
            self._connection_status_label.setToolTip("Проверка подключения...")
        elif status == ConnectionStatus.RECONNECTING:
            self._connection_status_label.setText("🟡 Переподключение...")
            self._connection_status_label.setStyleSheet(
                "color: #ff9800; font-size: 9pt; font-weight: bold;"
            )
            self._connection_status_label.setToolTip("Попытка переподключения...")
        elif status == ConnectionStatus.CONNECTED:
            self._connection_status_label.setText("🟢 Онлайн")
            self._connection_status_label.setStyleSheet(
                "color: #4caf50; font-size: 9pt; font-weight: bold;"
            )
            self._connection_status_label.setToolTip("Подключено к серверу")
        elif status == ConnectionStatus.DISCONNECTED:
            self._connection_status_label.setText("🔴 Офлайн")
            self._connection_status_label.setStyleSheet(
                "color: #f44336; font-size: 9pt; font-weight: bold;"
            )
            self._connection_status_label.setToolTip(
                "Нет подключения. Работа в офлайн режиме."
            )

    def _update_sync_queue_indicator(self):
        """Обновить индикатор очереди синхронизации"""
        from app.gui.sync_queue import get_sync_queue

        queue = get_sync_queue()
        queue_size = queue.size()

        if queue_size > 0:
            self._sync_queue_label.setText(f"📤 {queue_size}")
            self._sync_queue_label.setStyleSheet(
                "color: #ff9800; font-size: 9pt; font-weight: bold;"
            )
            self._sync_queue_label.setToolTip(
                f"{queue_size} операций ожидают синхронизации"
            )
            self._sync_queue_label.show()
        else:
            self._sync_queue_label.hide()

    def _sync_pending_operations(self):
        """Синхронизировать отложенные операции"""
        from app.gui.sync_queue import get_sync_queue

        queue = get_sync_queue()
        if queue.is_empty():
            return

        pending = queue.get_pending_operations()
        logger.info(f"Синхронизация {len(pending)} отложенных операций...")

        # Синхронизируем операции в фоновом потоке
        from concurrent.futures import ThreadPoolExecutor

        def sync_operation(operation):
            try:
                from pathlib import Path

                from app.gui.sync_queue import SyncOperationType
                from rd_core.r2_storage import R2Storage

                if operation.type == SyncOperationType.SAVE_ANNOTATION:
                    # Сохранение аннотации в Supabase
                    if not operation.node_id or not operation.data:
                        queue.remove_operation(operation.id)
                        return

                    from app.tree_client import TreeClient

                    client = TreeClient()
                    ann_data = operation.data.get("annotation_data", {})
                    fmt_ver = operation.data.get("format_version", 2)
                    success = client.save_annotation(
                        operation.node_id, ann_data, fmt_ver
                    )
                    if success:
                        logger.info(
                            f"Аннотация синхронизирована в Supabase: {operation.node_id}"
                        )
                        # Обновляем флаг has_annotation
                        try:
                            node = client.get_node(operation.node_id)
                            if node and not node.attributes.get("has_annotation"):
                                attrs = node.attributes.copy()
                                attrs["has_annotation"] = True
                                client.update_node(
                                    operation.node_id, attributes=attrs
                                )
                        except Exception:
                            pass
                        queue.remove_operation(operation.id)
                    else:
                        queue.mark_failed(
                            operation.id,
                            "Не удалось сохранить аннотацию в Supabase",
                        )

                elif operation.type == SyncOperationType.UPLOAD_FILE:
                    r2 = R2Storage()
                    local_path = operation.local_path
                    r2_key = operation.r2_key
                    content_type = (
                        operation.data.get("content_type") if operation.data else None
                    )

                    if not Path(local_path).exists():
                        logger.warning(
                            f"Файл не найден для синхронизации: {local_path}"
                        )
                        queue.remove_operation(operation.id)
                        return

                    if r2.upload_file(local_path, r2_key, content_type):
                        logger.info(f"Операция синхронизирована: {operation.id}")
                        queue.remove_operation(operation.id)

                        # Удаляем временный файл если это был временный файл
                        if operation.data and operation.data.get("is_temp"):
                            try:
                                Path(local_path).unlink()
                            except Exception:
                                pass
                    else:
                        queue.mark_failed(
                            operation.id, "Не удалось загрузить файл"
                        )

            except Exception as e:
                logger.error(f"Ошибка синхронизации операции {operation.id}: {e}")
                queue.mark_failed(operation.id, str(e))

        # Синхронизируем операции параллельно
        with ThreadPoolExecutor(max_workers=3) as executor:
            executor.map(sync_operation, pending)

