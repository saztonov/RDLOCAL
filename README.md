# Core Structure

`Core Structure` — десктопное приложение для разметки PDF-документов и запуска OCR.

Текущий основной сценарий работы GUI — локальный OCR через `app/ocr` и `LocalOcrRunner`. При этом в репозитории остаётся полноценный Remote OCR сервер в `services/remote_ocr`, который нужен для API, очередей и фоновой обработки через FastAPI + Celery + Redis.

## Что есть в проекте

- Desktop GUI на `PySide6` для просмотра PDF, разметки блоков и навигации по дереву проектов.
- Локальный OCR pipeline на базе `LM Studio`, запускаемый из GUI без HTTP и Celery.
- Общее ядро `rd_core` с моделями, PDF-утилитами, OCR-бэкендами и R2 storage.
- Remote OCR сервер для очередей, shared storage и фоновых OCR-задач.
- Интеграции с `Supabase` и `Cloudflare R2` для дерева документов, аннотаций и артефактов.

## Быстрый старт

### Desktop + local OCR

```bash
pip install .
python app/main.py
```

Для локального OCR нужен `.env` на основе [.env.example](.env.example). Минимально:

```env
CHANDRA_BASE_URL=http://localhost:1234
QWEN_BASE_URL=http://localhost:1234
```

Если нужны дерево проектов, аннотации в Supabase и R2-артефакты, добавьте также `SUPABASE_*` и `R2_*`.

### Remote OCR сервер

Установите серверные зависимости:

```bash
pip install -r services/remote_ocr/requirements.txt
```

Запуск через Docker Compose:

```bash
docker compose up --build
```

Ручной запуск:

```bash
redis-server
uvicorn services.remote_ocr.server.main:app --host 0.0.0.0 --port 8000 --reload
celery -A services.remote_ocr.server.celery_app worker --loglevel=info --concurrency=1
```

Проверка:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/ready
curl http://localhost:8000/queue
```

### Сборка desktop-приложения

```bash
pip install .[build]
python build.py
```

Результат: `dist/CoreStructure.exe`.

## Переменные окружения

Полный шаблон лежит в [.env.example](.env.example). Практически переменные делятся на четыре группы:

- Local OCR: `CHANDRA_BASE_URL`, `QWEN_BASE_URL`.
- Tree / annotations: `SUPABASE_URL`, `SUPABASE_KEY`.
- R2 storage: `R2_ACCOUNT_ID` или `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL`.
- Remote OCR server: `REDIS_URL`, `REMOTE_OCR_DATA_DIR`, `OCR_CONFIG_PATH`, `LOG_LEVEL`, `LOG_FORMAT`.

`ENABLE_PERFORMANCE_MONITOR` используется только desktop-клиентом и по умолчанию не нужен.

## Карта репозитория

```text
app/
  gui/            Desktop UI: MainWindow, PageViewer, ProjectTreeWidget, RemoteOCRPanel
  ocr/            Local OCR runner и pipeline
  tree_client/    Клиент для Supabase tree / annotations
  services.py     Facade-слой для GUI над R2, tree и annotations

rd_core/
  models/         Block, Page, Document и enum'ы
  ocr/            OCR-бэкенды Chandra, Qwen, Dummy
  pdf_utils.py    Рендеринг и чтение PDF
  r2_storage.py   Sync R2 storage client
  annotation_io.py

services/remote_ocr/server/
  main.py         FastAPI entrypoint
  routes/         Jobs, tree, storage API
  tasks.py        Celery tasks
  pdf_twopass/    Two-pass OCR pipeline
  config.yaml     Серверный конфиг

database/
  migrations/     SQL-дамп и миграции схемы
  exports/        Экспортированные артефакты схемы

tests/
  smoke, contract и unit-тесты
```

## Архитектурная заметка

В репозитории есть два OCR-сценария:

1. GUI -> `JobsController` -> `LocalOcrRunner` -> `app/ocr/local_pipeline.py`.
2. Клиент/интеграция -> FastAPI -> Celery worker -> `services/remote_ocr/server/task_ocr_twopass.py`.

Оба сценария используют общие OCR-бэкенды из `rd_core` и общие two-pass модули из `services/remote_ocr/server/pdf_twopass`.

## Документация

- [docs/README.md](docs/README.md) — индекс актуальных документов.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — карта подсистем и потоков данных.
- [docs/REMOTE_OCR_SERVER.md](docs/REMOTE_OCR_SERVER.md) — серверный режим, API и конфиг.
- [docs/DATABASE.md](docs/DATABASE.md) — минимальный обзор схемы и миграций.
