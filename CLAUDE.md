# CLAUDE.md

Этот файл даёт короткий ориентир по репозиторию для агентной работы.

## Project Overview

`Core Structure` — desktop-приложение для разметки PDF и OCR.

Ключевая архитектурная деталь: текущий GUI OCR-поток работает локально через `app/ocr/LocalOcrRunner`. Remote OCR сервер в `services/remote_ocr` остаётся отдельным поддерживаемым режимом, а не обязательной частью desktop runtime.

## Основные команды

### Desktop

```bash
pip install .
python app/main.py
```

### Build

```bash
pip install .[build]
python build.py
```

Результат: `dist/CoreStructure.exe`.

### Remote OCR Server

```bash
pip install -r services/remote_ocr/requirements.txt
docker compose up --build
```

Ручной запуск:

```bash
redis-server
uvicorn services.remote_ocr.server.main:app --host 0.0.0.0 --port 8000 --reload
celery -A services.remote_ocr.server.celery_app worker --loglevel=info --concurrency=1
```

### Проверки

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/ready
curl http://localhost:8000/queue
pytest tests/test_docs_smoke.py -q
```

## Repo Map

| Path | Purpose |
| --- | --- |
| `app/main.py` | Desktop entrypoint |
| `app/gui/` | Главное окно, page viewer, project tree, OCR panel |
| `app/ocr/` | Local OCR runner и pipeline |
| `app/tree_client/` | Supabase client для tree / annotations |
| `app/services.py` | Facade-слой для GUI над инфраструктурой |
| `rd_core/models/` | Доменные модели |
| `rd_core/ocr/` | OCR backends и factory |
| `rd_core/pdf_utils.py` | PDF rendering / extraction |
| `rd_core/annotation_io.py` | Annotation format и migration |
| `rd_core/r2_storage.py` | Sync R2 storage client |
| `services/remote_ocr/server/` | FastAPI, Celery, OCR orchestration |
| `database/` | SQL dump и schema exports |
| `tests/` | Unit, contract и smoke tests |

## Ключевые потоки

### Desktop + local OCR

```text
MainWindow
  -> RemoteOCRPanel
  -> JobsController
  -> LocalOcrRunner
  -> app/ocr/local_pipeline.py
  -> rd_core OCR backends + server pdf_twopass modules
```

### Remote OCR

```text
HTTP client
  -> FastAPI app
  -> jobs routes
  -> Celery worker
  -> Supabase + R2
```

## Важные модули

- `app/gui/main_window.py` — сборка desktop UI.
- `app/gui/project_tree/widget.py` — дерево проектов.
- `app/gui/remote_ocr/jobs_controller.py` — orchestration локальных OCR-задач.
- `app/ocr/local_runner.py` — multiprocessing wrapper.
- `app/ocr/local_pipeline.py` — локальный OCR pipeline.
- `services/remote_ocr/server/main.py` — FastAPI app.
- `services/remote_ocr/server/routes/` — API surface.
- `services/remote_ocr/server/pdf_twopass/` — two-pass OCR.

## Extension Points

- Новый GUI-функционал: обычно отдельный mixin или widget в `app/gui/`.
- Новый OCR backend: реализация в `rd_core/ocr/` и регистрация в `factory.py`.
- Новый server endpoint: модуль в `services/remote_ocr/server/routes/`.
- Изменение tree/storage integration: `app/tree_client/`, `app/services.py`, server `node_storage/`.
- Изменение схемы: `database/migrations/prod.sql` и `database/exports/`.

## Environment

Чаще всего нужны:

- `CHANDRA_BASE_URL`
- `QWEN_BASE_URL`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `R2_ACCOUNT_ID` или `R2_ENDPOINT_URL`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `R2_PUBLIC_URL`
- `REDIS_URL`

Опционально:

- `REMOTE_OCR_DATA_DIR`
- `OCR_CONFIG_PATH`
- `LOG_LEVEL`
- `LOG_FORMAT`
- `ENABLE_PERFORMANCE_MONITOR`

Шаблон: [.env.example](.env.example)

## Documentation

- [README.md](README.md) — onboarding и запуск.
- [docs/README.md](docs/README.md) — индекс документов.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — карта подсистем.
- [docs/REMOTE_OCR_SERVER.md](docs/REMOTE_OCR_SERVER.md) — серверный runtime.
- [docs/DATABASE.md](docs/DATABASE.md) — схема и миграции.
