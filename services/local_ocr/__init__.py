"""Local OCR Service — FastAPI-сервер без Celery/Redis.

Лёгкий аналог remote_ocr для работы на локальной машине.
Использует multiprocessing.Process вместо Celery worker.
"""
