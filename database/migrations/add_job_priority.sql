-- Миграция: добавление приоритета и celery_task_id в таблицу jobs
-- Для переупорядочивания задач в очереди (кнопки ↑/↓)

ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS celery_task_id TEXT;
