# Карта рабочих процессов CoreStructure

Этот документ описывает как работает приложение — от запуска до получения OCR-результатов. Каждый раздел объясняет один пользовательский сценарий простым языком с указанием ключевых файлов.

---

## Содержание

1. [Общая архитектура](#1-общая-архитектура)
2. [Работа с деревом проектов](#2-работа-с-деревом-проектов)
3. [Открытие и просмотр PDF](#3-открытие-и-просмотр-pdf)
4. [Разметка блоков](#4-разметка-блоков)
5. [Локальный OCR](#5-локальный-ocr)
6. [Удалённый OCR (сервер)](#6-удалённый-ocr-сервер)
7. [Получение результатов](#7-получение-результатов)
8. [Карта хранилищ данных](#8-карта-хранилищ-данных)
9. [Карта формирования документов](#9-карта-формирования-документов)
10. [Узкие места](#10-узкие-места)
11. [Дублирование](#11-дублирование)

---

## Что считать каноном

- Канонический пользовательский сценарий: tree-backed документ, открытый из дерева проектов, с аннотацией в Supabase и OCR-артефактами в R2.
- Канонический источник разметки: таблица `annotations`, а не sidecar JSON рядом с PDF.
- Канонический каталог OCR-артефактов: префикс документа в R2 плюс записи в `node_files`.
- Local OCR не является отдельным доменным контуром: он переиспользует общий OCR-код из `rd_core/pipeline`.
- Remote OCR остаётся полным production-контуром: API, очередь, воркер, R2, Supabase и регистрация результатов.

---

## 1. Общая архитектура

Приложение состоит из четырёх слоёв. Верхний слой — то, что видит пользователь. Нижний — внешние сервисы, куда уходят данные.

```
┌─────────────────────────────────────────────────────────────┐
│                    GUI (десктоп, PyQt6)                      │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Дерево       │  │ Просмотр     │  │ Панель блоков     │  │
│  │ проектов     │  │ страниц PDF  │  │ + панель OCR      │  │
│  │              │  │              │  │                   │  │
│  │ Папки,       │  │ Рисование    │  │ Список блоков,    │  │
│  │ документы,   │  │ блоков,      │  │ запуск OCR,       │  │
│  │ загрузка PDF │  │ навигация    │  │ просмотр          │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬──────────┘  │
│         └─────────────────┼───────────────────┘             │
│                    ┌──────┴──────┐                           │
│                    │ MainWindow  │                           │
│                    │ (6 миксинов)│                           │
│                    └──────┬──────┘                           │
├───────────────────────────┼─────────────────────────────────┤
│              Сервисный слой (app/)                           │
│                                                             │
│  services.py        Единая точка доступа к R2 и Supabase    │
│  TreeClient         HTTP-клиент к Supabase (5 миксинов)     │
│  AnnotationDBIO     Сохранение/загрузка разметки в БД       │
│  LocalOcrRunner     Запуск OCR в отдельном процессе          │
│  JobsController     Управление OCR-задачами (local/remote)   │
├───────────────────────────┼─────────────────────────────────┤
│              Ядро (rd_core/)                                 │
│                                                             │
│  models/            Block, Document, Page, ArmorID, enums   │
│  pipeline/          Двухпроходный OCR-конвейер              │
│  ocr/               Бэкенды: Chandra (текст), Qwen (картинки)│
│  pdf_utils.py       Рендер PDF через PyMuPDF                │
│  annotation_io.py   Версионирование и миграция разметки     │
│  r2_storage.py      Загрузка/скачивание файлов из облака    │
├───────────────────────────┼─────────────────────────────────┤
│              Внешняя инфраструктура                          │
│                                                             │
│  ┌───────────┐  ┌─────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ Supabase  │  │   R2    │  │ LM Studio│  │ Remote OCR  │ │
│  │ (база     │  │ (файлы  │  │ (локаль- │  │ Server      │ │
│  │  данных)  │  │  в обла-│  │  ные LLM │  │ (FastAPI +  │ │
│  │           │  │  ке)    │  │  модели) │  │  Celery +   │ │
│  │           │  │         │  │          │  │  Redis)     │ │
│  └───────────┘  └─────────┘  └──────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**GUI** — интерфейс на Qt: дерево проектов слева, просмотр PDF по центру, панель блоков и OCR справа.

**Сервисный слой** — прослойка между GUI и инфраструктурой. GUI не лезет в Supabase напрямую, а вызывает `services.py` или `TreeClient`.

**Ядро (rd_core)** — модели данных, OCR-бэкенды, работа с PDF. Не зависит от GUI. Используется и десктопом, и сервером.

**Инфраструктура** — Supabase хранит структуру проектов и разметку, R2 хранит PDF и результаты, LM Studio запускает LLM-модели, Remote Server — альтернативный режим обработки.

---

## 2. Работа с деревом проектов

**Что происходит:** при запуске приложения слева появляется дерево проектов — папки и документы, загруженные из Supabase. Пользователь может создавать папки, загружать PDF, переименовывать, архивировать, перетаскивать узлы.

```
Запуск приложения
       │
       ▼
InitialLoadWorker (фоновый поток)
       │
       ▼
TreeClient.get_root_nodes()  ──→  Supabase: tree_nodes
       │                                 (parent_id = null)
       ▼
Отображение корневых узлов
       │
       ▼  (клик на папку)
TreeClient.get_children(id)  ──→  Supabase: tree_nodes
       │                                 (parent_id = id)
       ▼
Отображение дочерних узлов (ленивая подгрузка)
       │
       ▼  (каждые N секунд)
TreeRefreshWorker  ──→  Supabase: проверка изменений
```

**Что делает каждый компонент:**

- **ProjectTreeWidget** ([widget.py](../app/gui/project_tree/widget.py)) — виджет дерева с 7 миксинами: CRUD узлов, фильтрация, контекстное меню, архивация, загрузка, раскрытие, перетаскивание.
- **TreeClient** ([app/tree_client/](../app/tree_client/)) — HTTP-клиент к Supabase REST API. 5 миксинов: узлы, статусы, файлы, пути, аннотации. Пул соединений (10 макс).
- **TreeNodeCache** — кеш узлов с TTL 120 секунд, чтобы не ходить в Supabase на каждый клик.
- **PDFStatusManager** — отслеживает статус обработки PDF (есть ли OCR, все ли файлы на месте).

**Данные:**

| Таблица Supabase | Что хранит |
|-----------------|-----------|
| `tree_nodes` | Узлы дерева: id, parent_id, name, type (FOLDER/DOCUMENT), path, status |
| `node_files` | Файлы узла: r2_key, file_type (PDF/ANNOTATION/OCR_HTML/RESULT_MD/...) |

---

## 3. Открытие и просмотр PDF

**Что происходит:** пользователь кликает на документ в дереве → PDF скачивается из облака → отображается страница → поверх рисуются ранее созданные блоки разметки.

```
Двойной клик на документе в дереве
       │
       ▼
document_selected(node_id, r2_key)       ← сигнал от дерева
       │
       ▼
FileDownloadMixin создаёт temp workspace
       │
       ├──→  R2Storage.download_file(r2_key)  ──→  Cloudflare R2
       │                                          (с кешем на диске)
       ▼
PyMuPDF рендерит страницу (150 DPI для превью)
       │
       ▼
PageViewer отображает растр страницы
       │
       ├──→  AnnotationDBIO.load_from_db(node_id)  ──→  Supabase: annotations
       │            │
       │            ▼
       │     Миграция формата (v0 → v1 → v2 если нужно)
       │            │
       │            ▼
       │     annotation_canonicalizer: подгонка координат под размер страницы
       │
       ▼
PageViewer рисует блоки поверх страницы (прямоугольники, полигоны)
```

**Что делает каждый компонент:**

- **FileDownloadMixin** ([file_download.py](../app/gui/file_download.py)) — создаёт temp workspace, скачивает PDF и sidecar-файлы из R2. Не скачивает повторно если файл уже есть.
- **NavigationManager** ([navigation_manager.py](../app/gui/navigation_manager.py)) — листание страниц (вперёд/назад/к номеру), сохранение зума для каждой страницы.
- **PageViewer** ([page_viewer.py](../app/gui/page_viewer.py)) — графическая сцена Qt. Показывает страницу PDF как картинку и рисует поверх блоки. 5 миксинов: контекстное меню, обработка мыши, отрисовка блоков, полигоны, ручки изменения размера.
- **PDFDocument** ([pdf_utils.py](../rd_core/pdf_utils.py)) — обёртка над PyMuPDF. Рендер страницы в PIL Image. Два режима: 150 DPI (быстрый превью) и 300 DPI (для OCR).
- **AnnotationDBIO** ([annotation_db.py](../app/annotation_db.py)) — загрузка разметки из Supabase с автоматической миграцией старых форматов.
- **annotation_canonicalizer** ([annotation_canonicalizer.py](../rd_core/annotation_canonicalizer.py)) — проверяет что координаты блоков соответствуют реальным размерам страницы PDF. Если размеры не совпадают — пересчитывает.

**Кеширование:**

- R2 файлы кешируются на диске ([r2_disk_cache.py](../rd_core/r2_disk_cache.py)) — LRU кеш с TTL.
- Существование файлов в R2 кешируется в памяти ([r2_metadata_cache.py](../rd_core/r2_metadata_cache.py)) — экономит HEAD-запросы.
- Зум-состояние каждой страницы сохраняется в `NavigationManager.page_zoom_states`.

---

## 4. Разметка блоков

**Что происходит:** пользователь рисует прямоугольники или полигоны на странице PDF. Каждый нарисованный элемент становится «блоком» с уникальным ID. Блоки сохраняются в Supabase автоматически.

```
Пользователь рисует прямоугольник на странице
       │
       ▼
MouseEventsMixin фиксирует координаты
       │
       ▼
BlockDrawMixin._on_block_drawn(x, y, w, h)
       │
       ├──→  Генерация ArmorID (OCR-устойчивый ID)
       │
       ├──→  Создание Block(coords_px, coords_norm, block_type)
       │
       ├──→  Добавление на Page → Document
       │
       ├──→  PageViewer отрисовывает прямоугольник
       │
       └──→  BlocksTreeManager обновляет дерево блоков
              │
              ▼
       AnnotationCache (дебаунс, dirty-флаг)
              │
              ▼
       AnnotationDBIO.save_to_db(document, node_id)  ──→  Supabase: annotations
```

**Три типа блоков:**

| Тип | Для чего | OCR-бэкенд |
|-----|---------|-------------|
| **TEXT** | Текстовые области (абзацы, заголовки, таблицы) | Chandra (chandra-ocr-2) |
| **IMAGE** | Иллюстрации, схемы, чертежи | Qwen (qwen3.5-27b) |
| **STAMP** | Штампы, печати, QR-коды | Qwen (qwen3.5-9b) |

**ArmorID** ([armor_id.py](../rd_core/models/armor_id.py)) — специальный формат ID блока (11 символов: `XXXX-XXXX-XXX`). Использует алфавит из 26 символов, устойчивых к OCR-путанице (нет похожих пар типа 0/O, 1/I). Может восстановить до 3 ошибок с помощью матрицы OCR-конфузий.

**Координаты блока:** хранятся в двух форматах одновременно:
- `coords_px` — пиксельные координаты на текущем рендере страницы
- `coords_norm` — нормализованные координаты (0..1), не зависят от масштаба

**Undo/Redo** ([undo_redo_mixin.py](../app/gui/undo_redo_mixin.py)) — перед каждым изменением блоков сохраняется полная копия состояния страницы. Максимум 50 шагов отмены.

**Формируемый документ:** **Annotation JSON** — сохраняется в таблицу `annotations` в Supabase. Формат v2: `{pages: [{page_index, width, height, blocks: [...]}]}`.

---

## 5. Локальный OCR

**Что происходит:** пользователь нажимает «Запустить OCR» → создаётся отдельный процесс → PDF нарезается на кропы блоков → каждый кроп отправляется в LLM-модель → результаты собираются в HTML и Markdown.

```
┌─────────────────────────────────────────────────────────────┐
│  ДЕСКТОП (основной процесс)                                 │
│                                                             │
│  Пользователь нажимает "Запустить OCR"                      │
│         │                                                   │
│         ▼                                                   │
│  JobsController подготавливает блоки                        │
│  (smart mode: только блоки без OCR, correction: повторные)  │
│         │                                                   │
│         ▼                                                   │
│  flush autosave → sync аннотации в Supabase                │
│         │                                                   │
│         ▼                                                   │
│  LocalOcrRunner.submit_job(pdf, blocks, output_dir)         │
│         │                                                   │
│         ▼                                                   │
│  multiprocessing.Process(target=run_local_ocr)              │
│         │           ▲                                       │
│         │           │  multiprocessing.Queue                │
│         │           │  (прогресс, статус)                   │
│         ▼           │                                       │
│  ┌──────────────────┴─────────────────────────────────────┐ │
│  │  SUBPROCESS (изолированная память)                     │ │
│  │                                                        │ │
│  │  ┌── PASS 1: Нарезка кропов ────────────────────────┐  │ │
│  │  │                                                  │  │ │
│  │  │  PDF страница → рендер 300 DPI (полное качество)  │  │ │
│  │  │       │                                          │  │ │
│  │  │       ▼                                          │  │ │
│  │  │  Для каждого блока:                              │  │ │
│  │  │    вырезать область → сохранить как PDF-кроп     │  │ │
│  │  │       │                                          │  │ │
│  │  │       ▼                                          │  │ │
│  │  │  TwoPassManifest (список кропов + метаданные)    │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                         │                              │ │
│  │                         ▼                              │ │
│  │  ┌── PASS 2: Распознавание (3 фазы) ───────────────┐  │ │
│  │  │                                                  │  │ │
│  │  │  Фаза A: TEXT блоки                              │  │ │
│  │  │    ChandraBackend → LM Studio (chandra-ocr-2)    │  │ │
│  │  │    Промпт: «Распознай текст, верни HTML»         │  │ │
│  │  │                                                  │  │ │
│  │  │  ── пауза: смена модели в LM Studio ──           │  │ │
│  │  │                                                  │  │ │
│  │  │  Фаза B: STAMP блоки                             │  │ │
│  │  │    QwenBackend → LM Studio (qwen3.5-9b)          │  │ │
│  │  │    Промпт: «Опиши штамп, извлеки данные»         │  │ │
│  │  │                                                  │  │ │
│  │  │  ── пауза: смена модели в LM Studio ──           │  │ │
│  │  │                                                  │  │ │
│  │  │  Фаза C: IMAGE блоки                             │  │ │
│  │  │    QwenBackend → LM Studio (qwen3.5-27b)         │  │ │
│  │  │    Промпт: «Опиши изображение»                   │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                         │                              │ │
│  │                         ▼                              │ │
│  │  ┌── Верификация ───────────────────────────────────┐  │ │
│  │  │  block_verification.py                           │  │ │
│  │  │  Проверка качества: пустые ответы, ошибки,       │  │ │
│  │  │  подозрительный вывод → повторная попытка        │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                         │                              │ │
│  │                         ▼                              │ │
│  │  ┌── Генерация результатов ─────────────────────────┐  │ │
│  │  │  result_pipeline.py                              │  │ │
│  │  │    → OCR HTML  (html_generator.py)               │  │ │
│  │  │    → Markdown  (md/generator.py)                 │  │ │
│  │  │    → annotation.json (обогащённые блоки)         │  │ │
│  │  │    → export_report.json (статистика)             │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  После завершения subprocess:                               │
│  auto_download_result → merge OCR-полей обратно в документ  │
│  → autosave → sync в Supabase                              │
│  → sync HTML/MD/crops в R2 + node_files (если tree-backed)  │
└─────────────────────────────────────────────────────────────┘
```

**Что делает каждый компонент:**

- **JobsController** ([jobs_controller.py](../app/gui/remote_ocr/jobs_controller.py)) — оркестратор: выбор режима (local/remote), correction mode, подготовка блоков, polling, apply результатов обратно в документ. Самый нагруженный модуль GUI (~1278 строк).
- **LocalOcrRunner** ([local_runner.py](../app/ocr/local_runner.py)) — создаёт `multiprocessing.Process` для изоляции памяти. Общается с GUI через Queue (прогресс, ошибки, завершение). Qt-сигналы: `job_created`, `job_updated`, `job_finished`.
- **local_pipeline.py** ([local_pipeline.py](../app/ocr/local_pipeline.py)) — точка входа в subprocess. Загружает конфиг, создаёт бэкенды, запускает двухпроходный конвейер, генерирует артефакты, опционально синхронизирует в tree storage.
- **pass1_crops** ([pass1_crops.py](../rd_core/pipeline/pass1_crops.py)) — рендерит PDF-страницы в 300 DPI, вырезает область каждого блока, сохраняет как PDF-кроп на диск.
- **pass2_ocr_async** ([pass2_ocr_async.py](../rd_core/pipeline/pass2_ocr_async.py)) — асинхронно отправляет кропы в LLM. Три фазы: text → stamp → image. Между фазами меняется модель в LM Studio.
- **ChandraBackend** ([chandra.py](../rd_core/ocr/chandra.py)) — OCR для текста. Retry 3 раза с exponential backoff. Авто-retry при обрезке ответа (finish_reason=length).
- **QwenBackend** ([qwen.py](../rd_core/ocr/qwen.py)) — OCR для картинок и штампов. Два варианта: 9b (штампы, быстрее) и 27b (картинки, точнее).
- **block_verification** ([block_verification.py](../rd_core/ocr/block_verification.py)) — проверяет качество OCR. Детектирует: пустые ответы, JSON-дампы вместо текста, «размышления» модели, подозрительно короткий текст.
- **result_pipeline** ([result_pipeline.py](../rd_core/ocr/result_pipeline.py)) — собирает все результаты в финальные файлы: HTML, Markdown, JSON.

**Checkpoint** ([checkpoint_models.py](../rd_core/pipeline/checkpoint_models.py)) — после каждой фазы сохраняется состояние. Если процесс упал, можно продолжить с того же места.

**Формируемые документы:**

| Документ | Генератор | Назначение |
|----------|----------|-----------|
| OCR HTML | [html_generator.py](../rd_core/ocr/html_generator.py) | Полный документ с разметкой для просмотра |
| Markdown | [md/generator.py](../rd_core/ocr/md/generator.py) | Текстовый формат для внешних потребителей |
| annotation.json | [local_pipeline.py](../app/ocr/local_pipeline.py) | Обогащённые блоки (ocr_text, ocr_html заполнены) |
| export_report.json | [local_pipeline.py](../app/ocr/local_pipeline.py) | Статистика генерации (только локально) |

---

## 6. Удалённый OCR (сервер)

**Что происходит:** пользователь отправляет задачу на сервер → сервер ставит в очередь → Celery worker обрабатывает → результат загружается в R2 → десктоп скачивает и применяет.

### Десктоп: отправка задачи

```
Пользователь нажимает "Отправить на сервер"
       │
       ▼
JobsController flush autosave → sync аннотации
       │
       ▼
POST /jobs/node (tree-backed)  ──→  FastAPI сервер
или POST /jobs (legacy upload)
       │
       ▼
Десктоп начинает polling: GET /jobs (каждые N секунд)
```

### Сервер: 7 стадий обработки

```
POST /jobs → FastAPI → Celery queue (Redis)
       │
       ▼
┌─── Стадия 1: VALIDATE ──────────────────────────────────┐
│ Проверяет: не дубль ли это? не отменена ли задача?       │
│ Не превышен ли лимит попыток (3)? Не истекло ли время?   │
│ Захватывает execution_lock в Redis (атомарно, NX).       │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌─── Стадия 2: BOOTSTRAP ─────────────────────────────────┐
│ Скачивает PDF и аннотацию из R2 / Supabase.             │
│ Парсит блоки из JSON. Фильтрует (correction mode).      │
│ Создаёт тройку бэкендов (Chandra + 2x Qwen).           │
│ Загружает модели в LM Studio.                           │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌─── Стадия 3: RUN_OCR ───────────────────────────────────┐
│ Тот же двухпроходный конвейер что и в локальном OCR:    │
│ Pass 1 (кропы) → Pass 2 (text → stamp → image).        │
│ Код общий: rd_core/pipeline/.                           │
│ + checkpoint для восстановления при падении.            │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌─── Стадия 4: GENERATE ──────────────────────────────────┐
│ Генерирует HTML, Markdown, result.json.                 │
│ Запускает верификацию и повторные попытки.               │
│ Correction mode: загружает existing annotation из       │
│ Supabase и обновляет только correction-блоки.           │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌─── Стадия 5: UPLOAD ────────────────────────────────────┐
│ Загружает все результаты в R2:                          │
│ HTML, MD, result.json, кропы блоков.                    │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌─── Стадия 6: REGISTER ──────────────────────────────────┐
│ Записывает enriched аннотацию в Supabase (annotations). │
│ Регистрирует файлы в node_files.                        │
│ Обновляет pdf_status узла.                              │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌─── Стадия 7: FINALIZE ──────────────────────────────────┐
│ Очищает временные файлы и рабочую директорию.           │
│ Отпускает execution_lock. Выгружает модели.             │
│ Ставит статус "done".                                   │
└──────────────────────────────────────────────────────────┘
```

### Инфраструктура сервера

| Компонент | Файл | Что делает простым языком |
|-----------|------|--------------------------|
| **Execution Lock** | [execution_lock.py](../services/remote_ocr/server/execution_lock.py) | Redis-замок: одна задача не может обрабатываться двумя воркерами одновременно |
| **Zombie Detector** | [zombie_detector.py](../services/remote_ocr/server/zombie_detector.py) | Каждые 5 минут ищет «зависшие» задачи (processing дольше 2-6 часов) и помечает как ошибку |
| **Rate Limiter** | [rate_limiter.py](../services/remote_ocr/server/rate_limiter.py) | Ограничивает число одновременных запросов к LM Studio, чтобы не перегрузить GPU |
| **Debounced Updater** | [debounced_updater.py](../services/remote_ocr/server/debounced_updater.py) | Обновляет прогресс в Supabase не чаще 1 раза в 3 секунды (экономит запросы) |
| **LM Studio Lifecycle** | [lmstudio_lifecycle.py](../services/remote_ocr/server/lmstudio_lifecycle.py) | Управляет загрузкой/выгрузкой моделей. Grace period 120с перед выгрузкой |
| **Dynamic Timeout** | [timeout_utils.py](../services/remote_ocr/server/timeout_utils.py) | Вычисляет таймаут задачи по формуле: base + (блоки * секунды_на_блок) + буфер |

### Применение результатов обратно в документ

```
Polling замечает статус "done"
       │
       ▼
Local: auto_download_result → _reload_annotation_from_result(output_dir)
Remote: _remote_download_result → читает аннотацию из Supabase
       │
       ▼
Обе ветки: строят {block_id → OCR fields}
       │
       ▼
Merge ocr_text/ocr_html/ocr_json/ocr_meta в текущий annotation_document
(не заменяют документ целиком — мерджат поля по block.id)
       │
       ▼
_render_current_page() + обновление blocks tree + OCR preview
       │
       ▼
autosave → sync в Supabase
```

---

## 7. Получение результатов

**Что происходит:** после завершения OCR приложение ищет файлы результатов, скачивает их и показывает пользователю.

```
OCR завершён (статус "done")
       │
       ▼
sidecar_resolver.py ищет файлы результатов:
       │
       ├── 1. Запись в node_files (предпочтительно)
       ├── 2. Путь tree_docs/{node_id}/ (текущая схема)
       ├── 3. Путь рядом с PDF (легаси)
       └── 4. Не найдено
       │
       ▼
R2Storage.download_file()  ──→  скачивание HTML/MD
       │
       ▼
OcrPreviewWidget (QWebEngineView)
       │
       ▼
Пользователь видит распознанный текст в панели справа
```

**Проверка качества результатов** ([ocr_result.py](../rd_core/ocr_result.py)):

| Маркер | Значение |
|--------|---------|
| Чистый текст | Успешное распознавание |
| `[Ошибка: ...]` | Повторяемая ошибка (можно перезапустить) |
| `[НеПовторяемая ошибка: ...]` | Постоянная ошибка (блок нераспознаваем) |

Детектор подозрительного вывода (`is_suspicious_output`) ловит:
- JSON-дампы вместо текста
- «Размышления» модели («I need to...», «Давайте...»)
- Слишком мало текста (< 20 символов в длинном ответе)

**PDF Status** ([pdf_status.py](../rd_core/pdf_status.py)) — итоговый статус документа:
- `COMPLETE` — все файлы и блоки на месте
- `MISSING_FILES` — OCR HTML или другие файлы отсутствуют
- `MISSING_BLOCKS` — аннотация есть, но часть страниц без блоков

---

## 8. Карта хранилищ данных

Где лежат данные и как к ним обращаются:

```
┌─────────────────────────────────────────────────────────┐
│                     SUPABASE (PostgreSQL)                │
│                                                         │
│  tree_nodes ──── Дерево проектов (папки, документы)     │
│  node_files ──── Метаданные файлов (R2-ключи, типы)    │
│  annotations ─── JSON-разметка блоков (формат v2)      │
│  jobs ────────── Задачи OCR (local + remote)           │
│  job_settings ── Настройки OCR (модели, режимы)        │
│                                                         │
│  Доступ: TreeClient (десктоп), storage_*.py (сервер)   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                     CLOUDFLARE R2 (S3)                   │
│                                                         │
│  tree_docs/{node_id}/ ── PDF, OCR HTML, MD, кропы      │
│  local://ocr_jobs/{id}/ ── standalone задачи           │
│                                                         │
│  Доступ: R2Storage singleton (boto3, пул 20 потоков)   │
│  Кеш: r2_disk_cache (файлы), r2_metadata_cache (HEAD)  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                     REDIS                                │
│                                                         │
│  celery (брокер) ── очередь задач                      │
│  ocr:executing:{id} ── execution lock                  │
│  ocr:lmstudio:jobs ── активные модели                  │
│  pause_cache ── кеш статусов паузы (15с TTL)           │
│  jobs_list_cache ── кеш списка задач (5с TTL)          │
│                                                         │
│  Доступ: Celery (брокер), прямые SET/GET               │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                     ЛОКАЛЬНЫЙ ДИСК                       │
│                                                         │
│  Temp workspace ── скачанные PDF для просмотра         │
│  Временные кропы (pass1) ── удаляются после OCR        │
│  R2 кеш (LRU) ── скачанные файлы                      │
│  Логи (logs/client.log) ── ротация 5MB x 3             │
│                                                         │
│  Доступ: прямой I/O, r2_disk_cache                     │
└─────────────────────────────────────────────────────────┘
```

---

## 9. Карта формирования документов

Какой артефакт → где рождается → где хранится → кто потребляет:

| Артефакт | Где рождается | Где хранится | Кто потребляет |
|----------|--------------|-------------|---------------|
| **Annotation JSON** (разметка блоков) | `Document.to_dict()` через `AnnotationDBIO` (десктоп) или `ocr_registry` (сервер) | Supabase `annotations` | PageViewer, OCR pipeline, correction mode |
| **`annotation.json`** (snapshot) | [local_pipeline.py](../app/ocr/local_pipeline.py) (local), [create_handler.py](../services/remote_ocr/server/routes/jobs/create_handler.py) (remote input) | Локальный output_dir, R2 | Local result merge, server bootstrap |
| **OCR HTML** (`*_ocr.html`) | [html_generator.py](../rd_core/ocr/html_generator.py) | R2 + `node_files`, local output_dir | OcrPreviewWidget, браузер |
| **Markdown** (`*_document.md`) | [md/generator.py](../rd_core/ocr/md/generator.py) | R2 + `node_files`, local output_dir | Экспорт, LLM-сценарии |
| **Кропы блоков** (`crops/{block_id}.pdf`) | [pass1_crops.py](../rd_core/pipeline/pass1_crops.py) | Диск (temp) → R2 + `node_files` | Chandra, Qwen (OCR вход) |
| **Export report** (`*_export_report.json`) | [local_pipeline.py](../app/ocr/local_pipeline.py) | Только local output_dir | Пользователь (анализ) |
| **Job record** | [local_runner.py](../app/ocr/local_runner.py), [storage_jobs.py](../services/remote_ocr/server/storage_jobs.py) | Supabase `jobs` | RemoteOCRPanel, polling |
| **Node files registry** | [local_pipeline.py](../app/ocr/local_pipeline.py), [ocr_registry.py](../services/remote_ocr/server/node_storage/ocr_registry.py) | Supabase `node_files` | Sidecar resolver, tree download |

**Важное расхождение:** local OCR пишет `annotation.json` как sidecar-файл, а server-side делает ставку на enriched annotation в Supabase + HTML/MD/crops в R2. Часть кода живёт в модели «DB + R2 files», а часть ещё ожидает sidecar JSON.

---

## 10. Узкие места

### A. JobsController как god object

**Где:** [jobs_controller.py](../app/gui/remote_ocr/jobs_controller.py) (~1278 строк)

**Что происходит:** один файл совмещает local/remote OCR режимы, correction logic, polling, snapshot persistence, create job, auto-download и merge результатов.

**Влияние:** любое изменение OCR-флоу требует править один перегруженный модуль и проверять оба режима.

### B. Смена моделей LM Studio

**Где:** `pass2_ocr_async` — между фазами text → stamp → image.

**Что происходит:** LM Studio может держать в памяти только одну модель. При переходе между фазами старая модель выгружается, новая загружается. Это занимает 30-120 секунд.

**Влияние:** задача с 3 типами блоков тратит 1-4 минуты только на переключение моделей.

### C. Синхронные Supabase/R2 вызовы из GUI

**Где:** [file_operations.py](../app/gui/file_operations.py), [file_download.py](../app/gui/file_download.py), [widget.py](../app/gui/project_tree/widget.py)

**Что происходит:** GUI-слой напрямую создаёт `TreeClient()` и `R2Storage()` и делает сетевые операции. Не все из них вынесены в фоновые потоки.

**Влияние:** UI latency при медленной сети, сложнее тестировать.

### D. Тяжёлая OCR post-processing стадия

**Где:** [local_pipeline.py](../app/ocr/local_pipeline.py), [task_results.py](../services/remote_ocr/server/task_results.py)

**Что происходит:** генерация артефактов, verification retry, model swap, upload и sync с persistence смешаны в одном критическом пути.

**Влияние:** долгий latency, дорого разбирать partial failures.

### E. Legacy-ветки и storage drift

**Где:** [annotation_io.py](../rd_core/annotation_io.py), [sidecar_resolver.py](../rd_core/sidecar_resolver.py)

**Что происходит:** система одновременно поддерживает несколько поколений аннотаций (v0, v1, v2) и sidecar-схем (node_files, tree_docs, рядом с PDF).

**Влияние:** повышается цена изменений. Новый поток может сломать старую совместимость.

### F. Рендер 300 DPI для OCR

**Где:** `pass1_crops` — рендер страниц для нарезки кропов.

**Что происходит:** страница A0 при 300 DPI — это ~140 мегапикселей.

**Смягчение:** `StreamingPDFProcessor` адаптивно снижает DPI для больших страниц (лимит 100M пикселей). Subprocess изолирует память — после OCR вся память освобождается.

### G. Deep copy для Undo/Redo

**Где:** [undo_redo_mixin.py](../app/gui/undo_redo_mixin.py) — до 50 полных копий состояния.

**Что происходит:** перед каждым действием копируется весь список блоков страницы.

**Влияние:** при 500+ блоках на странице — заметный расход RAM.

### H. Shared tables с несколькими владельцами

**Где:** `jobs`, `annotations`, `node_files` — пишутся и из GUI, и из сервера.

**Влияние:** трудно определить owner-слой и инварианты данных.

---

## 11. Дублирование

### A. Генерация OCR-результатов (поведенческое)

| Путь | Файл |
|------|------|
| Local | [local_pipeline.py](../app/ocr/local_pipeline.py) `_generate_local_results()` |
| Remote | [task_results.py](../services/remote_ocr/server/task_results.py) `generate_results()` |

**Почему:** local OCR был построен как desktop-адаптация server pipeline без Celery/HTTP. Оба вызывают `rd_core` для генерации, но оборачивают по-разному.

**Риск:** исправления в post-processing нужно переносить дважды; дрейф артефактов уже заметен.

### B. Два пути сохранения аннотаций

| Путь | Файл | Контекст |
|------|------|---------|
| Десктоп | [annotation_db.py](../app/annotation_db.py) `AnnotationDBIO` | Сохранение из GUI (autosave, ручной) |
| Сервер | [ocr_registry.py](../services/remote_ocr/server/node_storage/ocr_registry.py) | Сохранение после OCR (с enriched блоками) |

**Оба пишут в одну таблицу** `annotations`. Разные migration/fallback правила и разные HTTP-обвязки для одного ресурса.

### C. Два пути применения OCR-результатов в документ

| Путь | Метод в [jobs_controller.py](../app/gui/remote_ocr/jobs_controller.py) |
|------|------|
| Local | `_reload_annotation_from_result()` — из filesystem |
| Remote | `_on_remote_result_loaded()` — из Supabase |

**Риск:** могут разойтись правила merge, очистки `is_correction`, preview refresh и autosave.

### D. Shim-файлы сервера (осознанное)

Сервер содержит файлы-прокладки, которые реэкспортируют классы из `rd_core`:

| Серверный файл | Источник |
|---------------|---------|
| `server/checkpoint_models.py` | `rd_core/pipeline/checkpoint_models.py` |
| `server/manifest_models.py` | `rd_core/pipeline/manifest_models.py` |
| `server/memory_utils.py` | `rd_core/pipeline/memory_utils.py` |
| `server/worker_prompts.py` | `rd_core/pipeline/prompts.py` |

**Зачем:** стабильный API для сервера. Если `rd_core` изменит структуру, достаточно обновить один shim. Это осознанное решение, не проблема.

### E. Прямой доступ GUI к TreeClient/R2 в обход фасада

[services.py](../app/services.py) существует как фасад, но множество GUI модулей создают клиентов напрямую. Фасад появился позже, когда прямые вызовы уже расползлись по mixin-слоям.

### F. Кеширование (5 отдельных реализаций)

| Кеш | Что кешируется |
|-----|---------------|
| `TreeNodeCache` | Узлы дерева (TTL 120с) |
| `PdfStatusCache` | Статусы PDF |
| [r2_disk_cache.py](../rd_core/r2_disk_cache.py) | Скачанные файлы (LRU) |
| [r2_metadata_cache.py](../rd_core/r2_metadata_cache.py) | Существование файлов (TTL) |
| Redis `pause_cache` / `jobs_list_cache` | Состояние задач (5-15с TTL) |

---

## Hotspots — файлы, на которые стоит смотреть первыми

| Файл | Строк | Почему hotspot |
|------|-------|---------------|
| [jobs_controller.py](../app/gui/remote_ocr/jobs_controller.py) | ~1278 | Главная точка схождения local/remote OCR, correction, polling, result merge |
| [local_pipeline.py](../app/ocr/local_pipeline.py) | ~629 | Local OCR orchestration, artefact generation и sync в tree |
| [task_results.py](../services/remote_ocr/server/task_results.py) | ~383 | Каноническая бизнес-логика server-side результатов |
| [job_stages.py](../services/remote_ocr/server/job_stages.py) | ~350+ | 7 стадий серверной обработки |
| [widget.py](../app/gui/project_tree/widget.py) | ~422 | Tree navigation, locking, context actions |
| [main_window.py](../app/gui/main_window.py) | ~396 | Центральный state shell с mixin-boundaries |
