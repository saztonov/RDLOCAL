# Документация Core Structure

Это минимальный набор актуальной документации для разработчика. Подробности, которые легко восстановить по коду, намеренно не дублируются.

## Основные документы

- [../README.md](../README.md) — входная точка: запуск, `.env`, карта репозитория.
- [ARCHITECTURE.md](ARCHITECTURE.md) — архитектура desktop, local OCR и Remote OCR.
- [REMOTE_OCR_SERVER.md](REMOTE_OCR_SERVER.md) — запуск и устройство серверного режима.
- [DATABASE.md](DATABASE.md) — ключевые таблицы, источник правды по схеме и процесс обновления.

## С чего начать

- Если нужен onboarding по проекту: откройте [../README.md](../README.md).
- Если нужно понять связи между подсистемами: откройте [ARCHITECTURE.md](ARCHITECTURE.md).
- Если работаете с FastAPI/Celery: откройте [REMOTE_OCR_SERVER.md](REMOTE_OCR_SERVER.md).
- Если меняете Supabase-схему: откройте [DATABASE.md](DATABASE.md).
