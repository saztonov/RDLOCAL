# Remote OCR Server

## Назначение

`services/remote_ocr/server` — серверный OCR-режим проекта. Он добавляет:

- HTTP API на `FastAPI`;
- очередь задач через `Celery` + `Redis`;
- работу с `Supabase` и `Cloudflare R2`;
- фоновые OCR-задачи вне desktop-процесса.

Важно: текущий GUI по умолчанию использует локальный OCR через `LocalOcrRunner` и не требует запуска этого сервера. Серверный режим нужен для интеграций, очередей и удалённой обработки.

## Состав

- `main.py` — FastAPI entrypoint и health endpoints.
- `routes/jobs/` — API для OCR-задач.
- `routes/tree.py` — прокси-операции над деревом проектов и `node_files`.
- `routes/storage.py` — R2 storage API.
- `tasks.py` / `task_ocr_twopass.py` — Celery tasks и orchestration OCR.
- `pdf_twopass/` — two-pass pipeline.
- `settings.py` + `config.yaml` — конфиг runtime.

## Запуск

### Docker Compose

Из корня репозитория:

```bash
docker compose up --build
```

Compose поднимает:

- `web` — FastAPI сервер на `127.0.0.1:8000`;
- `redis` — broker для Celery;
- `worker` — Celery worker.

### Ручной запуск

Установка зависимостей:

```bash
pip install -r services/remote_ocr/requirements.txt
```

Запуск:

```bash
redis-server
uvicorn services.remote_ocr.server.main:app --host 0.0.0.0 --port 8000 --reload
celery -A services.remote_ocr.server.celery_app worker --loglevel=info --concurrency=1
```

### Проверка

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/ready
curl http://localhost:8000/queue
```

- `/health` — процесс жив.
- `/health/ready` — проверка `Redis`, `Supabase`, конфигурации и доступности OCR provider URL.
- `/queue` — backpressure и размер очереди.

## Конфигурация

### Обязательные переменные

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `R2_ACCOUNT_ID` или `R2_ENDPOINT_URL`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `CHANDRA_BASE_URL`

### Обычно нужны

- `QWEN_BASE_URL`
- `R2_PUBLIC_URL`
- `REDIS_URL`

### Опциональные

- `REMOTE_OCR_DATA_DIR`
- `OCR_CONFIG_PATH`
- `LOG_LEVEL`
- `LOG_FORMAT`

### `config.yaml`

Путь по умолчанию: `services/remote_ocr/server/config.yaml`.

В `config.yaml` лежат числовые и текстовые настройки runtime:

- concurrency;
- timeouts;
- DPI;
- лимиты очереди;
- default OCR models;
- system/user prompts для `IMAGE` и `STAMP`.

Приоритет настроек:

1. ENV override.
2. Значение из `config.yaml`.

Секреты и URL сервисов должны приходить через `.env`, а не быть зашиты в YAML.

## API-поверхность

### System endpoints

- `GET /health`
- `GET /health/ready`
- `GET /queue`

### Jobs API

Префикс: `/jobs`

Основные операции:

- `POST /jobs` — создать OCR-задачу;
- `GET /jobs` — список задач;
- `GET /jobs/{job_id}` — состояние задачи;
- `GET /jobs/{job_id}/details` — расширенные детали;
- `GET /jobs/{job_id}/result` — presigned URL на результат;
- `POST /jobs/{job_id}/start|pause|resume|cancel|restart|reorder`;
- `PATCH /jobs/{job_id}`;
- `DELETE /jobs/{job_id}`.

### Tree API

Префикс: `/api/tree`

Используется для узлов дерева проектов, PDF status и `node_files`.

Основные операции:

- чтение корневых и дочерних узлов;
- создание, обновление и удаление узлов;
- обновление PDF status;
- чтение и добавление файлов узла.

### Storage API

Префикс: `/api/storage`

Используется для работы с R2:

- `exists`;
- download / download-text;
- upload / upload-text;
- delete / delete-batch / delete-prefix;
- list / list-metadata.

## Как сервер связан с local OCR

Локальный OCR pipeline в `app/ocr/local_pipeline.py` переиспользует серверные модули:

- `pdf_twopass`;
- OCR verification;
- генерацию результатов;
- общие OCR-бэкенды и формат результатов.

Серверный режим добавляет поверх этого:

- HTTP API;
- Celery orchestration;
- Redis broker;
- регистрацию файлов и статусов в `Supabase` и `R2`;
- отдельный lifecycle worker-процесса.

## Что проверять при проблемах

- Доступен ли `CHANDRA_BASE_URL` и при необходимости `QWEN_BASE_URL`.
- Отвечает ли `Redis`.
- Заданы ли `SUPABASE_*` и `R2_*`.
- Что показывает `GET /health/ready`.
- Есть ли ошибки в логах `web` и `worker`.
