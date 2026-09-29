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
COPY ui ./ui
COPY scripts ./scripts
RUN pip install --no-deps .

# Один образ на два сервиса: API с исполнителем очереди и интерфейс Streamlit.
# Команду задаёт docker-compose.yml.
EXPOSE 8010 8501
CMD ["uvicorn", "pelican.api:app", "--host", "0.0.0.0", "--port", "8010"]
