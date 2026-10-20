# UC-20 Собрать научный корпус

## 1. Шапка

| Атрибут | Значение |
|---|---|
| ID | UC-20 |
| Название | Собрать научный корпус |
| Пакет | D. Корпус и данные |
| Первичный актор | ML-инженер |
| Вторичные акторы | arXiv API, OpenAlex API, Postgres |
| Приоритет | Should |
| Статус | Реализовано |
| Стабильность | Средняя: ширины окон и паузы замерены (`sources/arxiv.py`, `sources/openalex.py`); терпение бэкфилла OpenAlex (`BACKFILL_PATIENCE` = 1 ч, потолок паузы 60 с) — догадка (`OQ-16`) |
| Риск | Средний: платный бюджет OpenAlex, блокировка arXiv по IP, отсутствие расписания (`OQ-06`) |
| Источник | `src/pelican/collect.py`, `src/pelican/sources/__init__.py`, `sources/arxiv.py`, `sources/openalex.py`, `sources/base.py`, `src/pelican/store.py`, README «Корпус», техдока §3–4 |
| Проверено на | `master@1dded3e` |

## 2. Краткое описание

ML-инженер наполняет и поддерживает корпус научных работ в таблице `works`: суточным
окном (оба источника, без аргументов) или загрузкой истории за N суток с продолжением
без нахлёста. Корпус — научный поток конвейера (FEAT-03): без него выдача опирается
только на новости. Сбор идемпотентен (id работы вычисляется из данных), устойчив к
отказу одного источника и сообщает исход кодом возврата.

## 3. Предусловия

- Postgres доступен по `DATABASE_URL` (`config.Settings.database_url`); схема применяется
  сбором сама (`Store.init_schema(indexes=False)`).
- Пакет установлен в окружение (`pip install -e .`), сеть до `export.arxiv.org` и
  `api.openalex.org` есть.
- `OPENALEX_API_KEY` задан, если прогону нужно больше запросов, чем даёт бюджет без
  ключа: ключ — суточный бюджет (`sources/openalex.py`: $0.0001 за запрос, свободный
  ключ — $1 в сутки, то есть 10 000 запросов; без ключа на порядок меньше). Исчерпание
  бюджета — не отказ, а E1 (`PartialFetch`, код 23).
- `CONTACT` задан — он идёт в `User-Agent` (`config.Settings.user_agent`, `NFR-69`).
- Для суточного окна важно, чтобы индексы корпуса уже были построены (после первой
  загрузки — `CREATE INDEX IF NOT EXISTS works_tsv … GIN (tsv)`, README).

## 4. Триггер

Наступило время суточного сбора (ручной запуск, расписания нет — `OQ-06`) или нужно
загрузить историю за период.

## 5. Основной поток

1. ML-инженер запускает суточный сбор:

   ```bash
   python -m pelican.collect                     # оба источника, суточное окно
   ```

2. Система строит адаптеры из реестра `sources.REGISTRY` (`arxiv`, `openalex`) с суточным
   окном: arXiv — `DAILY_LOOKBACK` = 3 суток по `submittedDate` (индекс отстаёт на
   ~сутки), OpenAlex — `DAILY_LOOKBACK` = 7 суток по `publication_date` (день
   наполняется неделями).
3. Система читает источники конкурентно (producer на источник), а пишет одним
   потребителем через `asyncio.Queue(QUEUE_SIZE = 64)` кусками `Chunk` по мере
   поступления (`collect.collect`), печатая на каждый кусок «источник: кусок k, получено
   N, новых M».
4. Адаптер arXiv ждёт `DELAY_S` = 3 с между запросами (общая очередь на хост
   `HostPacer`), берёт срезы по `PAGE_SIZE` = 2000, не больше `MAX_PAGES_PER_WINDOW` = 3
   на окно; окно, не влезающее в ёмкость, делится пополам, а не обрезается.
5. Адаптер OpenAlex ходит курсором по `PER_PAGE` = 200 с паузой `DELAY_S` = 0,2 с, срезы
   одного окна (`OPENALEX_FILTERS`, 6 полей) параллельно; окно шире суток с
   `meta.count` > `SPLIT_AT` = 80 000 делится; остаток бюджета читается из заголовков
   `x-ratelimit-*`.
6. Для каждой работы система вычисляет `id` = `store.work_id(source, external_id,
   metric, observed_at)` (младшие 8 байт md5, сдвиг на бит), отбрасывает повторные версии
   препринта arXiv (`Store._new_arxiv_versions_only`: выживает младшая версия), считает
   `junk` (`weak.corpus.is_junk`) и `field`, пишет `COPY` во временную таблицу и
   `INSERT … ON CONFLICT (id) DO NOTHING` с `collected_at = now()` (`Store.insert_works`).
7. По завершении всех источников система печатает итог по каждому («ок» / «частично» /
   «отказ», получено, новых) и общее время и завершается кодом **0**.

## 6. Альтернативные потоки и потоки исключений

**A1. Загрузка истории (шаг 1).** ML-инженер задаёт окно и источник:

```bash
python -m pelican.collect -s openalex --days 365                 # год назад от сегодня
python -m pelican.collect -s arxiv --days 520 --skip-days 260    # продолжение без нахлёста
```

`sources.build_backfill(name, days, skip_days)` строит тот же адаптер с `lookback = days`
и `skip = skip_days` (свежий край окна отодвинут, чтобы срез начинался там, где кончился
предыдущий); `--skip-days` ≥ `--days` отвергается (`KeyError`). Ширина окон: arXiv — по
плотности года (`DENSITY_BY_YEAR`, цель `TARGET_PER_WINDOW` = 1500 работ в окне, от
`MIN_WINDOW_DAYS` = 1 до `MAX_WINDOW_DAYS` = 365; план всегда строится от «сегодня» и лишь
потом режется по `skip`, чтобы срезы стыковались), OpenAlex — `WINDOW_DAYS` = 31 (каждое
окно стоит минимум двух запросов). Ретраи у истории OpenAlex — `BACKFILL_RETRY` (общий
дедлайн `BACKFILL_PATIENCE` = 1 ч, пауза до 60 с с джиттером) вместо суточных
`DAILY_RETRY` (3 попытки, пауза 1–10 с).

**A2. Один источник (шаг 1).** `-s arxiv` или `-s openalex`; неизвестное имя
отвергается `argparse` по списку `REGISTRY`.

**A3. Срезы OpenAlex из окружения.** `OPENALEX_FILTERS` (через запятую) и
`ARXIV_CATEGORIES` переопределяют умолчания `config.Settings` — так `verify_science_corpus`
печатает команды докачки под одну дыру (UC-30).

**A4. Повтор того же окна.** Дублей не возникает: одинаковый `id` попадает в
`ON CONFLICT DO NOTHING`; `fetched` растёт, `inserted` — нет. Повтор платит только сетью и
бюджетом.

**A5. Наблюдение за долгим прогоном.** `python scripts/science_progress.py` показывает
добор молодых срезов OpenAlex (`YOUNG_SLICES`: Computer Science, Engineering), последнюю
запись по `collected_at` (UTC и местное время) и остаток суточного бюджета ключа
(UC-21 A1).

**E1. Источник собран не целиком (шаг 3–5).** Адаптер поднял `PartialFetch` (исчерпан
бюджет OpenAlex на одном из срезов окна — остальные срезы окна сохраняются,
`test_openalex_budget_on_one_slice_keeps_the_other_slices`; обрыв посреди окна):
собранное записано, итог «частично — <причина>», код возврата **23** (`collect.EXIT_PARTIAL`).
Следующий прогон доберёт.

**E2. Источник отказал (шаг 3–5).** Исключение адаптера (arXiv `ArXivThrottled` — отказ
по частоте, по IP, ретраями не лечится и не повторяется; окно, которое не делится;
`RuntimeError` «окно обрезано»): остальные источники продолжают, записанное до отказа
остаётся, итог «отказ — <тип: текст>», код возврата **1**.

**E3. Сетевая икота.** `_http.with_retries`: 3 попытки с экспоненциальной паузой 1–10 с
на `TransportError` и 5xx/429 (кроме arXiv 429); 4xx не повторяются.

**E4. База недоступна.** Запись идёт в отдельном потоке (`asyncio.to_thread`); отказ
Postgres роняет прогон исключением — записанные куски остаются (каждый кусок — своя
транзакция).

**E5. Окно arXiv не делится (шаг 4).** Окно схлопнулось до минимума и всё равно не
влезло в ёмкость `PAGE_SIZE × MAX_PAGES_PER_WINDOW` = 6000: `RuntimeError`, код 1
(недостижимо на реальных объёмах, но не молчит).

## 7. Постусловия

**Успех (код 0).** В `works` добавлены новые работы окна с `collected_at = now()`;
повторные версии препринтов не добавлены; `tsv` посчитан (генерируемая колонка).

**Частично (код 23).** Часть окна записана; дыра известна по сообщению; закрывается
повторным прогоном или командами из UC-30.

**Отказ (код 1).** Записанное до отказа сохранено; причина напечатана.

Во всех исходах: индексы корпуса сбором не трогаются (`init_schema(indexes=False)`);
векторов у новых работ нет до UC-21; полнота доказывается только UC-30.

## 8. Особые требования

- `BR-49` (окна и паузы замерены; `BACKFILL_PATIENCE` — догадка, `OQ-16`).
- `NFR-23` (конкурентный сбор, одна запись, код 23), `NFR-36` (переменные окружения),
  `NFR-41` (схема идемпотентна, индексы отдельно), `NFR-57` (схема `works`), `NFR-59`
  (нет дублей), `NFR-60` (`collected_at` с октября 2026), `NFR-69` (User-Agent, паузы,
  бюджет из заголовков).
- Отбор в истории обязан совпадать с суточным (один адаптер, разное окно): иначе свежие
  дни окажутся плотнее исторических (`sources/__init__.py`).
- Дата работы — `<published>` (подача v1) у arXiv и `publication_date` у OpenAlex;
  `cited_by_count` никогда не идёт в `value` (величина «на сейчас»).
- Перед каждым запуском истории: ключ `OPENALEX_API_KEY` задан, бюджет на окно оценён по
  прогнозу `forecast`.

## 9. Точки расширения и включения

- UC-30 — приёмочный шаг этого сценария (полнота доказывается сверкой, а не min/max).
- UC-21 следует за ним (векторы новых работ).
- Предложено: расписание суточного сбора (`FEAT-42`, `OQ-06`).

## 10. Данные и интерфейсы

- CLI: `python -m pelican.collect [-s arxiv|openalex]… [--days N] [--skip-days M]`;
  коды возврата 0 / 23 (`collect.EXIT_PARTIAL`) / 1.
- Таблица `works` (`schema.sql`): `id`, `source`, `term_raw` (заголовок + первые
  `base.TERM_BODY_CHARS` = 240 символов тела), `observed_at`, `url`, `field`, `junk`,
  `tsv`, `collected_at`.
- Константы: `collect.QUEUE_SIZE = 64`; arXiv `DELAY_S = 3.0`, `PAGE_SIZE = 2000`,
  `MAX_PAGES_PER_WINDOW = 3`, `DAILY_LOOKBACK = 3 сут`, `TARGET_PER_WINDOW = 1500`,
  `DENSITY_BY_YEAR`; OpenAlex `DELAY_S = 0.2`, `PER_PAGE = 200`, `WINDOW_DAYS = 31`,
  `MAX_PAGES_PER_WINDOW = 2750`, `SPLIT_AT = 80_000`, `DAILY_LOOKBACK = 7 сут`,
  `BACKFILL_PATIENCE = 1 ч`; `_http.with_retries` 3 попытки, 1–10 с.
- Окружение: `DATABASE_URL`, `OPENALEX_API_KEY`, `OPENALEX_FILTERS`, `ARXIV_CATEGORIES`,
  `CONTACT`.
- Внешние API: `https://export.arxiv.org/api/query` (Atom, `submittedDate`),
  `https://api.openalex.org/works` (курсор, `from/to_publication_date`, заголовки
  `x-ratelimit-remaining`, `-limit`, `-reset`, `-remaining-usd`).

## 11. Открытые вопросы

`OQ-06` (нет планировщика), `OQ-13` (размеры корпуса в документах расходятся),
`OQ-16` (`BACKFILL_PATIENCE` — догадка).

## 12. Definition of Ready

Поверх общего чек-листа `08` §4.1:

- [x] Числа привязаны к константам `sources/arxiv.py`, `sources/openalex.py`,
      `sources/_http.py`, `collect.py`, `sources/base.py` (§10).
- [x] Контракт событий прогона не затрагивается (сбор — вне `ask.run`).
- [x] Тестовые фикстуры названы: живые ответы в `tests/fixtures/` для парсинга без сети
      (`tests/test_science_sources.py`), база `pelican_test` для `Store`.
- [x] Правило приёмки истории записано до прогона: помесячная сверка UC-30 с порогом
      `HOLE_RATIO` = 1 %.
- [x] Бюджет OpenAlex на окно считается кодом (`sources/openalex.Tally`, `forecast`).
- [x] Не применяется: UI-макет, замок модели (сбор GPU не использует), проверка раскладки.
- [ ] Расписание запуска и ответственный — после решения `OQ-06`.

## 13. Definition of Done

Поверх общего чек-листа `08` §4.2. Для статуса «Реализовано» заполнено свидетельствами:

- [x] `tests/test_science_sources.py`: `test_parse_entries_extracts_signals`,
      `test_external_id_keeps_the_version`, `test_old_style_ids_keep_their_archive`,
      `test_date_comes_from_published_not_updated`, `test_arxiv_windows_cover_the_whole_lookback`,
      `test_arxiv_windows_are_contiguous`, `test_window_width_follows_measured_density`,
      `test_deep_backfill_costs_hundreds_of_requests_not_thousands`,
      `test_arxiv_skip_moves_the_fresh_edge_back`, `test_arxiv_slices_join_without_gap_or_overlap`,
      `test_skip_is_refused_where_it_would_lie`, `test_arxiv_does_not_knock_again_after_a_refusal`.
- [x] `tests/test_science_sources.py`: `test_parse_works_extracts_signals`,
      `test_openalex_params_carry_the_window_and_the_scope`,
      `test_openalex_splits_a_window_that_does_not_fit`,
      `test_openalex_refuses_to_truncate_a_day`, `test_openalex_takes_a_dense_day_whole`,
      `test_openalex_follows_the_cursor_past_the_old_ceiling`,
      `test_openalex_budget_on_one_slice_keeps_the_other_slices`,
      `test_openalex_windows_and_slices`, `test_openalex_windows_are_contiguous_and_wide`,
      `test_openalex_walks_windows_outside_and_scopes_together`,
      `test_backfill_waits_out_a_network_outage`, `test_daily_run_does_not_wait_for_one_source`,
      `test_backfill_gets_the_patient_retry`, `test_openalex_reports_plan_and_windows`.
- [x] `tests/test_science_sources.py::test_second_version_of_a_preprint_is_not_a_second_work`
      и `tests/test_store.py::test_work_id_matches_source`,
      `::test_work_id_aware_equals_naive_utc` — идемпотентность id и версии arXiv (нужна
      база `pelican_test`).
- [x] Ручная процедура (суточное окно):

      ```bash
      python -m pelican.collect
      # ожидаемый вывод: «arxiv: ок; получено N, новых M», «openalex: ок; …», «за T с»; код возврата 0
      echo $?            # 0 — собрано; 23 — частично; 1 — отказ источника
      ```

- [x] Ручная процедура (повтор того же окна): второй запуск даёт `новых 0` при
      `получено` > 0 — дублей нет.
- [x] После загрузки истории выполнен UC-30 и дыр нет (или команды докачки отработаны).
- [x] Не применяется: `tests/test_events.py`, потолок 1200 с, проверка раскладки,
      `/api/glossary`.
- [x] Документация: README «Корпус» описывает команды и коды возврата; техдока §4.
