# Архитектура Core Structure

## Обзор

Проект состоит из четырёх крупных частей:

- `app` — desktop-клиент на `PySide6`.
- `app/ocr` — локальный OCR runtime, запускаемый из GUI.
- `rd_core` — общая доменная и OCR-логика.
- `services/remote_ocr/server` — серверный OCR-режим на `FastAPI` + `Celery`.

## Слои и ответственность

| Подсистема | Что делает |
| --- | --- |
| `app/main.py` | Точка входа desktop-приложения |
| `app/gui/` | UI: главное окно, просмотр страниц, дерево проектов, OCR-панель |
| `app/ocr/` | `LocalOcrRunner` и `run_local_ocr()` для локального OCR |
| `app/tree_client/` | REST-клиент к Supabase для `tree_nodes`, `node_files`, `annotations` |
| `app/services.py` | Facade-слой для GUI над `R2Storage`, `TreeClient`, `AnnotationDBIO` |
| `rd_core/models/` | Базовые модели: `Block`, `Page`, `Document`, enum'ы |
| `rd_core/pdf_utils.py` | Рендеринг страниц PDF и чтение геометрии |
| `rd_core/annotation_io.py` | Сериализация, миграция и загрузка аннотаций |
| `rd_core/ocr/` | OCR-бэкенды `Chandra`, `Qwen`, `Dummy`, фабрика `create_ocr_engine()` |
| `rd_core/r2_storage.py` | Sync-клиент Cloudflare R2 |
| `services/remote_ocr/server/` | HTTP API, Celery worker, two-pass OCR, storage integration |
| `database/` | SQL-дамп схемы и экспортированные артефакты БД |

## Desktop runtime

Основной runtime для разработчика сейчас выглядит так:

1. `app/main.py` создаёт `MainWindow`.
2. `MainWindow` собирает UI из mixin'ов и виджетов:
   `PageViewer`, `BlocksTreeManager`, `ProjectTreeWidget`, `RemoteOCRPanel`.
3. Разметка живёт в моделях `Document` / `Page` / `Block`.
4. GUI работает с хранилищами через:
   `app.services`, `TreeClient`, `AnnotationDBIO`, `R2Storage`.

Ключевые виджеты:

- `MainWindow` — композиция mixin'ов и общий state container.
- `PageViewer` — просмотр страницы PDF и операции с блоками.
- `ProjectTreeWidget` — дерево проектов, документов и файлов из Supabase.
- `RemoteOCRPanel` — UI над OCR-задачами.

## Local OCR flow

Это текущий основной OCR-путь в GUI.

```text
MainWindow
  -> RemoteOCRPanel
  -> JobsController
  -> LocalOcrRunner
  -> app/ocr/local_pipeline.py
  -> rd_core OCR backends + server pdf_twopass modules
```

Что важно:

- GUI не зависит от HTTP API и Celery для основного OCR-сценария.
- `LocalOcrRunner` запускает каждую OCR-задачу в отдельном `multiprocessing.Process`.
- `app/ocr/local_pipeline.py` переиспользует серверные модули:
  `pdf_twopass`, `block_verification`, генерацию результатов и OCR-бэкенды.
- Для локального OCR в минимальной конфигурации достаточно `CHANDRA_BASE_URL` и опционально `QWEN_BASE_URL`.

## Remote OCR flow

Серверный режим остаётся полноценной частью системы.

```text
HTTP client / integration
  -> FastAPI app
  -> jobs routes
  -> Supabase + R2
  -> Celery task
  -> pdf_twopass + OCR backends
  -> result files + status updates
```

Он нужен, когда важны:

- удалённый API;
- очередь задач;
- отдельный worker runtime;
- shared storage через Supabase и R2;
- background processing вне GUI.

## Что общее у local и remote OCR

Оба режима используют общие строительные блоки:

- модели из `rd_core.models`;
- OCR-бэкенды из `rd_core.ocr`;
- two-pass извлечение и распознавание из `services/remote_ocr/server/pdf_twopass`;
- генерацию OCR-результатов и merge-логику;
- конфигурацию LM Studio URL через `CHANDRA_BASE_URL` и `QWEN_BASE_URL`.

Разница только в orchestration:

- local OCR работает напрямую из desktop-процесса через `multiprocessing`;
- remote OCR добавляет HTTP API, Redis, Celery, Supabase и R2 как обязательные части runtime.

## Данные и границы

### Аннотации

- JSON-аннотации сериализуются через `AnnotationIO`.
- Для Supabase используется `AnnotationDBIO`, таблица `annotations`.
- `Document` и `Page` остаются legacy-совместимыми моделями для GUI.

### Дерево проектов

- `TreeClient` работает с `tree_nodes`, `node_files`, `annotations`.
- `ProjectTreeWidget` строит UI поверх `TreeClient`.
- `app/services.py` даёт более узкие facade-функции для GUI и тестов.

### Файлы и артефакты

- R2 используется для OCR-артефактов, публичных ссылок и server-side storage API.
- В desktop-части прямой доступ идёт через `rd_core.r2_storage.R2Storage`.
- В серверной части используется `AsyncR2StorageSync` и асинхронные обвязки.

## Точки расширения

- Новый GUI-функционал: обычно новый mixin или отдельный виджет в `app/gui/`.
- Новый OCR-бэкенд: реализация в `rd_core/ocr/` и регистрация в `factory.py`.
- Новый server endpoint: модуль в `services/remote_ocr/server/routes/` и подключение в `main.py`.
- Новая схема/таблица: изменение `database/migrations/prod.sql`, экспортов и краткой документации.

## Что считать каноном

- Onboarding и команды запуска: [../README.md](../README.md).
- Архитектурная карта: этот файл.
- Серверный runtime: [REMOTE_OCR_SERVER.md](REMOTE_OCR_SERVER.md).
- Схема и миграции: [DATABASE.md](DATABASE.md).
