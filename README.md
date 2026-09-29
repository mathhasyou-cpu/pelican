# Pelican — радар слабых сигналов

Сервис автоматизированного поиска зарождающихся научно-технологических трендов (слабых
сигналов). Пользователь вводит направление в свободной форме — «технологии в ИИ»,
«перспективные решения в финтехе», «слабые сигналы в области кибербезопасности», — и
получает ТОП-15 зарождающихся технологий: описание, преимущество, кейс-пример, источники с
датой, типом, языком и уровнем доверенности, уверенность модели и объяснение, почему это
слабый сигнал. Зрелые технологии, стандарты, общие категории, хайп и шум исключаются и
показываются отдельно с причиной.

ЛЦТ-2026, кейс Газпромбанк.Тех.

- **Стенд**: https://pelican.chinchilla-ilish.ts.net
- **Техническая документация** (пайплайн, признаки, методология): [docs/techdoc.md](docs/techdoc.md)
- **Схема архитектуры**: [docs/architecture.puml](docs/architecture.puml) (PlantUML)
- **Отчёт об оценке** (Precision, Recall, F1): [reports/weak-eval.md](reports/weak-eval.md)

## Как устроено

```
Streamlit (ui) ──► FastAPI (api: очередь + конвейер) ──► Postgres 18
                          │                               works 15.2 млн работ + GIN
                          ├─► LM Studio на хосте (gemma-4-12b, embedding-gemma-300m)
                          ├─► шарды векторов корпуса (только чтение)
                          └─► живой поиск: Google News, КиберЛенинка, Хабр, GitHub, EPO OPS
```

Научный корпус (arXiv + OpenAlex) собирается заранее модулем `pelican.collect`; в момент
запроса к нему добавляется живой поиск по открытым источникам. Локальная модель выполняет
запросы по одному, поэтому новый запрос встаёт в очередь, видит своё место и ожидаемое
время начала и запускается сам. Подробности — в [техдоке](docs/techdoc.md).

## Требования

- Docker с Compose v2 (Docker Desktop на Windows/macOS).
- OpenAI-совместимый сервер моделей на хосте, например [LM Studio](https://lmstudio.ai)
  с моделями `google/gemma-4-12b-qat` и `google/embedding-gemma-300m`, сервер на порту 1234.
  Нужна GPU с ~12 ГБ памяти.
- Корпус: Postgres-база с работами и шарды векторов `emb-science/` (раздел «Корпус»).

## Запуск

```bash
cp .env.example .env              # заполнить пути и ключи
docker compose up -d --build      # postgres + api + ui
```

Интерфейс — http://127.0.0.1:8501, API и Swagger — http://127.0.0.1:8010/api/docs.

⚠️ На чистой машине `docker compose up` поднимает сервис с **пустым корпусом**: научная часть
поиска опирается на 15.2 млн работ в Postgres (14 ГБ) и 22 ГБ шардов векторов, и в образ они не
входят. Наполнить корпус — раздел «Корпус» ниже. Демо-стенд развёрнут с полным корпусом.

Публичный адрес через Tailscale Funnel (узел `pelican` в вашем tailnet): задать `TS_AUTHKEY`
в `.env`, разрешить атрибут `funnel` в политике tailnet и поднять профиль `stand`:

```bash
docker compose --profile stand up -d
```

Логи и выбор моделей по каждому ответу:

```bash
docker compose logs -f api
```

## Корпус

Корпус живёт в Postgres (таблица `works`), векторы — в каталоге шардов на хосте
(`EMB_HOST_DIR`, монтируется в `api` только на чтение). Наполнить его можно двумя путями.

**Сбор с нуля** — сборщики arXiv и OpenAlex, суточное окно и история:

```bash
pip install -e .
python -m pelican.collect                            # суточное окно, оба источника
python -m pelican.collect -s openalex --days 365     # история за год
python -m pelican.collect -s arxiv --days 520 --skip-days 260   # продолжение без нахлёста
python scripts/embed_science.py                      # эмбеддинги новых работ в шарды
docker compose exec postgres psql -U pelican -d pelican \
  -c "CREATE INDEX IF NOT EXISTS works_tsv ON works USING GIN (tsv)"   # индекс после загрузки
```

Код возврата сбора: 0 — собрано, 23 — собрано не целиком (следующий прогон доберёт),
1 — источник отказал. Повторный сбор того же окна дублей не даёт: id работы вычисляется
из её данных.

**Перелив готового корпуса** из DuckDB, которую держит сервер Quack:

```bash
QUACK_TOKEN=... python -m pelican.load_corpus --from quack:localhost
```

Перелив идёт кусками по диапазону id и продолжается с места обрыва; в конце строятся индексы.

## Как задать запрос

- **Интерфейс**: поле «Направление» или одна из трёх кнопок-примеров ТЗ. Пока идёт
  предыдущий запрос, ваш ждёт в очереди с прогресс-баром до начала; ссылка на страницу
  сохраняет место в очереди. Готовый отчёт открывается сам и остаётся в истории. Пока
  запрос вкладки в очереди или в работе, кнопки запуска неактивны.
- **Лимит**: с одного IP — не больше трёх запросов в очереди и в работе вместе, четвёртый
  получает `429`. Адрес интерфейс передаёт в API заголовком `X-Client-IP`; без него
  считается адрес соединения.
- **API**:

```bash
curl -X POST http://127.0.0.1:8010/api/ask \
     -H 'Content-Type: application/json; charset=utf-8' \
     -d '{"query": "слабые сигналы в области кибербезопасности"}'
# → 202 {"id": 7, "status": "queued", "position": 1, "eta_start_s": 1020, ...}
curl http://127.0.0.1:8010/api/jobs/7            # статус, место, ход выполнения
curl http://127.0.0.1:8010/api/reports           # история
curl http://127.0.0.1:8010/api/reports/<имя>     # результат целиком (JSON)
```

## Переменные окружения

| переменная | по умолчанию | назначение |
|---|---|---|
| `POSTGRES_PASSWORD` | `pelican` | пароль Postgres в compose |
| `DATABASE_URL` | `postgresql://pelican:pelican@127.0.0.1:5436/pelican` | база для запуска вне Docker (сбор, скрипты) |
| `LLM_BASE_URL` | `http://localhost:1234/v1` | сервер моделей для запуска вне Docker |
| `LLM_BASE_URL_DOCKER` | `http://host.docker.internal:1234/v1` | тот же сервер из контейнера |
| `LLM_MODEL` | `google/gemma-4-12b-qat` | генерация |
| `EMBEDDING_MODEL` | `google/embedding-gemma-300m` | эмбеддинги |
| `LLM_API_KEY` | `lm-studio` | ключ сервера моделей |
| `EMB_HOST_DIR` | `./data/emb-science` | шарды векторов на хосте (для compose) |
| `EMB_DIR`, `DATA_DIR` | `data/emb-science`, `data` | шарды и состояние для запуска вне Docker |
| `GITHUB_TOKEN` | — | поиск GitHub с большим лимитом |
| `EPO_OPS_KEY`, `EPO_OPS_SECRET` | — | патенты EPO OPS; без них патентный канал выключен |
| `CONTACT` | — | контакт в User-Agent для arXiv и OpenAlex |
| `OPENALEX_API_KEY` | — | суточный бюджет запросов OpenAlex при сборе |
| `TS_AUTHKEY` | — | ключ узла Tailscale для публичного стенда |
| `API_PORT`, `UI_PORT`, `POSTGRES_PORT` | 8010, 8501, 5436 | порты на хосте |

## Оценка модели

Отчёт — [reports/weak-eval.md](reports/weak-eval.md): Precision, Recall, F1 с 95%
интервалами на датасете заказчика и контрольном наборе, воспроизводимость рубрики
методолога, точность и достоверность открытой выдачи, ретроспективный бэктест.

Пересобрать отчёт и модель этапа 1:

```bash
pip install -e ".[ml]"
python scripts/measure_signal_kinds.py --evidence mixed    # замер жанра на датасете
python scripts/train_signal_model.py --install             # обучение → src/pelican/weak/model.json
python scripts/weak_eval_report.py                         # → reports/weak-eval.md
python scripts/predict_dataset.py <датасет.csv>            # разметка закрытого датасета
```

Датасет заказчика — `scripts/weak_signals_100.tsv`, контрольный набор — `scripts/weak_controls.tsv`.

## Разработка

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev,ml]"   # Linux/macOS: .venv/bin/pip
docker compose up -d postgres
pytest
```

Тесты используют отдельную базу `pelican_test` в том же Postgres.

## Структура

```
src/pelican/
  api.py, jobs.py, runner.py   API, очередь, прогон запроса
  weak/                        конвейер открытого запроса и методология
  sources/, collect.py         сборщики arXiv и OpenAlex
  store.py, schema.sql         Postgres
  load_corpus.py, quack.py     перелив корпуса из DuckDB
  embed.py, llm/               эмбеддинги и клиент модели
ui/app.py                      интерфейс Streamlit
scripts/                       замеры, обучение, отчёт метрик, датасеты
docs/                          техдока и схема архитектуры
reports/weak-eval.md           отчёт об оценке
```
