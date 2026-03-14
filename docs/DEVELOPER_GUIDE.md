# Руководство разработчика

## Быстрый старт

### Установка зависимостей

```bash
pip install -r requirements.txt
```

### Настройка окружения

Создайте файл `.env` в корне проекта:

```env
# Минимальная конфигурация
REMOTE_OCR_BASE_URL=http://localhost:8000
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_anon_key
OPENROUTER_API_KEY=sk-or-...

# R2 Storage (для промптов и результатов)
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY_ID=your_access_key
R2_SECRET_ACCESS_KEY=your_secret_key
R2_BUCKET_NAME=rd1
```

### Запуск приложения

```bash
python app/main.py
```

---

## Работа с кодом

### Создание нового блока разметки

```python
from rd_core.models import Block, BlockType, BlockSource, ShapeType

# Создание прямоугольного текстового блока
block = Block.create(
    page_index=0,
    coords_px=(100, 200, 500, 400),  # x1, y1, x2, y2
    page_width=1600,
    page_height=2400,
    block_type=BlockType.TEXT,
    source=BlockSource.USER,
    shape_type=ShapeType.RECTANGLE
)

# Создание полигонального блока
polygon_block = Block.create(
    page_index=0,
    coords_px=(100, 200, 500, 400),
    page_width=1600,
    page_height=2400,
    block_type=BlockType.IMAGE,
    source=BlockSource.USER,
    shape_type=ShapeType.POLYGON,
    polygon_points=[(100, 200), (500, 200), (500, 400), (100, 400)]
)
```

### Работа с PDF

```python
from rd_core.pdf_utils import PDFDocument

# Context manager
with PDFDocument("document.pdf") as pdf:
    # Рендеринг страницы в изображение
    image = pdf.render_page(0)  # PIL.Image

    # Размеры страницы
    width, height = pdf.get_page_dimensions(0)

    # Количество страниц
    print(f"Страниц: {pdf.page_count}")

# Извлечение текста из области
from rd_core.pdf_utils import extract_text_pdfplumber

text = extract_text_pdfplumber(
    pdf_path="document.pdf",
    page_index=0,
    bbox=(100, 200, 500, 400)  # В PDF-координатах
)
```

### Remote OCR Client

```python
from app.ocr_client import RemoteOCRClient

client = RemoteOCRClient()

# Проверка доступности сервера
if client.health():
    print("Сервер доступен")

# Создание задачи
job = client.create_job(
    pdf_path="document.pdf",
    selected_blocks=blocks,
    task_name="OCR Task",
    engine="openrouter",
    text_model="qwen/qwen3-vl-30b-a3b-instruct"
)
print(f"Задача создана: {job.id}, статус: {job.status}")

# Polling статуса
import time
while job.status in ("queued", "processing"):
    time.sleep(5)
    job = client.get_job(job.id)
    print(f"Прогресс: {job.progress * 100:.0f}%")

# Скачивание результата
if job.status == "done":
    client.download_result(job.id, "result.zip")

# Создание черновика (без OCR)
from rd_core.models import Document
draft = client.create_draft(
    pdf_path="document.pdf",
    annotation_document=document,
    task_name="Draft"
)

# Запуск черновика на OCR
client.start_job(draft.id, engine="openrouter")

# Управление задачей
client.pause_job(job.id)
client.resume_job(job.id)
client.restart_job(job.id)
client.delete_job(job.id)
```

### Tree Client

```python
from app.tree_client import TreeClient, NodeType

client = TreeClient()

# Проверка доступности Supabase
if not client.is_available():
    print("Supabase недоступен")

# Получение корневых проектов
projects = client.get_root_nodes()

# Создание иерархии (v2: folder | document)
project = client.create_node(
    node_type=NodeType.FOLDER,
    name="Новый проект"
)

section = client.create_node(
    node_type=NodeType.FOLDER,
    name="Архитектурные решения",
    parent_id=project.id,
    code="АР"
)

# Добавление документа (с автоверсионированием)
doc = client.add_document(
    parent_id=section.id,
    name="План этажа.pdf",
    r2_key="documents/plan.pdf",
    file_size=1234567,
    local_path="C:/path/to/file.pdf"
)

# Lazy loading дочерних узлов
children = client.get_children(project.id)

# Обновление и удаление
client.update_node(doc.id, name="Новое имя")
client.delete_node(folder.id)  # Каскадное удаление
```

### R2 Storage

**Cloudflare R2** — S3-совместимое хранилище для файлов.

**Настройка (.env):**
```env
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY_ID=your_access_key
R2_SECRET_ACCESS_KEY=your_secret_key
R2_BUCKET_NAME=rd1
R2_PUBLIC_URL=https://pub-xxxxx.r2.dev
```

**Использование:**
```python
from rd_core.r2_storage import R2Storage

r2 = R2Storage()

# Загрузка/скачивание файлов
r2.upload_file("local/file.pdf", "remote/path/file.pdf")
r2.download_file("remote/path/file.pdf", "local/downloaded.pdf")

# Работа с текстом
r2.upload_text("Hello!", "prompts/test.txt")
content = r2.download_text("prompts/test.txt")

# Список объектов
keys = r2.list_objects(prefix="prompts/")

# Presigned URL (временная ссылка для скачивания)
url = r2.generate_presigned_url("results/file.zip", expiration=3600)

# Удаление
r2.delete_object("remote/path/file.pdf")

# Загрузка директории
r2.upload_directory("output/results", "ocr_results/project1")
```

**Структура bucket:**
```
rd1/
├── prompts/           # Промпты OCR (text.json, table.json, image.json)
├── ocr_jobs/          # Временные файлы задач
│   └── {job_id}/
│       ├── document.pdf
│       ├── blocks.json
│       ├── result.md
│       └── crops/
└── tree_docs/         # Документы из Tree Projects
    └── {node_id}/
```

**Промпты OCR:**
Промты хранятся в `prompts/` и редактируются через GUI (`Settings → Edit Prompts`).
Формат: `{"system": "...", "user": "..."}`

### OCR движки

```python
from rd_core.ocr import create_ocr_engine
from PIL import Image

# OpenRouter (VLM модели)
engine = create_ocr_engine(
    backend="openrouter",
    api_key="sk-or-...",
    model_name="qwen/qwen3-vl-30b-a3b-instruct"
)

image = Image.open("page.png")
text = engine.recognize(
    image,
    prompt={
        "system": "You are an OCR expert...",
        "user": "Extract text from this image."
    }
)

# Datalab (Marker API)
engine = create_ocr_engine(
    backend="datalab",
    api_key="..."
)

# Chandra (LM Studio, через ngrok)
engine = create_ocr_engine(
    backend="chandra",
    base_url="https://xxx.ngrok-free.app"
)

# Qwen (LM Studio, два режима)
engine = create_ocr_engine(
    backend="qwen",
    base_url="https://xxx.ngrok-free.app",
    mode="text"   # или mode="stamp" для штампов
)

# JSON mode (автоопределение или явный)
result = engine.recognize(image, prompt={"user": "Return JSON..."}, json_mode=True)
```

---

## Расширение GUI

### Добавление нового Mixin

```python
# app/gui/my_feature.py
class MyFeatureMixin:
    def _setup_my_feature(self):
        """Вызывается в MainWindow.__init__"""
        pass

    def my_action(self):
        """Действие"""
        if not self.pdf_document:
            return
        # ...

# app/gui/main_window.py
from app.gui.my_feature import MyFeatureMixin

class MainWindow(MyFeatureMixin, MenuSetupMixin, ..., QMainWindow):
    def __init__(self):
        # ...
        self._setup_my_feature()
```

### Добавление пункта меню

```python
# app/gui/menu_setup.py
def _setup_menu(self):
    # ...
    tools_menu = menubar.addMenu("&Инструменты")

    my_action = QAction("Моя функция", self)
    my_action.setShortcut("Ctrl+M")
    my_action.triggered.connect(self._my_action_handler)
    tools_menu.addAction(my_action)
```

### Создание диалога

```python
# app/gui/my_dialog.py
from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QLineEdit

class MyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Мой диалог")

        layout = QVBoxLayout(self)

        self.input = QLineEdit()
        layout.addWidget(self.input)

        btn = QPushButton("OK")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

    def get_value(self) -> str:
        return self.input.text()

# Использование
dialog = MyDialog(self)
if dialog.exec() == QDialog.Accepted:
    value = dialog.get_value()
```

### Добавление сигнала в PageViewer

```python
# app/gui/page_viewer.py
class PageViewer(QGraphicsView):
    # Добавить сигнал
    my_signal = Signal(str, int)

    def some_method(self):
        # Эмитировать сигнал
        self.my_signal.emit("data", 42)

# main_window.py
self.page_viewer.my_signal.connect(self._handle_my_signal)

def _handle_my_signal(self, data: str, value: int):
    print(f"Received: {data}, {value}")
```

---

## Тестирование

### Структура тестов

```
tests/
├── test_models.py
├── test_pdf_utils.py
├── test_cropping.py
├── test_remote_ocr_client.py
├── test_tree_client.py
└── conftest.py  # fixtures
```

### Пример теста

```python
# tests/test_models.py
import pytest
from rd_core.models import Block, BlockType, BlockSource

def test_block_create():
    block = Block.create(
        page_index=0,
        coords_px=(100, 100, 200, 200),
        page_width=1000,
        page_height=1000,
        block_type=BlockType.TEXT,
        source=BlockSource.USER
    )

    assert block.page_index == 0
    assert block.coords_norm == (0.1, 0.1, 0.2, 0.2)
    assert block.block_type == BlockType.TEXT

def test_block_serialization():
    block = Block.create(...)
    data = block.to_dict()
    restored = Block.from_dict(data)

    assert restored.id == block.id
    assert restored.coords_px == block.coords_px
```

### Запуск тестов

```bash
pytest tests/ -v
pytest tests/test_models.py -v
pytest --cov=rd_core --cov-report=html
```

---

## Отладка

### Включение DEBUG логирования

```python
# app/main.py
setup_logging(log_level=logging.DEBUG)
```

### Логирование в модуле

```python
import logging
logger = logging.getLogger(__name__)

def my_function():
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning")
    logger.error("Error", exc_info=True)
```

### Просмотр логов

```bash
tail -f logs/app.log
```

### Qt Debug

```python
from PySide6.QtCore import qDebug, qWarning
qDebug("Debug message")
qWarning("Warning message")
```

---

## Сборка

### PyInstaller

```bash
python build.py
```

Конфигурация в `PDFAnnotationTool.spec`:

```python
a = Analysis(
    ['app/main.py'],
    pathex=[],
    datas=[],
    hiddenimports=['rd_core', 'app.gui'],
    ...
)
```

### Результат

```
dist/
└── PDFAnnotation.exe
```

---

## Стиль кода

### Форматирование

- **Black** для форматирования
- **isort** для сортировки импортов
- **flake8** для линтинга

```bash
black app/ rd_core/
isort app/ rd_core/
flake8 app/ rd_core/
```

### Docstrings

```python
def my_function(arg1: str, arg2: int = 10) -> bool:
    """
    Краткое описание функции.

    Args:
        arg1: Описание первого аргумента
        arg2: Описание второго аргумента (по умолчанию 10)

    Returns:
        True если успешно, False при ошибке

    Raises:
        ValueError: если arg1 пустой
    """
    pass
```

### Type Hints

```python
from typing import Optional, List, Dict, Tuple, Union

def process_blocks(
    blocks: List[Block],
    options: Optional[Dict[str, str]] = None
) -> Tuple[int, int]:
    ...
```

---

## Частые проблемы

### R2 Storage не работает

1. Проверьте `.env` файл
2. Убедитесь что bucket существует
3. Проверьте права доступа API ключей

### Supabase недоступен

1. Проверьте `SUPABASE_URL` и `SUPABASE_KEY`
2. Убедитесь что таблицы созданы (см. `tree_schema.sql`)
3. Проверьте RLS политики

### OCR не работает

1. Проверьте `OPENROUTER_API_KEY` или `DATALAB_API_KEY`
2. Проверьте баланс на OpenRouter
3. Увеличьте таймаут для больших изображений

### Celery worker не стартует

1. Проверьте Redis: `redis-cli ping`
2. Проверьте `REDIS_URL` в env
3. Запустите с verbose: `celery -A server.celery_app worker -l debug`

### Большие PDF не рендерятся

- PyMuPDF автоматически снижает zoom для больших страниц
- Лимит: ~400 млн пикселей
- Проверьте `logs/app.log` на предупреждения

---

## Полезные команды

```bash
# Запуск клиента
python app/main.py

# Remote OCR сервер
docker compose -f docker-compose.remote-ocr.dev.yml up

# Только API без воркера
uvicorn services.remote_ocr.server.main:app --reload

# Только воркер
celery -A services.remote_ocr.server.celery_app worker -l info

# Экспорт схемы БД
# (через supabase-py или pg_dump)

# Проверка API
curl http://localhost:8000/health
curl http://localhost:8000/jobs

# Сборка
python build.py
```
