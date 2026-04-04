# ============================================================
# RD-OCR Server: FastAPI + LM Studio (llmster) + Web UI
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
    supervisor curl wget libatomic1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Альтернатива python → python3.11
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

# Установка llmster (headless LM Studio)
RUN curl -fsSL https://lmstudio.ai/install.sh | bash \
    && ln -s /root/.lmstudio/bin/lms /usr/local/bin/lms
ENV PATH="/root/.lmstudio/bin:${PATH}"

WORKDIR /app

# Python зависимости
COPY services/remote_ocr/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Копируем backend код
COPY rd_core/ /app/rd_core/
COPY services/ /app/services/
COPY pyproject.toml /app/
RUN pip install --no-cache-dir -e . 2>/dev/null || true

# Копируем собранный frontend
COPY --from=frontend-build /app/web/dist /app/web/dist

# Конфигурация supervisor
COPY deploy/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Entrypoint: запускает lms daemon, затем supervisor
COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000 1234

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
