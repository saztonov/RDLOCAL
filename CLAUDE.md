# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Core Structure** - Desktop PDF annotation tool with distributed OCR processing.

Stack: PySide6 (Qt 6), FastAPI, Celery + Redis, Supabase (PostgreSQL), Cloudflare R2.

## Language / Язык

**Все планы и описания задач выводить на русском языке.**

## Commands

### Desktop Client
```bash
python app/main.py                    # Run application
python build.py                        # Build executable → dist/CoreStructure.exe
```

### Remote OCR Server
```bash
# Docker (recommended)
docker compose up --build

# Manual
redis-server                                                              # Terminal 1
uvicorn services.remote_ocr.server.main:app --host 0.0.0.0 --port 8000 --reload  # Terminal 2
celery -A services.remote_ocr.server.celery_app worker --loglevel=info --concurrency=1  # Terminal 3
```

### Health Checks
```bash
curl http://localhost:8000/health
curl http://localhost:8000/queue
```

## Architecture

```
Desktop Client (PySide6)
    ├─→ RemoteOCRClient (HTTP) ──→ Remote OCR Server (FastAPI)
    │                                 ├─→ Celery Workers (Redis)
    │                                 ├─→ Supabase (jobs, tree_nodes)
    │                                 └─→ R2 Storage (files)
    ├─→ TreeClient (REST) ──→ Supabase (project hierarchy)
    └─→ R2Storage (boto3) ──→ Cloudflare R2 (prompts, results)
```

### Key Components

| Directory | Purpose |
|-----------|---------|
| `app/` | Desktop GUI (PySide6). Entry: `app/main.py` |
| `app/gui/` | GUI modules. Core: `main_window.py`, `page_viewer.py` |
| `app/gui/blocks/` | Block operations (7 mixins) |
| `app/gui/project_tree/` | Tree widget (4 mixins + helpers) |
| `app/gui/remote_ocr/` | OCR panel (5 mixins) |
| `app/gui/reconciliation/` | R2 ↔ Supabase sync verification |
| `app/ocr_client/` | Remote OCR HTTP client (6 modules) |
| `app/tree_client/` | Supabase tree API (6 mixins) |
| `rd_core/` | Core logic: models, PDF utils, R2 storage, OCR engines |
| `rd_core/ocr/` | OCR backends (OpenRouter, Datalab, Chandra, Qwen). Protocol: `base.py` |
| `services/remote_ocr/server/` | FastAPI server + Celery tasks |
| `services/remote_ocr/server/node_storage/` | OCR results registration in tree |
| `services/remote_ocr/server/pdf_twopass/` | Two-pass PDF (pass1_crops, pass2_ocr) |
| `services/remote_ocr/server/routes/jobs/` | Modular job routes |
| `database/migrations/` | SQL migration files |
| `docs/` | Full documentation |

### Architectural Patterns

**Mixin Pattern (GUI)**: `MainWindow` composes multiple mixins - each handles specific responsibility (menus, file ops, block handlers).

**Protocol Pattern (OCR)**: `OCRBackend` protocol in `rd_core/ocr/base.py`. Implementations: `OpenRouterBackend`, `DatalabOCRBackend`, `ChandraBackend`, `QwenBackend`. Factory: `create_ocr_engine()`.

**Context Manager (PDF)**: `PDFDocument` in `rd_core/pdf_utils.py` uses `__enter__`/`__exit__` for resource cleanup.

### OCR Backends (`rd_core/ocr/`)

| Backend | Engine key | API | Notes |
|---------|-----------|-----|-------|
| `OpenRouterBackend` | `openrouter` | OpenRouter (VLM) | Cloud, default for IMAGE blocks |
| `DatalabOCRBackend` | `datalab` | Datalab Marker | Cloud, segmentation + OCR |
| `ChandraBackend` | `chandra` | LM Studio | Local, shared instance with Qwen |
| `QwenBackend` | `qwen` | LM Studio | Local, mode="text" or "stamp" |
| `DummyBackend` | `dummy` | — | Testing stub |

Server uses `backend_factory.py` to create a trio: `strip_backend` (TEXT), `image_backend` (IMAGE), `stamp_backend` (stamps/titles).

### GUI Mixin Composition

| Widget | Mixins |
|--------|--------|
| `MainWindow` | MenuSetup, PanelsSetup, FileOperations, BlockHandlers |
| `PageViewer` | ContextMenu, MouseEvents, BlockRendering, Polygon, ResizeHandles |
| `ProjectTreeWidget` | TreeNodeOps, TreeSync, TreeFilter, TreeContextMenu |
| `RemoteOCRPanel` | JobOps, Download, PollingController, ResultHandler, TableManager |
| `TreeClient` | Core, Nodes, Status, Files, Categories, PathV2 |
| `R2Storage` | Upload, Download, Utils (Singleton) |

### Key Dialogs (`app/gui/`)

| Dialog | Purpose |
|--------|---------|
| `ocr_preview_widget.py` | OCR result preview + HTML WebEngine |
| `block_verification_dialog.py` | Verify annotation vs result coords |
| `reconciliation/dialog.py` | R2 ↔ Supabase file sync |
| `image_categories_dialog.py` | Image block categorization |
| `ocr_settings/dialog.py` | OCR engine settings |
| `r2_viewer/dialog.py` | R2 file browser |

### Data Models (`rd_core/models/`)

```python
Block      # block.py - annotation with coords, categories
ArmorID    # armor_id.py - OCR-resistant ID format (XXXX-XXXX-XXX)
Document   # document.py - collection of pages
Page       # document.py - page with blocks list
BlockType  # enums.py - TEXT, IMAGE
ShapeType  # enums.py - RECTANGLE, POLYGON
```

```python
# app/tree_models.py
TreeNode   # Node in project hierarchy
NodeFile   # File metadata (r2_key, file_type)
FileType   # PDF, ANNOTATION, RESULT_JSON, CROPS, BLOCKS_INDEX...
NodeType   # FOLDER, DOCUMENT (v2)
```

### Annotation Format

Version: 2 (`rd_core/annotation_io.py`)

**V2 additions:** `coords_norm`, `source`, `shape_type`, `created_at`

Auto-migration v1→v2 on load via `AnnotationIO.load()`.

### Database Tables (Supabase v2)

- `jobs` - OCR tasks (status: draft/queued/processing/done/error/paused)
- `job_files` - Job files (pdf, blocks, results, crops)
- `job_settings` - Model settings per job
- `tree_nodes` - Project hierarchy v2 (path, depth, pdf_status, is_locked)
- `node_files` - Node files (PDF, annotations, OCR results, crops)

node_type v2: `folder` | `document` (legacy types in attributes.legacy_node_type)

### Server Components

#### Core Processing
| Module | Purpose |
|--------|---------|
| `task_ocr_twopass.py` | Two-pass OCR task with checkpoint/resume |
| `task_dispatch.py` | Unified task dispatch + dynamic timeout |
| `backend_factory.py` | Backend trio factory (strip/image/stamp) |
| `pdf_streaming_core.py` | Memory-efficient PDF page rendering |
| `pdf_twopass/` | Pass1 crops → Pass2 OCR → Cleanup |
| `lmstudio_lifecycle.py` | Redis-coordinated LM Studio model lifecycle |
| `checkpoint_models.py` | OCR checkpoint/resume for paused jobs |
| `worker_prompts.py` | Prompt building + batch response parsing |
| `block_verification.py` | Verify + retry missing blocks |
| `block_id_matcher.py` | ArmorID + fuzzy matching |

#### Performance & Reliability
| Module | Purpose |
|--------|---------|
| `debounced_updater.py` | Reduce Supabase calls (-90%) |
| `rate_limiter.py` | Token bucket for Datalab API |
| `memory_utils.py` | Memory monitoring (psutil) |
| `queue_checker.py` | Backpressure mechanism |
| `timeout_utils.py` | Dynamic timeout calculation |

#### Storage & Configuration
| Module | Purpose |
|--------|---------|
| `async_r2_storage.py` | Async R2 (aioboto3) |
| `node_storage/` | Register OCR results in tree |
| `settings.py` | Dynamic config (config.yaml → env → default) |
| `config.yaml` | Main server config (timeouts, concurrency, engines) |

### Client Infrastructure

#### Caching (`rd_core/`, `app/gui/`)
| Module | Purpose |
|--------|---------|
| `annotation_cache.py` | Annotation cache + delayed R2 sync |
| `r2_disk_cache.py` | LRU disk cache (3GB default) |
| `r2_metadata_cache.py` | R2 metadata caching |
| `cache_base.py` | ThreadSafeCache base class |
| `tree_cache_ops.py` | Tree operations caching |

#### Utilities
| Module | Purpose |
|--------|---------|
| `logging_manager.py` | Dynamic log folder switching |

### OCR Job Lifecycle (Two-Pass)

1. User selects blocks → `RemoteOCRClient.create_job()` → POST /jobs
2. Server: PDF + blocks.json → R2, job → Supabase (status=queued)
3. Celery worker two-pass:
   - PASS 1: Stream PDF → crops to disk (memory-efficient)
   - PASS 2: OCR from manifest → merge results
4. Upload results → status=done
5. Client polls GET /jobs/{id} → downloads result

### API Routes Structure

```
routes/
├── jobs/
│   ├── router.py          # Main router
│   ├── create_handler.py  # POST /jobs
│   ├── read_handlers.py   # GET /jobs, /jobs/{id}, /jobs/changes
│   ├── update_handlers.py # start/pause/resume/cancel/restart
│   └── delete_handler.py  # DELETE /jobs/{id}
├── storage.py             # R2 storage API (upload/download/exists)
└── tree.py                # Tree nodes API (CRUD + node_files)
```

## Extension Points

**Add GUI feature**: Create mixin in `app/gui/`, add to MainWindow inheritance.

**Add OCR engine**: Implement `OCRBackend` protocol, add to `rd_core/ocr/`, register in `factory.py`.

**Add API endpoint**: Create route in `services/remote_ocr/server/routes/`, include in `main.py`.

**Modify database**: Add migration in `database/migrations/`, document in `docs/DATABASE.md`.

**Add cache layer**: Extend `ThreadSafeCache` in `rd_core/cache_base.py`.

**Add tree client feature**: Create mixin in `app/tree_client/`, add to `TreeClient`.

**Add server optimization**: Follow patterns in `debounced_updater.py`, `rate_limiter.py`.

## Configuration

Required `.env` variables:
- `SUPABASE_URL`, `SUPABASE_KEY` - Database
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` - Storage
- `OPENROUTER_API_KEY` and/or `DATALAB_API_KEY` - OCR engines (cloud)
- `CHANDRA_BASE_URL` - LM Studio URL for Chandra
- `QWEN_BASE_URL` - LM Studio URL for Qwen (fallback: `CHANDRA_BASE_URL`)
- `REMOTE_OCR_BASE_URL` - Server URL (default: http://localhost:8000)
- `REDIS_URL` - For server (default: redis://redis:6379/0)

Server-specific (dynamic loading via `settings.py` from `config.yaml`):
- `REMOTE_OCR_DATA_DIR` - Work directory (default: /data)
- `REMOTE_OCR_API_KEY` - API authentication
- `config.yaml` - Main config file (timeouts, concurrency, engines, DPI, queue settings)
- `OCR_CONFIG_PATH` - Override path to config.yaml

## Logging (Server)

Централизованная система логирования в `services/remote_ocr/server/`.

### Конфигурация

| Переменная | Значения | По умолчанию |
|------------|----------|--------------|
| `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR | INFO |
| `LOG_FORMAT` | json, text | json |

### Ключевые модули

| Модуль | Назначение |
|--------|------------|
| `logging_config.py` | JSONFormatter, setup_logging(), get_logger() |
| `celery_signals.py` | Celery lifecycle (task_prerun/postrun/failure) |

### Использование

```python
from .logging_config import get_logger

logger = get_logger(__name__)
logger.info("Message", extra={"job_id": "abc-123", "event": "task_started"})
```

### JSON формат (production)

```json
{"timestamp": "2026-01-25T12:34:56Z", "level": "INFO", "logger": "tasks", "message": "Task completed", "job_id": "abc-123", "duration_ms": 45230}
```

### Extra поля

Поддерживаемые поля для `extra={}`: `job_id`, `task_id`, `block_id`, `strip_id`, `page_index`, `duration_ms`, `memory_mb`, `event`, `status`, `status_code`, `method`, `path`, `exception_type`.

## Code Style

From `.cursorrules`: Be maximally concise. Code only in code blocks. Changes as minimal diff. No explanations unless asked. If text needed - max 5 points, each ≤ 12 words.

## Documentation

- `docs/ARCHITECTURE.md` - Full technical documentation
- `docs/DEVELOPER_GUIDE.md` - Code examples and patterns
- `docs/DATABASE.md` - Complete DB schema
- `docs/REMOTE_OCR_SERVER.md` - Server API reference
