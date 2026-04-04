# Карта работы RDLOCAL

Этот документ фиксирует текущую рабочую карту всего стека RDLOCAL по состоянию репозитория: `desktop GUI + local OCR + remote OCR server + Supabase/R2 + shared core`.

Цель документа не перечислить папки, а показать:
- какие подсистемы реально участвуют в работе;
- как проходят основные пользовательские и системные сценарии;
- где формируются документы и побочные артефакты;
- где сосредоточены узкие места, избыточная связность и дублирование.

## Что считать каноном

- Канонический пользовательский сценарий для продукта: tree-backed документ, открытый из дерева проектов, с аннотацией в Supabase и OCR-артефактами в R2.
- Канонический источник разметки: таблица `annotations`, а не sidecar JSON рядом с PDF.
- Канонический каталог OCR-артефактов: префикс документа в R2 плюс записи в `node_files`.
- Local OCR не является отдельным доменным контуром: он переиспользует общий OCR-код и значимую часть серверной постобработки.
- Remote OCR остаётся полным production-контуром: API, очередь, воркер, R2, Supabase и регистрация результатов.

## Слой 1. Ландшафт системы

| Подсистема | Основные точки кода | Роль | Читает | Пишет | Основной риск |
| --- | --- | --- | --- | --- | --- |
| Desktop shell и состояние окна | [`app/main.py`](../app/main.py), [`app/gui/main_window.py`](../app/gui/main_window.py), [`app/gui/panels_setup.py`](../app/gui/panels_setup.py) | Поднимает Qt, собирает `MainWindow`, хранит текущий документ, страницу, temp-сессию, undo/redo, OCR panel | `.env`, `QSettings`, текущее состояние документа | UI state, temp cleanup, status bar, dock layout | Большая mixin-композиция затрудняет трассировку жизненного цикла |
| Дерево проектов и file I/O | [`app/gui/project_tree/widget.py`](../app/gui/project_tree/widget.py), [`app/gui/file_download.py`](../app/gui/file_download.py), [`app/gui/file_operations.py`](../app/gui/file_operations.py) | Навигация по `tree_nodes`, скачивание PDF и sidecar-файлов, открытие tree-backed документа через temp workspace | `tree_nodes`, `node_files`, R2, temp workspace | temp workspace, текущий `node_id`, lock state, локальный открытый PDF | UI-слой одновременно навигирует, скачивает, открывает и частично обновляет storage-состояние |
| Модель аннотаций и сохранение | [`rd_core/models/document.py`](../rd_core/models/document.py), [`rd_core/annotation_io.py`](../rd_core/annotation_io.py), [`app/annotation_db.py`](../app/annotation_db.py), [`app/gui/annotation_cache.py`](../app/gui/annotation_cache.py) | Представление `Document/Page/Block`, миграция legacy-форматов, асинхронный autosave в Supabase | JSON-структуры аннотаций, `annotations`, in-memory `Document` | `annotations`, dirty-cache, флаги `has_annotation` | Сразу несколько точек записи в одну и ту же сущность |
| Оркестрация OCR в GUI | [`app/gui/remote_ocr/jobs_controller.py`](../app/gui/remote_ocr/jobs_controller.py), [`app/ocr/local_runner.py`](../app/ocr/local_runner.py) | Выбор режима, correction mode, запуск local/remote OCR, polling, применение результатов обратно в документ | `annotation_document`, `jobs`, server API, local subprocess queue | local jobs, remote requests, merged OCR fields, snapshot jobs | Один контроллер совмещает UI, orchestration и result-merge |
| Local OCR pipeline | [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py) | Pass1/pass2 OCR, model swap, verification, генерация файлов результатов, sync обратно в tree storage | PDF, `blocks_data`, shared OCR backends, server `pdf_twopass` | `annotation.json`, HTML, MD, export report, R2/node_files | Толстый модуль с высокой связностью и множеством обязанностей |
| Remote OCR API и worker | [`services/remote_ocr/server/main.py`](../services/remote_ocr/server/main.py), [`services/remote_ocr/server/routes/jobs/create_handler.py`](../services/remote_ocr/server/routes/jobs/create_handler.py), [`services/remote_ocr/server/tasks.py`](../services/remote_ocr/server/tasks.py), [`services/remote_ocr/server/job_stages.py`](../services/remote_ocr/server/job_stages.py) | Принимает задания, создаёт `jobs`, запускает Celery-пайплайн, публикует результаты | `jobs`, `job_files`, `job_settings`, R2 PDF/blocks, `annotations` | `jobs`, `job_settings`, `job_files`, `annotations`, R2 artifacts, `node_files` | Переходы состояния распределены между route/storage/task-слоями |
| Shared core | [`rd_core/ocr`](../rd_core/ocr), [`rd_core/pdf_utils.py`](../rd_core/pdf_utils.py), [`rd_core/sidecar_resolver.py`](../rd_core/sidecar_resolver.py), [`rd_core/models`](../rd_core/models) | Общие модели, OCR backends, HTML/MD генераторы, PDF утилиты, sidecar compatibility | PDF geometry, OCR raw output, block ids | HTML/MD content, normalized coords, resolved sidecar keys | Legacy-совместимость протекает в оба runtime-контура |
| Storage и DB слой | [`app/tree_client`](../app/tree_client), [`rd_core/r2_storage.py`](../rd_core/r2_storage.py), [`services/remote_ocr/server/storage_jobs.py`](../services/remote_ocr/server/storage_jobs.py), [`services/remote_ocr/server/node_storage/ocr_registry.py`](../services/remote_ocr/server/node_storage/ocr_registry.py) | Доступ к Supabase и R2, регистрация файлов, очередь jobs, pdf status | Таблицы `tree_nodes`, `node_files`, `annotations`, `jobs`, `job_files`, `job_settings`, объекты R2 | Те же таблицы и R2-объекты | Один и тот же storage управляется и GUI, и сервером через разные клиенты |

Короткий вывод по ландшафту: система уже не делится на “GUI” и “сервер” как на независимые продукты. Это один стек с двумя оркестраторами поверх общего ядра и общей persistence-модели.

## Слой 2. Карта runtime-потоков

### 1. Открытие локального PDF

1. Любой локальный open-flow в итоге приходит в [`app/gui/file_operations.py`](../app/gui/file_operations.py) через `FileOperationsMixin._open_pdf_file()`.
2. Перед открытием окно делает `flush` предыдущей аннотации и, если до этого был tree-backed документ, удаляет старую temp-сессию.
3. [`rd_core/pdf_utils.py`](../rd_core/pdf_utils.py) через `PDFDocument` открывает PDF и даёт размеры/страницы для viewer.
4. Если `_current_node_id` пуст, Supabase пропускается и создаётся пустой `Document`; если контекст всё ещё node-backed, приложение пробует взять аннотацию из [`app/annotation_db.py`](../app/annotation_db.py).
5. Аннотация канонизируется под реальные preview-размеры, затем `MainWindow` рендерит текущую страницу и обновляет blocks tree.
6. OCR preview и статистика обновляются уже поверх in-memory `annotation_document`.

### 2. Открытие tree-backed документа через temp workspace

1. [`app/gui/project_tree/widget.py`](../app/gui/project_tree/widget.py) при double-click на документе вызывает `document_selected.emit(node_id, r2_key)`.
2. [`app/gui/panels_setup.py`](../app/gui/panels_setup.py) связывает этот сигнал с `FileDownloadMixin._on_tree_document_selected()`.
3. [`app/gui/file_download.py`](../app/gui/file_download.py) создаёт temp workspace через [`app/gui/temp_session.py`](../app/gui/temp_session.py), собирает задачи на скачивание PDF и sidecar-файлов из `node_files`, и запускает `FileTransferWorker`.
4. После загрузки окно выставляет `_current_node_id`, `_current_r2_key`, lock state, origin=`tree_temp` и переходит в тот же `_open_pdf_file()`.
5. Внутри `_open_pdf_file()` источник правды для разметки остаётся прежним: приложение загружает аннотацию из Supabase, а скачанные `_ocr.html` / `_document.md` выступают как дополнительные sidecar-артефакты.
6. Логи для tree-backed документа переключаются на projects folder, а не в temp, чтобы cleanup workspace не терял историю.

### 3. Редактирование блоков и autosave аннотаций

1. Пользовательские действия в [`app/gui/page_viewer.py`](../app/gui/page_viewer.py) и block mixin-слоях меняют `MainWindow.annotation_document`.
2. `FileAutoSaveMixin._auto_save_annotation()` кладёт актуальный `Document` в глобальный [`app/gui/annotation_cache.py`](../app/gui/annotation_cache.py) и помечает `node_id` как dirty.
3. `AnnotationCache` по таймеру запускает асинхронный `AnnotationDBIO.save_to_db()`, а перед OCR и при закрытии использует синхронные `flush_for_ocr()`, `force_sync()`, `force_sync_all()`.
4. После успешной синхронизации UI обновляет `has_annotation` и пересчитывает `pdf_status` через прямые вызовы `TreeClient` и `R2Storage`.
5. Результат: редактирование остаётся быстрым, но логика persistence и логика tree/status обновления жёстко сцеплены прямо в GUI.

### 4. Запуск local OCR

1. [`app/gui/remote_ocr/jobs_controller.py`](../app/gui/remote_ocr/jobs_controller.py) в `create_job()` собирает блоки, определяет smart/full режим через `needs_ocr()`, при необходимости очищает старые OCR-поля и сохраняет correction markers.
2. Перед стартом controller делает `flush` autosave и синхронно сохраняет текущую аннотацию в Supabase.
3. `JobsController._local_create_job()` передаёт работу в [`app/ocr/local_runner.py`](../app/ocr/local_runner.py), а тот поднимает отдельный `multiprocessing.Process`.
4. В subprocess вызывается [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py): parse blocks, create backends, `pass1_prepare_crops()`, `pass2_ocr_from_manifest_async()`, verification и генерация локальных артефактов.
5. `_generate_local_results()` пишет `annotation.json`, `{stem}_ocr.html`, `{stem}_document.md`, `{stem}_export_report.json`, а при наличии `node_id` ещё синхронизирует HTML/MD/crops в `tree_docs/{node_id}` и `node_files`.
6. После завершения `auto_download_result()` локально перечитывает `annotation.json`, мерджит OCR-поля по `block.id` обратно в открытый документ и снова запускает autosave.

### 5. Запуск remote OCR

1. Тот же `JobsController.create_job()` готовит аннотацию и correction flags, но для tree-backed документа идёт по `JobsController._server_create_job()`, а не по legacy upload-flow.
2. Клиент отправляет лёгкий POST в `/jobs/node`, где [`services/remote_ocr/server/routes/jobs/create_handler.py`](../services/remote_ocr/server/routes/jobs/create_handler.py) поднимает `jobs`, `job_settings`, делает snapshot blocks в R2 и регистрирует `job_files`.
3. Если используется legacy remote flow `/jobs`, route дополнительно загружает PDF в R2; node-backed flow берёт PDF по `node_id`.
4. Celery вызывает [`services/remote_ocr/server/tasks.py`](../services/remote_ocr/server/tasks.py), а дальше stages в [`services/remote_ocr/server/job_stages.py`](../services/remote_ocr/server/job_stages.py): `bootstrap_job()` -> `run_ocr()` -> `generate_and_upload()` -> `register_results()` -> `finalize()`.
5. `bootstrap_job()` скачивает PDF и blocks snapshot из R2, flatten-ит `pages -> blocks`, фильтрует correction subset и создаёт OCR backends.
6. [`services/remote_ocr/server/task_results.py`](../services/remote_ocr/server/task_results.py) генерирует HTML/MD, enrich-ит аннотацию, делает verification retry и сохраняет enriched annotation обратно в Supabase.
7. [`services/remote_ocr/server/task_upload.py`](../services/remote_ocr/server/task_upload.py) выкладывает HTML/MD/crops в R2 и регистрирует `job_files`, а [`services/remote_ocr/server/node_storage/ocr_registry.py`](../services/remote_ocr/server/node_storage/ocr_registry.py) зеркалит результат в `node_files` и обновляет `pdf_status`.

### 6. Применение OCR-результатов обратно в документ

1. Local branch: `JobsController.auto_download_result()` сразу идёт в `_reload_annotation_from_result(output_dir)`.
2. Remote branch: polling замечает terminal status, запускает `_remote_download_result()` и читает итоговую аннотацию обратно из Supabase.
3. Обе ветки строят словарь `block_id -> OCR fields`, мерджат `ocr_text/ocr_html/ocr_json/ocr_meta` в текущий `annotation_document` и снимают `is_correction`.
4. После merge окно делает `_render_current_page()`, обновляет blocks tree, preview widgets и OCR stats.
5. Если обновления были, включается `_auto_save_annotation()`, чтобы in-memory документ и Supabase снова синхронизировались.
6. Ключевая опора всего цикла: стабильный `block.id`; ни local, ни remote flow не заменяют документ целиком, они мерджат поля на уровне блока.

### 7. Correction mode и legacy fallback

1. Smart OCR определяется через [`rd_core/ocr_block_status.py`](../rd_core/ocr_block_status.py): controller распознаёт только блоки, которым действительно нужен OCR.
2. Local correction mode отправляет subset блоков в OCR, а потом в [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py) вшивает новые OCR-поля обратно в `full_blocks_data`, чтобы HTML/MD строились по полному документу.
3. Remote correction mode в [`services/remote_ocr/server/task_results.py`](../services/remote_ocr/server/task_results.py) загружает существующий enriched annotation из Supabase и обновляет только correction-блоки.
4. Если existing annotation для remote correction не найден, сервер откатывается к full generation.
5. Legacy совместимость держится сразу в нескольких местах: `migrate_flat_to_structured()`, `migrate_annotation_data()`, old remote upload path, ручная миграция legacy JSON из дерева и resolver порядка `node_files -> tree_docs/{node_id} -> pdf_parent`.
6. Итог: система одновременно поддерживает новый node-backed контур и несколько старых схем хранения, что повышает цену любого изменения.

## Слой 3. Карта документов и артефактов

| Артефакт | Источник данных | Генератор | Где хранится | Кто читает дальше | Комментарий |
| --- | --- | --- | --- | --- | --- |
| `annotations` (`data` по `node_id`) | in-memory `Document`, user edits, OCR enrichment | [`app/annotation_db.py`](../app/annotation_db.py) `AnnotationDBIO.save_to_db()`, [`services/remote_ocr/server/node_storage/ocr_registry.py`](../services/remote_ocr/server/node_storage/ocr_registry.py) `_save_annotation_to_db()` | Supabase `annotations` | `FileOperationsMixin._load_annotation_if_exists()`, remote result apply, compatibility/status операции | Это канонический источник разметки |
| `annotation.json` | pages/blocks после local OCR и optional full merge | [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py) `_generate_local_results()` | Локальный `output_dir` | `JobsController._reload_annotation_from_result()`, локальный post-run merge | Канонический sidecar только для local pipeline |
| `{stem}_annotation.json` | snapshot blocks, взятый из Supabase или upload | [`services/remote_ocr/server/routes/jobs/create_handler.py`](../services/remote_ocr/server/routes/jobs/create_handler.py) через `blocks_key()` | R2 + `job_files` | `bootstrap_job()` через `download_job_files()` | Это входной snapshot job, а не финальная enriched annotation |
| `{stem}_ocr.html` | OCR blocks + regenerated `ocr_html` fragments | Local: [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py); Remote: [`services/remote_ocr/server/task_results.py`](../services/remote_ocr/server/task_results.py) + [`services/remote_ocr/server/task_upload.py`](../services/remote_ocr/server/task_upload.py) | Local `output_dir`; R2 префикс документа; `node_files`; `job_files` | Unified files dialog, block verification, sidecar resolver, пользователи | В remote work dir сначала живёт как `ocr_result.html`, затем переименовывается при upload |
| `{stem}_document.md` | OCR blocks + regenerated markdown view | Local: [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py); Remote: [`services/remote_ocr/server/task_results.py`](../services/remote_ocr/server/task_results.py) + `task_upload.py` | Local `output_dir`; R2 префикс документа; `node_files`; `job_files` | Viewer/dialogs, LLM-oriented export use-cases | В remote work dir сначала живёт как `document.md` |
| `{stem}_export_report.json` | `html_stats` и `md_stats` local export | [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py) `_generate_local_results()` | Только local `output_dir` | Пользователь/ручной анализ | На R2 и в `node_files` не зеркалится |
| `crops/{block_id}.pdf` | PASS1 crops и copied final PDF crops | [`services/remote_ocr/server/pdf_twopass`](../services/remote_ocr/server/pdf_twopass), `copy_crops_to_final()` в [`services/remote_ocr/server/task_upload.py`](../services/remote_ocr/server/task_upload.py) и [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py) | Temp work dir, затем R2, `node_files`, `job_files` | HTML crop links, unified files dialog, verification tooling | Stamp crops специально исключаются из итоговой публикации |
| `jobs` | OCR request metadata, progress, status, block stats | Local: [`app/ocr/local_runner.py`](../app/ocr/local_runner.py) `_save_job_to_supabase()`; Remote: [`services/remote_ocr/server/storage_jobs.py`](../services/remote_ocr/server/storage_jobs.py) | Supabase `jobs` | Jobs panel, remote poller, snapshot/history loaders | Таблица уже общая для local и remote режимов |
| `job_settings` | engine и correction flags job-а | [`services/remote_ocr/server/routes/jobs/create_handler.py`](../services/remote_ocr/server/routes/jobs/create_handler.py) -> `save_job_settings()` | Supabase `job_settings` | `bootstrap_job()`, `task_results.generate_results()` | Server-only артефакт |
| `job_files` | PDF/blocks snapshot и загруженные OCR-файлы | create handlers + [`services/remote_ocr/server/task_upload.py`](../services/remote_ocr/server/task_upload.py) | Supabase `job_files` | `download_job_files()`, job inspection | Local mode не использует `job_files` |
| `node_files` | PDF документа и опубликованные OCR-артефакты | GUI: [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py) `_sync_results_to_tree()`; Server: [`services/remote_ocr/server/node_storage/ocr_registry.py`](../services/remote_ocr/server/node_storage/ocr_registry.py) | Supabase `node_files` | Tree download, sidecar resolver, unified files dialog, pdf status | Это канонический регистр файлов документа в tree-backed сценарии |

Короткий вывод по артефактам: для node-backed сценария финальная разметка живёт в `annotations`, а не в R2 sidecar JSON. R2 нужен прежде всего для HTML/MD/crops и для входных job snapshots.

### Важное расхождение по sidecar-артефактам

- Репозиторий до сих пор содержит заметное количество ссылок на `_result.json` и local/legacy `annotation.json`.
- В examined current paths local OCR действительно пишет `annotation.json`, но server-side result generation делает ставку на enriched annotation в Supabase плюс HTML/MD/crops в R2.
- Иными словами, часть кода уже живёт в модели `DB + R2 files`, а часть всё ещё ожидает sidecar JSON как полноценный продуктовый артефакт.

## Слой 4. Узкие места

| Ранг | Узкое место | Где видно | Почему это bottleneck | Практический эффект |
| --- | --- | --- | --- | --- |
| 1 | `JobsController` как orchestration god object | [`app/gui/remote_ocr/jobs_controller.py`](../app/gui/remote_ocr/jobs_controller.py) | Один файл совмещает режимы local/remote, correction logic, polling, snapshot persistence, create job, auto-download и merge результатов | Любое изменение OCR-флоу требует править один перегруженный модуль и проверять оба режима |
| 2 | Синхронные Supabase/R2 вызовы из GUI | [`app/gui/file_operations.py`](../app/gui/file_operations.py), [`app/gui/file_download.py`](../app/gui/file_download.py), [`app/gui/file_auto_save.py`](../app/gui/file_auto_save.py), [`app/gui/project_tree/widget.py`](../app/gui/project_tree/widget.py) | GUI-слой напрямую создаёт `TreeClient()` и `R2Storage()` и делает сетевые операции вне общего service boundary | Сложнее тестировать, выше шанс UI-latency и дублирования error/caching логики |
| 3 | Тяжёлая OCR post-processing стадия | [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py), [`services/remote_ocr/server/task_results.py`](../services/remote_ocr/server/task_results.py), [`services/remote_ocr/server/block_verification.py`](../services/remote_ocr/server/block_verification.py) | Генерация артефактов, verification retry, model swap, upload и sync с persistence смешаны в одном критическом пути | Долгий latency path, дорого разбирать partial failures, тяжело локально профилировать |
| 4 | Legacy-ветки и storage drift | [`rd_core/annotation_io.py`](../rd_core/annotation_io.py), [`rd_core/sidecar_resolver.py`](../rd_core/sidecar_resolver.py), [`app/gui/project_tree/legacy_migration.py`](../app/gui/project_tree/legacy_migration.py), `/_result.json` expectations по репозиторию | Система поддерживает сразу несколько поколений аннотаций и sidecar-схем | Повышается цена изменений и вероятность того, что новый поток сломает старую совместимость |
| 5 | Temp workspace/session churn | [`app/gui/file_download.py`](../app/gui/file_download.py), [`app/gui/temp_session.py`](../app/gui/temp_session.py), [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py), [`services/remote_ocr/server/job_stages.py`](../services/remote_ocr/server/job_stages.py) | Почти все non-trivial сценарии создают временные директории и копируют PDF/crops | Дополнительный I/O, сложный cleanup, неоднозначные “живые” и “временные” файлы для диагностики |
| 6 | Shared tables с несколькими владельцами | `jobs`, `annotations`, `node_files` через GUI и server storage слои | Одна и та же сущность записывается разными клиентами и в разном темпе | Трудно жёстко определить owner-слой и инварианты данных |

### Что это значит на практике

- Самые дорогие для сопровождения изменения лежат не в OCR backend-ах, а в orchestration и persistence glue.
- Узкие места в основном не вычислительные, а организационные: “кто главный источник правды” и “какой слой имеет право писать”.
- Без явного разделения канона и совместимости дрейф артефактов будет продолжаться.

## Слой 5. Карта дублирования

| Поведение | Где живёт сейчас | Почему появилось | Чем рискует | Что считать каноном |
| --- | --- | --- | --- | --- |
| Генерация OCR-результатов | Local: [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py) `_generate_local_results()`; Remote: [`services/remote_ocr/server/task_results.py`](../services/remote_ocr/server/task_results.py) `generate_results()` / `_generate_correction_results()` | Local OCR был построен как desktop-адаптация server pipeline без Celery/HTTP | Исправления в post-processing, naming и verification нужно переносить дважды; дрейф артефактов уже заметен | Доменная логика результата ближе всего к remote `task_results.py`; local pipeline должен быть thin adapter |
| Создание OCR job | GUI: `_server_create_job()` и legacy `_remote_create_job()` в [`jobs_controller.py`](../app/gui/remote_ocr/jobs_controller.py); Server: `create_node_job_handler()` и `create_job_handler()` в [`create_handler.py`](../services/remote_ocr/server/routes/jobs/create_handler.py) | Новый node-backed fast path добавлен поверх старого upload API | Валидация, queue checks и регистрация файлов могут разойтись | Для текущего tree workflow канон: `JobsController._server_create_job()` + `/jobs/node` |
| Загрузка и миграция аннотаций | GUI: [`app/annotation_db.py`](../app/annotation_db.py), local result reload в [`jobs_controller.py`](../app/gui/remote_ocr/jobs_controller.py); Server: [`services/remote_ocr/server/node_storage/ocr_registry.py`](../services/remote_ocr/server/node_storage/ocr_registry.py); Legacy helpers в [`rd_core/annotation_io.py`](../rd_core/annotation_io.py) | GUI нужен `Document`, серверу нужен dict, а старые форматы всё ещё живы | Разные migration/fallback правила и разные HTTP-обвязки для одного и того же ресурса | Канон: `annotations` как source of truth; адаптация к `Document` или raw dict должна быть вторичной |
| Прямой доступ GUI к `TreeClient` и `R2Storage` в обход фасада | [`app/services.py`](../app/services.py) существует, но множество GUI модулей создают клиентов напрямую | Фасад появился позже, когда прямые вызовы уже расползлись по mixin-слоям | Непоследовательные timeout/caching/logging, лишний coupling, сложнее мокать | Для GUI канон должен быть `app.services` |
| Применение OCR-результатов в открытый документ | Local merge: `_reload_annotation_from_result()`; Remote merge: `_on_remote_result_loaded()` в [`jobs_controller.py`](../app/gui/remote_ocr/jobs_controller.py) | Источник результата разный: local filesystem vs Supabase | Могут разойтись правила merge, очистки `is_correction`, preview refresh и autosave | Канон должен быть единый helper `result source -> block field map -> apply to current document` |
| Sidecar naming и старые JSON-ожидания | [`rd_core/sidecar_resolver.py`](../rd_core/sidecar_resolver.py), [`services/remote_ocr/server/r2_keys.py`](../services/remote_ocr/server/r2_keys.py), [`app/gui/project_tree/legacy_migration.py`](../app/gui/project_tree/legacy_migration.py), UI references to `_result.json` | Архитектура переехала с sidecar JSON на `annotations` + R2 files, но совместимость сохранена | Модули продолжают ожидать файлы, которые новые потоки уже не считают основными | Канон: `annotations` + `node_files`/R2 HTML/MD/crops; sidecar JSON — только compatibility layer |

Короткий вывод по дублированию: большая часть дублирования поведенческая, а не текстовая. Код повторяет не строки, а право решать одни и те же задачи в нескольких местах.

## Hotspots, на которые стоит смотреть первыми

| Файл | Размер | Почему hotspot |
| --- | --- | --- |
| [`app/gui/remote_ocr/jobs_controller.py`](../app/gui/remote_ocr/jobs_controller.py) | 1278 строк | Главная точка схождения local/remote OCR, correction mode, polling и result apply |
| [`services/remote_ocr/server/block_verification.py`](../services/remote_ocr/server/block_verification.py) | 654 строки | Скрытая стоимость post-processing и retry логики |
| [`app/ocr/local_pipeline.py`](../app/ocr/local_pipeline.py) | 629 строк | Local OCR orchestration, artefact generation и sync в tree storage |
| [`app/gui/project_tree/widget.py`](../app/gui/project_tree/widget.py) | 422 строки | Tree navigation, locking, context actions, legacy migration |
| [`app/gui/main_window.py`](../app/gui/main_window.py) | 396 строк | Центральный state shell с mixin-boundaries |
| [`services/remote_ocr/server/task_results.py`](../services/remote_ocr/server/task_results.py) | 383 строки | Каноническая бизнес-логика server-side результатов и correction merge |

## Проверка покрытия карты сценариями

- Открытие локального PDF покрыто секцией “Открытие локального PDF”.
- Открытие tree-backed документа через temp workspace покрыто секцией “Открытие tree-backed документа через temp workspace”.
- Чтение, миграция и сохранение аннотаций в Supabase покрыты секциями “Ландшафт системы”, “Редактирование блоков и autosave” и “Карта документов и артефактов”.
- Local OCR путь `GUI -> LocalOcrRunner -> local_pipeline -> result files -> apply back` покрыт секциями “Запуск local OCR” и “Применение OCR-результатов”.
- Remote OCR путь `GUI/API -> jobs -> Celery -> task_results -> node_files/annotations -> apply back` покрыт секциями “Запуск remote OCR” и “Применение OCR-результатов”.
- Correction mode покрыт отдельной секцией и отражён в карте дублирования.
- Legacy sidecar и legacy annotation схемы отражены как compatibility layer, а не как основной продуктовый путь.

## Базовая верификация

По состоянию анализа уже был выполнен `pytest -q` без мутаций кода:

- `237` тестов прошли;
- `3` теста упали;
- два падения связаны с отсутствием `.env.example`;
- одно падение связано с контрактом stamp formatting и ключом `Наименование`.

Это не меняет саму карту, но подтверждает две важные вещи:

- в репозитории есть отдельный пласт документно-конфигурационного долга;
- часть OCR/formatting контрактов уже живёт как зафиксированное поведение тестов, а не только как “ожидание из кода”.

