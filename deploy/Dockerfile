# ============================================================
# RD-OCR Server: FastAPI + LM Studio + Web UI
# Multi-stage build: frontend (Node) → runtime (CUDA + Python)
# ============================================================

# ── Stage 1: Build React SPA ────────────────────────────────
FROM node:20-slim AS frontend-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/ ./
RUN npm run build

# ── Stage 2: Runtime ────────────────────────────────────────
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Установка Python 3.11, supervisor, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    supervisor curl wget \
    && rm -rf /var/lib/apt/lists/*

# Альтернатива python → python3.11
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

WORKDIR /app

# Python зависимости
COPY services/remote_ocr/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Копируем backend код
COPY rd_core/ /app/rd_core/
COPY services/ /app/services/
COPY setup.py pyproject.toml /app/
RUN pip install --no-cache-dir -e . 2>/dev/null || true

# Копируем собранный frontend
COPY --from=frontend-build /app/web/dist /app/web/dist

# Конфигурация supervisor
COPY deploy/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# LM Studio: предполагается что lms CLI установлен в образе
# или монтируется как volume. Для production:
# RUN wget -qO- https://lmstudio.ai/install | bash
# Пока используем placeholder — LM Studio ставится вручную или через volume

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
