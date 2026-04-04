FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Зависимости для PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем зависимости (те же, что у remote_ocr)
COPY services/remote_ocr/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Копируем код
COPY rd_core /app/rd_core
COPY services /app/services

# Non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser \
    && mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 18100

CMD ["python", "-m", "services.local_ocr", "--port", "18100"]
