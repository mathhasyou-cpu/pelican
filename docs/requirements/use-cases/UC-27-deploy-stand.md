# UC-27 Развернуть стенд

## 1. Шапка

| Атрибут | Значение |
|---|---|
| ID | UC-27 |
| Название | Развернуть стенд |
| Пакет | F. Эксплуатация |
| Первичный актор | Администратор стенда |
| Вторичные акторы | Docker Compose; сервер моделей LM Studio (хост); Tailscale Funnel; Postgres 18; шард-сервер (хост, необязателен) |
| Приоритет | Must |
| Статус | Реализовано |
| Стабильность | Средняя (размеры корпуса и шардов расходятся в документах — `OQ-13`) |
| Риск | Средний: зависимость от GPU на хосте, объёмов корпуса и внешнего tailnet |
| Источник | README «Требования», «Запуск», «Корпус», «Переменные окружения»; `docker-compose.yml`; `Dockerfile`; `tailscale/serve.json`; техдока §2 |
| Проверено на | `master@1dded3e` |

## 2. Краткое описание

Администратор поднимает АС ПЕЛИКАН одной командой Compose: Postgres с корпусом и `api`
(uvicorn с исполнителем очереди и собранным веб-интерфейсом). Модели работают вне
контейнеров, на хосте, где есть GPU. Для доступа с других
машин стенд публикуется профилем `stand` через Tailscale Funnel (порты приложения — только
loopback хоста); для ускорения поиска по корпусу на хосте запускается нативный шард-сервер. Ценность: воспроизводимый запуск на любой машине с Docker и GPU,
без ручной сборки фронта и ручного применения схемы.

## 3. Предусловия

- Docker с Compose v2 (Docker Desktop на Windows/macOS); VM Docker ≈ 8 ГБ, часть которых
  делит LM Studio на той же машине (`docker-compose.yml`, комментарий к Postgres).
- На хосте запущен OpenAI-совместимый сервер моделей на порту 1234 с моделями
  `google/gemma-4-12b-qat` и `google/embedding-gemma-300m`; GPU ≈ 12 ГБ (README).
- Есть корпус: база Postgres с таблицей `works` (≈15.2 млн работ, ≈14 ГБ) и каталог шардов
  `emb-science/` (≈22 ГБ, `NFR-48`); либо администратор готов к запуску с пустым корпусом
  (A3).
- Файл `.env` создан из `.env.example` и заполнен (`NFR-36`); секреты не попадают в
  репозиторий (`NFR-54`).

## 4. Триггер

Администратор решает поднять стенд на новой машине или пересобрать существующий после
изменения кода.

## 5. Основной поток

1. Администратор копирует `.env.example` в `.env` и заполняет переменные: `POSTGRES_PASSWORD`,
   `LLM_BASE_URL_DOCKER` (по умолчанию `http://host.docker.internal:1234/v1`), `LLM_MODEL`,
   `EMBEDDING_MODEL`, `EMB_HOST_DIR` (каталог шардов на хосте); необязательные: `GITHUB_TOKEN`
   (без него запросы к GitHub идут без заголовка `Authorization`, `weak/live.py`),
   `EPO_OPS_KEY` и `EPO_OPS_SECRET` (без обоих канал патентов выключен, `weak/patents.py`),
   `CONTACT` (`User-Agent` сбора корпуса, UC-20), `OPENALEX_API_KEY` (бюджет сбора, UC-20).
2. Администратор запускает:
   ```bash
   docker compose up -d --build
   ```
3. Система (Compose) собирает один образ в две стадии (`Dockerfile`): `node:24-slim`
   выполняет `npm ci` и `npm run build` → `web/dist`; `python:3.12-slim` ставит
   `requirements.txt` отдельным слоем, копирует `pyproject.toml`, `README.md`, `src`,
   `web/dist`, `scripts` и устанавливает пакет `pip install --no-deps .` (`NFR-37`).
4. Система поднимает `postgres` (`postgres:18`, `shared_buffers=2GB`,
   `effective_cache_size=6GB`, `maintenance_work_mem=2GB`, `work_mem=64MB`, `max_wal_size=8GB`,
   `shm_size 2gb`; том `pgdata` смонтирован в `/var/lib/postgresql`); healthcheck
   `pg_isready` каждые 5 с, до 20 попыток.
5. Система поднимает `api` после `service_healthy` у Postgres: `uvicorn pelican.api:app
   --host 0.0.0.0 --port 8010`; переменные `DATABASE_URL` (внутрь compose),
   `LLM_BASE_URL` ← `LLM_BASE_URL_DOCKER`, `DATA_DIR=/data` (том `appdata`),
   `EMB_DIR=/shards` (bind mount `EMB_HOST_DIR` только на чтение, `NFR-56`),
   `SHARDS_URL` ← `SHARDS_URL_DOCKER` (по умолчанию `http://host.docker.internal:8020`),
   `NEWS_LOCAL_ADDRESS: ""` — привязка Google News к адресу Wi-Fi хоста в контейнере не
   задаётся (адреса хоста там нет, привязка валила бы каждый запрос); Google News идёт по
   маршруту по умолчанию, на машине стенда — через VPN (`config.news_local_address`,
   `weak/live.py`).
6. При старте `api` применяет схему (`jobs.apply_schema`: таблицы без индексов корпуса,
   идемпотентно, `NFR-41`), возвращает в очередь брошенные задания, а брошенное с флагом
   `cancel` переводит в `cancelled` (`jobs.requeue_stale`, `BR-40`), запускает поток исполнителя и сторожа (UC-32),
   открывает пул соединений `POOL_MAX = 8` и монтирует статику `web/dist` последней
   (`_SiteStatic`: `assets/` с `Cache-Control: public, max-age=31536000, immutable`, точки
   входа `index.html`, `print.html` — `no-cache`; `NFR-42`).
7. Compose проверяет здоровье `api`: `python -c "urllib.request.urlopen(
   'http://127.0.0.1:8010/api/health', timeout=10)"` каждые 60 с, таймаут 15 с, 3 попытки,
   `start_period` 60 с; `restart: unless-stopped` у всех сервисов.
8. Снято: сервис `ui` (Streamlit на `/classic`) убран (`c1c4ea8`).
9. Порты публикуются только на loopback хоста (`NFR-50`): `127.0.0.1:${API_PORT:-8010}:8010`,
   `127.0.0.1:${POSTGRES_PORT:-5436}:5432`.
10. Администратор проверяет: `http://127.0.0.1:8010` открывает веб-интерфейс,
    `http://127.0.0.1:8010/api/docs` — Swagger, `GET /api/health` отвечает
    `{"status": "ok", "worker": true, ...}`.
11. Администратор проверяет связь с моделями одним запросом (UC-01) и видит в логе
    `docker compose logs -f api` строку ответа с моделями (`NFR-38`).

## 6. Альтернативные потоки и потоки исключений

**A1. Шард-сервер на хосте (после шага 5).** Проход по шардам из контейнера через bind
mount идёт втрое медленнее нативного (~165 с против ~60 с на вызов, три вызова на запрос —
докстринг `shard_server.py`). Администратор запускает на хосте:
```bash
python -m pelican.shard_server        # 127.0.0.1:8020
```
Процесс слушает только loopback; лог — файл `DATA_DIR/shard-server.log` (процесс
запускают через `pythonw`, без stdout). Из контейнера `api` достигает его по
`host.docker.internal:8020` (`SHARDS_URL_DOCKER`). Проверка: `GET http://127.0.0.1:8020/health`
→ `{"status": "ok"}`. Без шард-сервера `api` читает шарды сам из `/shards` — медленнее, но
работает (`NFR-32`).

**A2. Публичный стенд, профиль `stand` (после шага 10).** Администратор задаёт
`TS_AUTHKEY` в `.env`, разрешает атрибут `funnel` в политике tailnet и поднимает:
```bash
docker compose --profile stand up -d
```
Сайдкар `tailscale/tailscale:latest` с `hostname: pelican`, `TS_USERSPACE=true` (без
`/dev/net/tun`), конфигом `tailscale/serve.json`: Funnel на 443, единственный обработчик
`/` → `http://api:8010` (веб-интерфейс и API). Funnel перезаписывает `X-Forwarded-For`
адресом источника — на этом стоит правило адреса клиента `BR-36`, `NFR-51`: первый адрес
`X-Forwarded-For`, иначе адрес соединения (`api._client`). Публичный адрес —
`https://pelican.<tailnet>.ts.net`.

**A3. Чистая машина, пустой корпус (шаг 4).** На машине без базы и шардов
`docker compose up` поднимает сервис с пустым корпусом: научный поток ничего не находит,
но сервис работает. Наполнение — UC-20 (сбор с нуля), UC-22 (перелив из Quack), UC-21
(эмбеддинги), после загрузки — индекс:
```bash
docker compose exec postgres psql -U pelican -d pelican \
  -c "CREATE INDEX IF NOT EXISTS works_tsv ON works USING GIN (tsv)"
```
Полноту проверяет UC-30.

**A4. Запуск без Docker (разработка).** `python -m venv .venv && pip install -e ".[dev,ml]"`,
`docker compose up -d postgres`, `uvicorn pelican.api:app`; фронт — `cd web && npm ci &&
npm run build` (сборку отдаёт `api`) или `npm run dev` на :5173 с прокси `/api` на :8010
(`PELICAN_API`). `WEB_DIR` указывает каталог со сборкой; без `index.html` — только API.

**A5. Пересборка после изменения кода.** Повторный `docker compose up -d --build`:
слой зависимостей не переустанавливается, если `requirements.txt` не менялся; очередь в
Postgres переживает перезапуск (`NFR-17`); брошенное задание возвращается в очередь (шаг 6).

**E1. Сервер моделей недоступен или модель не загружена (шаг 11).** Стенд поднимается,
но первый прогон падает с фразой «Сервер моделей запущен, но модель на нём не загружена…»
или «Стенд не достучался до сервера моделей или поиска по корпусу…» (`failure.explain`,
`BR-42`). Администратор загружает модели в LM Studio и проверяет `LLM_BASE_URL_DOCKER`.
Прогон повторяется пользователем.

**E2. Postgres не прошёл healthcheck (шаг 4).** `api` не стартует (`depends_on:
service_healthy`). Администратор смотрит `docker compose logs postgres`; типичная причина —
нехватка памяти VM под `shared_buffers=2GB` или том, смонтированный не в
`/var/lib/postgresql` (данные образа 18 лежат в `/var/lib/postgresql/18/docker`).

**E3. `api` не проходит healthcheck (шаг 7).** `/api/health` отвечает 503 — поток
исполнителя мёртв или не обращался к очереди дольше `STUCK_RUNS = 2.0` × типичного прогона:
простаивающий исполнитель обращается к очереди каждые `IDLE_POLL_S`, поэтому так выглядит
занятый, чей прогон затянулся (`api.health`); разбор — UC-28, поведение сторожа — UC-32.

**E4. Нет `web/dist/index.html` (шаг 6).** `api` поднимается без интерфейса: `WEB_CANDIDATES`
не нашёл сборку. Причина — `WEB_DIR` указывает на `web/` без `dist` или сборка Vite не
прошла. Проверить лог стадии `node` при `--build`.

**E5. Шарды не смонтированы (шаг 5).** `EMB_HOST_DIR` указывает на пустой каталог —
научный поток пуст, как в A3. Проверить путь и права на чтение.

## 7. Постусловия

**Успех.** Сервисы `postgres` и `api` в состоянии `healthy` (при профиле `stand` — ещё
`tailscale` в `running`); `GET /api/health` → 200 `ok`; веб-интерфейс на `/`, Swagger на
`/api/docs`; очередь пуста или содержит возвращённые задания; схема применена; при профиле
`stand` — публичный адрес отвечает по HTTPS.

**Отказ.** Любой сервис не поднялся; состояние базы не повреждено (том `pgdata`
сохраняется между попытками); задания не потеряны.

## 8. Особые требования

- `FEAT-35` (развёртывание Docker Compose, профиль `stand`, шард-сервер, healthcheck).
- `NFR-36` (конфигурация переменными), `NFR-37` (один образ в две стадии), `NFR-41`
  (идемпотентная схема), `NFR-42` (статика последней; `assets/` — на год, точки входа —
  no-cache), `NFR-48` (GPU ≈ 12 ГБ,
  VM 8 ГБ, диск ≈14 + 22 ГБ), `NFR-50` (порты только loopback), `NFR-51` (адрес клиента),
  `NFR-54` (секреты вне репозитория), `NFR-56` (шарды только на чтение).
- `BR-34` (один исполнитель), `BR-36` (порядок заголовков адреса не меняется, пока порты
  на loopback), `BR-40` (возврат брошенных заданий при старте).
- Образ не содержит `.env`, `data`, `reports`, `tests`, `web/node_modules`, `web/dist` хоста
  (`.dockerignore`); `web/dist` берётся из стадии сборки.
- Индексы корпуса (`@indexes` в `schema.sql`) строятся переливом или вручную, не стартом
  приложения.

## 9. Точки расширения и включения

- Включает UC-32 (сторож и возврат брошенных заданий стартуют вместе с `api`).
- Расширяется UC-20/UC-21/UC-22/UC-30 (наполнение и проверка корпуса на чистой машине).
- Предшествует UC-28 (контроль) и всем пользовательским сценариям.

## 10. Данные и интерфейсы

| Элемент | Значение |
|---|---|
| Сервисы compose | `postgres` (postgres:18), `api` (образ `pelican:latest`, якорь `x-app`), `tailscale` (профиль `stand`) |
| Тома | `pgdata` → `/var/lib/postgresql`; `appdata` → `/data`; `tsstate` → `/var/lib/tailscale`; bind `${EMB_HOST_DIR}` → `/shards:ro`; `./tailscale/serve.json` → `/config/serve.json:ro` |
| Порты хоста | `API_PORT` 8010, `POSTGRES_PORT` 5436 — оба на 127.0.0.1; шард-сервер 8020 на хосте (`SHARD_SERVER_PORT`); Funnel 443 |
| Переменные в compose | у `api`: `DATABASE_URL`, `LLM_BASE_URL` ← `LLM_BASE_URL_DOCKER`, `DATA_DIR=/data`, `EMB_DIR=/shards`, `SHARDS_URL` ← `SHARDS_URL_DOCKER`, `NEWS_LOCAL_ADDRESS: ""` |
| Funnel | `tailscale/serve.json`: `${TS_CERT_DOMAIN}:443`, обработчик `/` → `http://api:8010`, `AllowFunnel` |
| Переменные `.env` | см. `NFR-36`; обязательные для стенда: `POSTGRES_PASSWORD`, `LLM_BASE_URL_DOCKER`, `EMB_HOST_DIR`; для `stand` — `TS_AUTHKEY` |
| Healthcheck | postgres: `pg_isready -U pelican -d pelican`, 5 с / 5 с / 20; api: `GET /api/health`, 60 с / 15 с / 3, `start_period` 60 с |
| Эндпоинты проверки | `GET /api/health`, `GET /api/docs`, `GET /` (index.html), `GET http://127.0.0.1:8020/health` |
| Код | `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `tailscale/serve.json`, `src/pelican/api.py` (`create_app`, `WEB_CANDIDATES`), `src/pelican/jobs.py` (`apply_schema`, `requeue_stale`), `src/pelican/shard_server.py`, `src/pelican/config.py` |

## 11. Открытые вопросы

- `OQ-04` — аутентификации нет: стенд за Funnel открыт всем, кто знает адрес.
- `OQ-06` — расписания сбора корпуса нет; на новой машине сбор запускается вручную.
- `OQ-13` — размеры корпуса и шардов в README, `shard_server.py` и техдоке расходятся
  (22 / 23.5 ГБ; 14 / 15.2 млн).

## 12. Definition of Ready

Поверх общего чек-листа 08 §4.1:

- [x] Источники названы: README, `docker-compose.yml`, `Dockerfile`, `serve.json`, техдока §2.
- [x] Числа привязаны к владельцам: порты и healthcheck — `docker-compose.yml`;
      `POOL_MAX = 8`, `WEB_CANDIDATES` — `api.py`; GPU и объёмы — README (`OQ-13`).
- [x] Контракт событий не затрагивается.
- [x] Фикстура: тестовая база `pelican_test` (`tests/conftest.py`) для проверки
      `apply_schema` и `requeue_stale`; сам Compose проверяется на стенде.
- [x] Стендовые условия: машина с Docker ≥ Compose v2, GPU ≈ 12 ГБ, LM Studio с двумя
      моделями; окно на сборку образа и наполнение корпуса (часы).
- [x] Не применяется: правило приёмки «до прогона» (сценарий не меняет состав ТОП);
      UI-макет (экрана нет).

## 13. Definition of Done

Поверх общего чек-листа 08 §4.2; для реализованного сценария — свидетельства:

- [x] `docker compose up -d --build` на стенде поднимает `postgres` и `api`; `api`
      становится `healthy` в пределах `start_period` 60 с + 3 × 60 с. Свидетельство: публичный
      стенд `https://pelican.chinchilla-ilish.ts.net` отвечает (README).
- [x] `GET http://127.0.0.1:8010/api/health` → 200 `{"status":"ok","worker":true}`;
      `GET /api/docs` → Swagger; `GET /` → `index.html` веб-интерфейса.
- [x] Схема применяется идемпотентно при каждом старте: `tests/test_jobs.py`
      (фикстура `conn` применяет `apply_schema` к `pelican_test` повторно без ошибок).
- [x] Брошенные задания возвращаются при старте:
      `tests/test_jobs.py::test_requeue_stale_running`,
      `::test_requeue_stale_drops_events_of_the_abandoned_run`.
- [x] Порты опубликованы только на 127.0.0.1: `docker compose ps` показывает
      `127.0.0.1:8010->8010`, `127.0.0.1:5436->5432`.
- [x] Профиль `stand`: `https://pelican.<tailnet>.ts.net/` открывает веб-интерфейс; лимит на
      адрес работает через Funnel (первый адрес `X-Forwarded-For`) —
      `tests/test_api.py::test_forwarded_for_limits_funnel_clients`.
- [x] Шард-сервер: `GET http://127.0.0.1:8020/health` → `ok`; при его остановке прогон
      проходит (медленнее) — ручная проверка на стенде.
- [x] Первый прогон после развёртывания даёт строку лога `runner.run_query` вида «ответ «…»:
      N сигналов, N кандидатов, N источников; модели: <роль>=<модель>, …», где роли —
      строки `weak/ask.answer_models`: «генерация (локально): направления запроса, приёмы в
      работах, ядра, …», «эмбеддинги (локально): поиск по научному корпусу, сведение
      названий», «скоринг (уверенность модели и порядок ТОП): …» (`BR-28`).
- [x] Не применяется: потолок времени, раскладка экранов, подсказки ⓘ, честность экрана
      (сценарий без интерфейса и без изменения конвейера).
- [x] Документация: README «Запуск», «Корпус», «Переменные окружения» описывают процедуру;
      техдока §2 — слои и Funnel. Трассировка — `07-traceability.md`.
