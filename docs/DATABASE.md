# Схема базы данных

## Обзор

Приложение использует **Supabase** (PostgreSQL) для хранения:
- Задач OCR и их файлов
- Иерархии проектов (Tree)
- Справочников (типы стадий и разделов)

## ER-диаграмма

```
┌─────────────────────────────────────────────────────────────────┐
│                         JOBS CLUSTER                            │
├─────────────────────────────────────────────────────────────────┤

┌─────────────────────┐     ┌─────────────────────┐
│       jobs          │     │    job_settings     │
├─────────────────────┤     ├─────────────────────┤
│ id           uuid PK│◄────│ job_id    uuid PK FK│
│ client_id    text   │     │ text_model    text  │
│ document_id  text   │     │ table_model   text  │
│ document_name text  │     │ image_model   text  │
│ task_name    text   │     │ stamp_model   text  │
│ status       text   │     │ created_at timestamptz
│ progress     real   │     │ updated_at timestamptz
│ engine       text   │     └─────────────────────┘
│ r2_prefix    text   │
│ node_id      uuid FK├─────┐ (связь с tree_nodes)
│ error_message text  │     │
│ migrated_to_node bool     │ ◄── v2: флаг миграции
│ created_at timestamptz    │
│ updated_at timestamptz    │     ┌─────────────────────┐
└─────────────────────┘◄────┼─────│     job_files       │
                            │     ├─────────────────────┤
                            │     │ id         uuid PK  │
                            │     │ job_id     uuid FK  │
                            │     │ file_type  text     │
                            │     │ r2_key     text     │
                            │     │ file_name  text     │
                            │     │ file_size  bigint   │
                            │     │ metadata   jsonb    │ ◄── для кропов: block_id, coords
                            │     │ created_at timestamptz
                            │     └─────────────────────┘
                            │
├───────────────────────────┴─────────────────────────────────────┤
│                    TREE & FILES CLUSTER (v2)                    │
├─────────────────────────────────────────────────────────────────┤

┌─────────────────────┐     ┌─────────────────────┐
│    tree_nodes       │     │     node_files      │
├─────────────────────┤     ├─────────────────────┤
│ id         uuid PK  │◄────│ id         uuid PK  │
│ parent_id  uuid FK ─┘     │ node_id    uuid FK  │
│ client_id  text     │     │ file_type  text     │
│ node_type  text     │     │ r2_key     text UK  │
│ name       text     │     │ file_name  text     │
│ code       text     │     │ file_size  bigint   │
│ version    int      │     │ mime_type  text     │
│ status     text     │     │ metadata   jsonb    │
│ attributes jsonb    │     │ created_at timestamptz
│ sort_order int      │     │ updated_at timestamptz
│ path       text     │ ◄── │ └─────────────────────┘
│ depth      int      │ v2
│ children_count int  │
│ descendants_count int
│ files_count int     │
│ pdf_status text     │
│ is_locked  bool     │
│ created_at timestamptz
│ updated_at timestamptz
└─────────────────────┘

node_type v2: 'folder' | 'document'
(legacy: project, stage, section, task_folder → folder)

├─────────────────────────────────────────────────────────────────┤
│                       СПРАВОЧНИКИ                               │
├─────────────────────────────────────────────────────────────────┤

┌─────────────────────┐     ┌─────────────────────┐
│    stage_types      │     │   section_types     │
├─────────────────────┤     ├─────────────────────┤
│ id         serial PK│     │ id         serial PK│
│ code       text UK  │     │ code       text UK  │
│ name       text     │     │ name       text     │
│ sort_order int      │     │ sort_order int      │
└─────────────────────┘     └─────────────────────┘
```

---

## Таблицы

### jobs

Основная таблица задач OCR.

```sql
CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id text NOT NULL,
    document_id text NOT NULL,
    document_name text NOT NULL,
    task_name text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'queued',
    progress real DEFAULT 0,
    engine text DEFAULT '',
    r2_prefix text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
```

#### Поля

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | uuid | Первичный ключ |
| `client_id` | text | Идентификатор клиента (из `~/.config/CoreStructure/client_id.txt`) |
| `document_id` | text | SHA256 хеш PDF файла |
| `document_name` | text | Имя PDF файла |
| `task_name` | text | Название задачи (пользовательское) |
| `status` | text | Статус: `draft`, `queued`, `processing`, `done`, `error`, `paused` |
| `progress` | real | Прогресс выполнения (0.0 - 1.0) |
| `engine` | text | OCR движок: `lmstudio`, `chandra` |
| `r2_prefix` | text | Префикс файлов в R2 (`ocr_jobs/{job_id}`) |
| `error_message` | text | Сообщение об ошибке (если status=error) |
| `created_at` | timestamptz | Дата создания |
| `updated_at` | timestamptz | Дата последнего обновления |

#### Индексы

```sql
CREATE INDEX idx_jobs_client_id ON jobs(client_id);
CREATE INDEX idx_jobs_document_id ON jobs(document_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_created_at ON jobs(created_at DESC);
```

#### Триггер

```sql
CREATE TRIGGER update_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

---

### job_files

Файлы, связанные с задачей.

```sql
CREATE TABLE job_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    file_type text NOT NULL,
    r2_key text NOT NULL,
    file_name text NOT NULL,
    file_size bigint DEFAULT 0,
    metadata jsonb DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
```

**Важно:** При удалении задачи (job) все связанные записи из `job_files` удаляются каскадно.
Данные в `node_files` при этом **не затрагиваются** (они связаны с `tree_nodes`, а не с `jobs`).

#### Поля

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | uuid | Первичный ключ |
| `job_id` | uuid | Ссылка на задачу (NOT NULL, каскадное удаление) |
| `file_type` | text | Тип файла (см. таблицу ниже) |
| `r2_key` | text | Полный путь к файлу в R2 |
| `file_name` | text | Имя файла |
| `file_size` | bigint | Размер файла в байтах |
| `metadata` | jsonb | Метаданные файла (для кропов: block_id, page_index, coords_norm, block_type) |
| `created_at` | timestamptz | Дата создания |

#### Типы файлов (file_type)

| Тип | Описание |
|-----|----------|
| `pdf` | Исходный PDF документ |
| `blocks` | blocks.json с координатами блоков |
| `annotation` | annotation.json с полной разметкой |
| `result` | result.json — JSON результат OCR |
| `result_md` | document.md — Markdown результат OCR |
| `ocr_html` | ocr_result.html — HTML результат |
| `crop` | Кроп блока (PDF) |

#### Метаданные для кропов (file_type='crop')

```json
{
  "block_id": "uuid-блока",
  "page_index": 0,
  "coords_norm": {"x1": 0.1, "y1": 0.2, "x2": 0.9, "y2": 0.8},
  "block_type": "text|image"
}
```

#### Индексы

```sql
CREATE INDEX idx_job_files_job_id ON job_files(job_id);
CREATE INDEX idx_job_files_type ON job_files(file_type);
CREATE INDEX idx_job_files_metadata ON job_files USING gin (metadata);
CREATE INDEX idx_job_files_block_id ON job_files((metadata->>'block_id')) WHERE file_type = 'crop';
```

---

### job_settings

Настройки моделей для задачи.

```sql
CREATE TABLE job_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    text_model text DEFAULT '',
    table_model text DEFAULT '',
    image_model text DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(job_id)
);
```

#### Примеры моделей

| Поле | Пример значения |
|------|-----------------|
| `text_model` | `qwen3.5-9b` (LM Studio) |
| `table_model` | `qwen3.5-9b` (LM Studio) |
| `image_model` | `qwen3.5-9b` (LM Studio) |
| `stamp_model` | `qwen3.5-9b` (LM Studio Qwen stamp mode) |

---

### tree_nodes

Иерархия проектов с произвольной вложенностью (v2).

```sql
CREATE TABLE tree_nodes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id uuid REFERENCES tree_nodes(id) ON DELETE CASCADE,
    client_id text NOT NULL,
    node_type text NOT NULL,  -- v2: 'folder' или 'document'
    name text NOT NULL,
    code text,
    version integer DEFAULT 1,
    status text DEFAULT 'active',
    attributes jsonb DEFAULT '{}',
    sort_order integer DEFAULT 0,

    -- v2: Новые поля для оптимизации
    path text,                          -- Materialized path: uuid1.uuid2.uuid3
    depth integer DEFAULT 0,            -- Глубина от корня (0 = корневой)
    children_count integer DEFAULT 0,   -- Прямые дочерние узлы
    descendants_count integer DEFAULT 0,-- Все потомки рекурсивно
    files_count integer DEFAULT 0,      -- Файлы в node_files для этого узла

    -- Для документов
    pdf_status text DEFAULT 'unknown',
    pdf_status_message text,
    pdf_status_updated_at timestamptz,
    is_locked boolean DEFAULT false,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CHECK (status IN ('active', 'completed', 'archived'))
);
```

#### Типы узлов (node_type) - v2

| Тип | Описание |
|-----|----------|
| `folder` | Папка (произвольная вложенность) |
| `document` | Документ PDF (листовой узел) |

**Legacy типы** (для обратной совместимости):
- `project` → `folder`
- `stage` → `folder`
- `section` → `folder`
- `task_folder` → `folder`

Старый тип сохраняется в `attributes.legacy_node_type`.

#### Новые поля v2

| Поле | Описание |
|------|----------|
| `path` | Materialized path: `uuid1.uuid2.uuid3` для быстрого обхода |
| `depth` | Глубина от корня (0 = проект) |
| `children_count` | Количество прямых дочерних (триггер) |
| `descendants_count` | Количество всех потомков (триггер) |
| `files_count` | Файлы в node_files (триггер) |

#### Поле attributes

```json
// Для document
{
  "original_name": "план_этажа.pdf",
  "r2_key": "tree_docs/uuid/file.pdf",
  "file_size": 1234567,
  "mime_type": "application/pdf",
  "legacy_node_type": "document"  // Сохранённый старый тип
}

// Для folder (бывший project/stage/section)
{
  "legacy_node_type": "project"   // или "stage", "section", "task_folder"
}
```

#### Индексы

```sql
-- Базовые
CREATE INDEX idx_tree_nodes_parent_id ON tree_nodes(parent_id);
CREATE INDEX idx_tree_nodes_client_id ON tree_nodes(client_id);
CREATE INDEX idx_tree_nodes_type ON tree_nodes(node_type);
CREATE INDEX idx_tree_nodes_sort ON tree_nodes(parent_id, sort_order);

-- v2: Новые индексы для оптимизации
CREATE INDEX idx_tree_nodes_path ON tree_nodes USING btree (path text_pattern_ops);
CREATE INDEX idx_tree_nodes_depth ON tree_nodes(depth);
CREATE INDEX idx_tree_nodes_parent_sort ON tree_nodes(parent_id, sort_order, created_at);
CREATE INDEX idx_tree_nodes_roots ON tree_nodes(client_id, sort_order) WHERE parent_id IS NULL;
```

#### Триггеры v2

- `tr_tree_nodes_path_insert` - вычисляет path/depth при INSERT
- `tr_tree_nodes_path_update` - обновляет path/depth при UPDATE parent_id
- `tr_tree_nodes_children_count` - счётчик прямых детей
- `tr_tree_nodes_descendants_count` - счётчик всех потомков
- `tr_node_files_count` - счётчик файлов (на node_files)

---

### node_files

Файлы узлов дерева (PDF, аннотации, кропы, результаты OCR).

```sql
CREATE TABLE node_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id uuid NOT NULL REFERENCES tree_nodes(id) ON DELETE CASCADE,
    file_type text NOT NULL,
    r2_key text NOT NULL,
    file_name text NOT NULL,
    file_size bigint DEFAULT 0,
    mime_type text DEFAULT 'application/octet-stream',
    metadata jsonb DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (node_id, r2_key)
);
```

#### Типы файлов (file_type)

| Тип | Описание |
|-----|----------|
| `pdf` | Исходный PDF документ |
| `annotation` | annotation.json с разметкой блоков |
| `result_json` | result.json — JSON результат OCR |
| `result_md` | document.md — Markdown результат |
| `ocr_html` | ocr_result.html — HTML результат |
| `crop` | Кроп блока (PDF) |
| `crops_folder` | Папка с кропами |

#### Индексы

```sql
CREATE INDEX idx_node_files_node_id ON node_files(node_id);
CREATE INDEX idx_node_files_node_type ON node_files(node_id, file_type);
CREATE INDEX idx_node_files_r2_key ON node_files(r2_key);
CREATE INDEX idx_node_files_type ON node_files(file_type);
```

---

### stage_types

Справочник типов стадий.

```sql
CREATE TABLE stage_types (
    id serial PRIMARY KEY,
    code text UNIQUE NOT NULL,
    name text NOT NULL,
    sort_order integer DEFAULT 0
);

-- Предзаполнение
INSERT INTO stage_types (code, name, sort_order) VALUES
    ('ПД', 'Проектная документация', 1),
    ('РД', 'Рабочая документация', 2);
```

---

### section_types

Справочник типов разделов.

```sql
CREATE TABLE section_types (
    id serial PRIMARY KEY,
    code text UNIQUE NOT NULL,
    name text NOT NULL,
    sort_order integer DEFAULT 0
);

-- Предзаполнение
INSERT INTO section_types (code, name, sort_order) VALUES
    ('АР', 'Архитектурные решения', 1),
    ('КР', 'Конструктивные решения', 2),
    ('ОВ', 'Отопление и вентиляция', 3),
    ('ВК', 'Водоснабжение и канализация', 4),
    ('ЭО', 'Электрооборудование', 5),
    ('СС', 'Слаботочные системы', 6),
    ('ГП', 'Генеральный план', 7),
    ('ПОС', 'Проект организации строительства', 8),
    ('ПЗ', 'Пояснительная записка', 9);
```

---

## Функции

### update_updated_at_column

Автоматическое обновление `updated_at`.

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;
```

---

## Триггеры

```sql
-- jobs
CREATE TRIGGER update_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- job_settings
CREATE TRIGGER update_job_settings_updated_at
    BEFORE UPDATE ON job_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- tree_nodes
CREATE TRIGGER update_tree_nodes_updated_at
    BEFORE UPDATE ON tree_nodes
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

---

## Миграции

### Создание схемы

```bash
# Через Supabase SQL Editor или psql
psql -h db.xxx.supabase.co -U postgres -d postgres -f tree_schema.sql
psql -h db.xxx.supabase.co -U postgres -d postgres -f database/migrations/prod.sql
```

### Экспорт схемы

```bash
pg_dump -h db.xxx.supabase.co -U postgres -d postgres \
    --schema-only --no-owner --no-privileges \
    > database/migrations/schema_export.sql
```

---

## Запросы

### Получить задачи клиента

```sql
SELECT * FROM jobs
WHERE client_id = 'xxx-xxx-xxx'
ORDER BY created_at DESC;
```

### Получить активные задачи

```sql
SELECT * FROM jobs
WHERE status IN ('queued', 'processing')
ORDER BY created_at;
```

### Получить файлы задачи

```sql
SELECT * FROM job_files
WHERE job_id = 'xxx-xxx-xxx'
ORDER BY file_type;
```

### Получить дерево проекта

```sql
-- Корневые проекты
SELECT * FROM tree_nodes
WHERE parent_id IS NULL
  AND client_id = 'xxx-xxx-xxx'
ORDER BY sort_order, created_at;

-- Дочерние узлы
SELECT * FROM tree_nodes
WHERE parent_id = 'xxx-xxx-xxx'
ORDER BY sort_order, created_at;
```

### Рекурсивный запрос всего дерева

```sql
WITH RECURSIVE tree AS (
    SELECT *, 0 as level
    FROM tree_nodes
    WHERE parent_id IS NULL AND client_id = 'xxx-xxx-xxx'

    UNION ALL

    SELECT tn.*, t.level + 1
    FROM tree_nodes tn
    JOIN tree t ON tn.parent_id = t.id
)
SELECT * FROM tree
ORDER BY level, sort_order;
```

### Подсчёт задач по статусам

```sql
SELECT status, COUNT(*) as count
FROM jobs
GROUP BY status
ORDER BY count DESC;
```

---

## RLS (Row Level Security)

Для production рекомендуется включить RLS:

```sql
-- Включить RLS
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE tree_nodes ENABLE ROW LEVEL SECURITY;

-- Политика: клиент видит только свои данные
CREATE POLICY jobs_client_policy ON jobs
    USING (client_id = current_setting('app.client_id', true));

CREATE POLICY tree_nodes_client_policy ON tree_nodes
    USING (client_id = current_setting('app.client_id', true));
```

---

## Резервное копирование

### Ручной бэкап

```bash
pg_dump -h db.xxx.supabase.co -U postgres -d postgres \
    --data-only \
    -t jobs -t job_files -t job_settings \
    -t tree_nodes -t node_files \
    > backup_$(date +%Y%m%d).sql
```

### Восстановление

```bash
psql -h db.xxx.supabase.co -U postgres -d postgres \
    < backup_20250120.sql
```

---

## Мониторинг

### Размер таблиц

```sql
SELECT
    relname as table_name,
    pg_size_pretty(pg_total_relation_size(relid)) as total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
```

### Количество записей

```sql
SELECT
    'jobs' as table_name, COUNT(*) FROM jobs
UNION ALL
SELECT
    'job_files', COUNT(*) FROM job_files
UNION ALL
SELECT
    'tree_nodes', COUNT(*) FROM tree_nodes;
```

### Медленные запросы

```sql
SELECT
    query,
    calls,
    mean_exec_time,
    total_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
```
