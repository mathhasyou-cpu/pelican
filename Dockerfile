# Веб-интерфейс (React + Vite) собирается своей стадией; в образ идёт только web/dist.
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости отдельным слоем: правка кода не переустанавливает их заново.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web /web/dist ./web/dist
COPY scripts ./scripts
RUN pip install --no-deps .

# API с исполнителем очереди и веб-интерфейсом (web/dist, отдаёт тот же uvicorn).
EXPOSE 8010
CMD ["uvicorn", "pelican.api:app", "--host", "0.0.0.0", "--port", "8010"]
