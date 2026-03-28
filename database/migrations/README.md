# Database Migrations

## Текущее состояние

`prod.sql` — полный дамп схемы Supabase (включая auth-таблицы). Это НЕ инкрементальная миграция.

## Структура версионирования

Новые миграции создаются в формате:

```
V001__initial_schema.sql        # Первоначальная схема (jobs, tree_nodes, node_files)
V002__add_job_settings.sql      # Добавление таблицы job_settings
V003__description.sql           # Следующие изменения
```

## Правила

1. Каждый файл — одна атомарная миграция
2. Имя: `V{NNN}__{description}.sql` (двойное подчёркивание)
3. Миграции идемпотентны: `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
4. Не менять существующие миграции после применения на production
5. `prod.sql` сохраняется как reference dump, не используется для миграций
