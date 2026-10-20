# UC-03 Обработать запрос из очереди

## 1. Шапка

| Атрибут | Значение |
|---|---|
| ID | UC-03 |
| Название | Обработать запрос из очереди |
| Пакет | A. Поиск слабых сигналов |
| Первичный актор | Исполнитель очереди (система: `api.Worker`); инициатор — постановка задания (`jobs.enqueue` + `worker.wake`) или опрос очереди раз в `api.IDLE_POLL_S = 5` с |
| Вторичные акторы | Сервер моделей (генерация, эмбеддинги), Postgres (корпус, разметка, очередь, отчёты), шард-сервер или шарды, живые источники (Google News EN/RU, КиберЛенинка, Хабр, GitHub), EPO OPS, arXiv/OpenAlex (аннотации для углубления), опциональные источники за флагом |
| Приоритет | Must |
| Статус | Реализовано (ветви за флагами `WEAK_*` — Реализовано за флагом; на стенде включены `WEAK_LAYERS` и `WEAK_EXTRA_SOURCES`, A8) |
| Стабильность | Средняя: бюджет `BUDGET_S`, `DEEPEN_MIN_S`, `PER_FACET`, `CORE_POOL`, `MONEY_POOL`, `NEWS_PER_CARD`, `EXTRA_FOR_COMPANIES` помечены в коде «догадка» (`OQ-16`); `CARDS_RESERVE_S` замерен (208–258 с на 15 сигналов) и догадкой не является |
| Риск | Высокий: качество и время зависят от локальной модели и внешних источников |
| Источник | ТЗ (цитаты A.1, A.2, A.5, A.7–A.10, A.17 в 02), техдока §2, §5, §6, `src/pelican/weak/ask.py run`, `api.Worker._run`, `events.py`, `runner.py` |
| Проверено на | `master@1dded3e` |

## 2. Краткое описание

Единственный исполнитель забирает старейшее ожидающее задание, держит замок модели и
проводит запрос через 10 стадий конвейера: раскрытие запроса, научный и денежный потоки,
сведение, свидетельства, оценка зрелости, скоринг, патенты, карточки, сохранение. На
каждой стадии он шлёт структурные события по контракту `events.py` (их читает экран
ожидания, UC-02) и строки прогресса. Итог — отчёт в `reports` (JSON, HTML, сводка, модели)
и задание в `done`; любой отказ — задание в `failed` с двумя строками причины; отмена
автором — задание в `cancelled` на ближайшей границе стадии (A11). Это главный
сценарий системы: именно здесь выполняются FEAT-02…13 и бизнес-правила ТЗ.

## 3. Предусловия

- В `jobs` есть задание `queued`; исполнитель жив (`Worker.alive()`).
- Сервер моделей отвечает на `LLM_BASE_URL` с моделями `LLM_MODEL` и `EMBEDDING_MODEL`.
- Корпус и шарды доступны (пустой корпус допустим — выдача обеднена, не отказ).
- Замок модели `runner.MODEL_LOCK` («model», берётся через `locks.hold`) свободен; иначе — E9.

## 4. Триггер

`Worker._loop`: `jobs.claim()` вернул задание — после `wake.set()` из `api.ask` или по
истечении `IDLE_POLL_S`.

## 5. Основной поток

Времена — холодный прогон на стенде (техдока §5); `emit` — событие по контракту (§10);
`say` — строка прогресса в `jobs.progress`.

1. **Взять задание.** `jobs.claim`: старейшее `queued` → `running`, `started = touched = now()`
   (`FOR UPDATE SKIP LOCKED`). Исполнитель запоминает момент старта (`began`) и запускает
   `runner.run_query(query, say, emit=emit)`.
2. **Захватить замок модели.** `locks.hold("model")` и `Store` — на время `ask.run`
   (шаги 3–12, `runner.run_query`); замок снимается сразу по возврате `ask.run`, до шага 13.
3. **expand** · `emit stage{expand}` · `say` «раскрываю запрос в направления» ·
   генеративная модель переводит запрос, называет предмет (`area_en`), 3–6 направлений
   (`facets`, `facets_en`), поисковый запрос `query_en` и до 6 подрынков (`submarkets_en`)
   по схеме `_expand_schema` (`EXPAND_PROMPT`: технологии общего назначения — только если
   названы пользователем; предмет не расширяется до родительской отрасли). Создаётся
   `AskResult` с `started`, `models = answer_models()`, `version = REPORT_VERSION` ·
   `emit facets{facets_ru, facets_en, submarkets, query_en}` · ~11–17 с.
4. **science** · `emit stage{science}` · `ground(store, facets, k=PER_FACET)`: эмбеддер
   находит `PER_FACET = 30` ближайших работ на направление (`works_found`) · `say` «называю
   технологию в N работах» · `run_tech`: модель называет приём и его роль в каждой работе,
   результат — в `tech_mentions` (повторно не размечается); отказы разметки — в
   `tech_stats` · `works_named` · `emit counts{works, works_named}` · сведение вариантов
   написания эмбеддером (`build_groups`, порог `embed.THRESHOLD`), одиночки — тоже
   кандидаты; в пул идут до `CORE_POOL − MONEY_POOL` научных кандидатов по числу работ ·
   1–2 мин.
5. **money** · `emit stage{money}` · `money_queries(area_en, facets_en, submarkets_en)` →
   `money_candidates`: заголовки о раундах, пилотах, сделках в два круга, модель называет
   направления с несколькими компаниями, компании сверяются с текстом заголовка ·
   направления, уже найденные наукой, отбрасываются; порядок — число компаний (не больше
   `PLAYERS_CAP`), специфичность имени, число заголовков · `emit counts{headlines,
   directions}` · до `MONEY_POOL = 35` денежных кандидатов; к ним привязываются работы
   (`PAPERS_FOR_CHECK = 4`) для проверки научной опоры · 3–4 мин.
6. **merge** · `emit stage{merge}` · `cores()`: модель сводит каждый ярлык к ядру технологии ·
   `_merge_near` (общие значимые слова), `_merge_twins` (модель) · рыночные категории
   (`is_category`: нет слов сверх запроса и рыночного словаря) снимаются до оценки и
   считаются вслух (`result.categories`, `BR-05`) · ~40 с.
7. **evidence** · `emit stage{evidence}` · `footprints` ядер по GIN-индексу (`say` «…это
   минуты») · отбор в оценку по новизне ядра (денежный поток первым, затем год первого
   появления ядра, затем число работ; ядро без работ — в хвост) · `cands = cands[:CANDIDATES]`
   (`CANDIDATES = 45`) · `emit counts{candidates, categories_removed, categories}` ·
   `assign_facets`: направление каждого кандидата (научному — фасет с наибольшим числом
   его работ, остальным — ближайший по косинусу; сбой эмбеддера — без направления,
   `BR-12`) · след ярлыков (`loose`) · живой поиск: новости по кандидатам
   (`news_many_measured`, EN), общий `live_search(query, area_en)` → `live_by_source`,
   `live_errors`; не ответившие и после повтора кандидаты — `live_errors["google_news_candidates"]`
   и пометка `news_unmeasured` (`BR-27`) · компании из всех рыночных заголовков одной пачкой
   (`companies_of`) · ~3 мин.
8. **assess** · `emit stage{assess}` · по каждому кандидату: свидетельства — до
   `NEWS_PER_CARD = 5` новостей, `PAPERS_PER_CARD = 2` работы, след корпуса (`_evidence`,
   не больше `assess.EVIDENCE`); добор заголовков ради источника у каждой названной компании
   (`_backing`, `EXTRA_FOR_COMPANIES = 3`); модель относит кандидата к одному из шести классов
   (`BR-01`, `BR-02`), называет стадию и 12 атомарных чтений (`judgments`,
   `assess.JUDGMENTS`; среди них три вехи стадии — `prototype_built`, `pilot_with_customer`,
   `generally_available`), `why` со ссылками `[n]` (без ссылок — отбрасывается; у
   исключённого класса пустое `why` заменяется причиной из чтений `ask.excluded_why` «По
   признакам оценки: …. Объяснение оценщика без ссылки на свидетельство не показано.»,
   `BR-03`; у `emerging` остаётся пустым) · проверки `Check`: «жанр: зарождающаяся»
   (`entry=True`; единственное, что проверяет `Signal.shown`), «несколько независимых
   игроков» (≥ `MIN_PLAYERS = 2`, `scored=False`: подпись, не условие ТОП и не в доле
   проверок), «разные типы источников» (≥ 2 из `INDEPENDENT_KINDS`), «научная опора» (≥ 2 работ с
   ядром в тексте), «разные издатели» (≥ 2 доменов новостей), «объяснение со ссылками» ·
   предварительная уверенность = доля пройденных проверок без условий входа и подписей
   (`Check.counted`, `evidence_share`) · `Signal` с компаниями только из показанных заголовков (`BR-21`),
   `facet`, следом корпуса, `news_unmeasured` · `emit verdict{…}` на каждого кандидата ·
   `sources_processed` = работы + документы живого поиска + показанные и добранные
   заголовки · ~170 с.
9. **Разделение выдачи.** `low_visibility` — работ по ярлыку не больше медианы пула;
   `shown` — только `kind == emerging` (`Signal.shown`, `BR-01`); остальные —
   `result.excluded` с причиной-классом (`BR-03`).
10. **score** · `emit stage{score}` · `score(pool)`: модель роста `weak/growth.json` (один
    признак `query_share_pct`, коэф. 1.37) пишет `confidence` (вероятность, Platt) и `predictors`;
    порядок — по убыванию уверенности, при равенстве `top_key` (`BR-08`, `BR-09`, `BR-10`);
    со слоями (шаг 11, `_split_layers`) это правило действует внутри слоя, а не через весь ТОП.
    Если модели нет или её правило провалено — уверенность остаётся долей проверок, порядок
    `top_key` · со слоями (`WEAK_LAYERS`) — `_drop_trends` (уже тренд → исключено) ·
    `_on_topic`: отдельный вызов модели оценивает соответствие запросу 0–3 по
    порядку, пока не наберётся `TOP = 15` (со слоями — `LAYER_POOL = 45`) с оценкой ≥ 2;
    оценка < 2 → исключённые классом `noise` с причиной «Свидетельства не о предмете
    запроса …», первая проверка — «другая отрасль» (`result.off_topic`, `BR-04`); сбой проверки (`LLMError`, `ValueError`,
    `httpx.HTTPError`) кандидата не снимает и оставляет `relevance = None`; хвост после
    набранных 15 не проверяется · `emit relevance{label, relevance, relevance_why}` по
    каждому проверенному · `thin = shown[TOP:]`, `shown = shown[:TOP]` (`BR-07`) ·
    `emit top{labels, confidence}` · доли секунды + вызовы модели.
11. **patents** · `emit stage{patents}` · `attach_patents(shown, live_errors, deadline =
    deadline − CARDS_RESERVE_S)`: для каждого из ≤ 15 финалистов число патентных публикаций
    за два окна и три свежих патента с заявителем (EPO OPS, без ключей канал выключен);
    не успевшее в бюджет — в `live_errors`; при `WEAK_EXTRA_SOURCES` — `attach_extra` ·
    `emit patents{patents: {label: n}}` · ~3.5 мин. Со слоями (`WEAK_LAYERS`, на стенде
    включён) `emit stage{patents}` идёт раньше — сразу после проверки соответствия, до
    `_split_layers`, который считает патенты (без дедлайна, A5) и
    `attach_extra` на весь пул `LAYER_POOL` и раскладывает слабый сигнал в ТОП,
    зарождающееся — в `watch`; следом —
    `emit patents`, затем отбор ТОП; после него патенты повторно не считаются
    (`ask.py:2091-2096`, `2125`).
12. **cards** · `emit stage{cards}` · если до резерва карточек остаётся ≥ `DEEPEN_MIN_S = 60`
    с — `deepen_top`: аннотации arXiv/OpenAlex и фактовые новости по ТОП, иначе
    `live_errors["deepen"]` = «аннотации не дочитывались: исчерпан бюджет времени запроса» ·
    `rubric.evidence_stage`: ступень каждого сигнала ТОП ставит лестница вех по чтениям —
    `generally_available` → раннее внедрение, `pilot_with_customer` → пилот,
    `prototype_built` → прототип, иначе концепция; ступень может стать и ниже выбора модели;
    `stage` модели остаётся признаком скоринга (шаг 10 уже прошёл); у оценки старого формата
    без вех — `rubric.stage_floor` (`BR-15`). Лестница применяется ко всем, кого видно в
    отчёте: ТОП, вне ТОП, исключённым и «наблюдать»; отчёты, сохранённые раньше, выправляют
    при чтении `page.from_json` (HTML) и `GET /api/reports/{name}` (экран) · `write_cards`: описание,
    преимущество, кейс для банка, объяснение с цитатой-подтверждением, гипотеза, хронология —
    только по свидетельствам, предложения без ссылки или с числом вне источников
    вычищаются (`BR-21`, `BR-22`) · `russian_summaries` для зарубежных источников без резюме
    (`machine_summary`, дедлайн — бюджет запроса) · `emit card{label, title, stage,
    description, advantage, case, hypothesis}` на каждый сигнал · `russian_titles` для
    исключённых, вне ТОП, «наблюдать» и сигналов ТОП, чей заголовок карточки остался без
    кириллицы (`latin_top`, `ask.py:2145-2149`) · `emit titles{titles}` · `signals = shown`,
    `seconds` · `say` «готово» · ~3 мин.
13. **Устойчивость.** `runner.run_query` (замок модели уже снят): `jobs.history(query)` → `history.annotate` —
    подпись «держится N из K» и `history_runs`, `stable` (`BR-13`); строка лога «ответ
    «…»: N сигналов, N кандидатов, N источников; модели: …» (`BR-28`).
14. **save** · исполнитель: `emit stage{save}` · `jobs.save_report(name = время старта
    `YYYYmmdd-HHMMSS`, query, began, JSON, HTML, summary)` (`BR-45`) · `jobs.finish` →
    `done`, `report = name` · `emit done{report}` · лог «задание N: готово за T с → name».
15. Исполнитель возвращается к очереди: следующий `jobs.claim`, при пустой очереди —
    `wake.wait(IDLE_POLL_S)`.

## 6. Альтернативные потоки и исключения

**A1. Повтор запроса в течение недели (шаги 4, 7).** Разметка работ уже в `tech_mentions`,
след корпуса в кэше 7 дней, живой поиск в кэше 24 ч — стадии проходят быстрее (`NFR-29`).

**A2. Нет кандидатов (после шага 5).** Ни научных, ни денежных кандидатов → результат с
пустыми списками, `live_errors["google_news_money"]` при отказе денежного потока, `seconds`;
прогон завершается без стадий 6–12 (события `stage` для них не шлются); отчёт сохраняется
(шаг 14) — «пустой отчёт», экран показывает «В этом отчёте нет ни сигналов, ни
исключённых…» (UC-04).

**A3. Оценка кандидата не удалась (шаг 8).** `LLMError`, `ValueError` или `httpx.HTTPError`
на одном кандидате → кандидат в `result.unassessed`, `say` «оценка не удалась: <label>
(<тип>)», `emit verdict{label, title=label, kind:"error", kind_label:"оценка не удалась",
stage:"", why:"", companies:[], stream, facet, sources:0}`; прогон продолжается; в конце
`live_errors["assess"]` = «не удалась у N из M кандидатов: …» (`BR-06`).

**A4. Отказ источника живого поиска (шаг 7).** Источник в `live_errors` с причиной, прогон
продолжается (`NFR-20`); отказ разметки приёмов — `live_errors["tech"]`.

**A5. Патенты и углубление не укладываются в бюджет (шаги 11–12).** Пропуск с пометкой в
`live_errors` («исчерпан бюджет времени»), карточки пишутся всегда (`BR-33`). Со слоями
(`WEAK_LAYERS`) патенты пула считаются в `_split_layers` без дедлайна, и пропуска по бюджету
у них нет — расхождение с `BR-33` (§13). Срок у функции есть, но вызов его не передаёт: срок
срабатывает в большинстве прогонов стенда, и без патентов кандидаты теряли бы подтверждение
слоя — состав ТОП сдвинулся бы против замера Big Tech 20 → 10. `attach_extra` дедлайна не
принимает ни в одной ветке.

**A6. Модель роста недоступна (шаг 10).** `score()` → `False`: уверенность = доля проверок,
порядок `top_key`, предикторов нет.

**A7. Нет `area_en` (шаг 10) — недостижимо.** `expand` подставляет вместо пустого
`area_en` поисковый запрос `query_en`, а вместо пустого `query_en` — сам запрос
(`ask.py:612-613`), поэтому предмет непуст всегда; условие `if area_en` в `run` остаётся
защитным, и проверка соответствия идёт в каждом прогоне.

**A8. Доработки за флагами** (`FEAT-14`; `weak/flags.py`, в коде по умолчанию выключены,
`BR-50`; на стенде решением владельца включены `WEAK_LAYERS=1` и `WEAK_EXTRA_SOURCES=1` —
направлений Big Tech с продажами в ТОП 20 → 10 на четырёх запросах ТЗ, ТОП до 15, ≈ +60 с на
запрос; записанное исключение из правила включения `docs/final-measure.md` §2г, `7ef4aaa`,
техдока §10; остальные флаги на стенде выключены):
`WEAK_BURST` — направления из всплеска в корпусе добавляются к фасетам (или заменяют их при
`only`); `WEAK_ONPOINT_COMPANIES` — компания в карточку только из заголовка о технологии;
`WEAK_LAYERS` — `_drop_trends` (уже тренд → исключено), `_split_layers` (слабый сигнал →
ТОП, зарождающееся → `watch`), пул `LAYER_POOL`; `WEAK_TECHNIQUE` — приём за направлением,
без приёма → «слишком общая категория»; `WEAK_EXTRA_SOURCES` — `attach_extra` (NIH, Europe
PMC, ClinicalTrials.gov, YC); `WEAK_LISTED_IN_ASK` — модель списка заказчика первым ключом
порядка (210–390 с дороже).

**A9. Запись события или строки прогресса не удалась.** Исполнитель пишет предупреждение
в лог и продолжает (`BR-43`); событие вне контракта — предупреждение `events.checked`, но
отправляется.

**A10. Соединение задания оборвалось при записи итога (шаг 14).** `_with_conn` повторяет
запись новым соединением (`NFR-25`).

**A11. Отмена автором (любая граница стадии, шаги 3–12).** Автор отменил идущее задание
(`POST /api/jobs/{id}/cancel`, UC-02 A7) — `jobs.cancel` ставит флаг `cancel = true`,
статус остаётся `running`. Исполнитель проверяет флаг в хуке `emit` на каждом событии
`stage`, кроме `save` (`api.Worker._run`, `jobs.cancel_requested`); посреди стадии поток
держит вызов модели и не прерывается. Флаг стоит → `jobs.Cancelled` (`BaseException`:
`except Exception` вокруг источников его не глотает) → выход из `ask.run` и `locks.hold`
(замок снят) → `jobs.cancelled`: задание `cancelled`, `finished = now()`; отчёт не
сохраняется, событий `done`/`failed` нет, лог «задание N: отменено автором через T с».
Сбой чтения флага — не отмена (предупреждение в лог). Стадия `save` не прерывается: отчёт
уже посчитан. Ожидающее задание отменяется без исполнителя — сразу `cancelled`, `claim` его
не берёт. Флаг `cancel` у `running` при старте процесса → `cancelled`, а не обратно в
очередь (`jobs.requeue_stale`, `BR-40`).

Потоки исключений: любое исключение или `SystemExit` на любой стадии (кроме отмены
`jobs.Cancelled` — A11) → `jobs.fail(error,
error_detail)` где `error = failure.explain(exc)`, `error_detail = "<Тип>: <текст>"`;
трассировка (3 кадра) — строкой прогресса; `emit failed{error, error_detail}`; задание в
`failed`, отчёт не сохраняется, замок снят (`BR-42`). Тексты `error`:

| Поток | Условие (`failure.explain`) | `error` |
|---|---|---|
| E1 | `LLMError` с текстом «No models loaded» / «model_not_found» / «model not found» | «Сервер моделей запущен, но модель на нём не загружена, поэтому прогон остановился. Запрос можно повторить, когда администратор стенда загрузит модель.» |
| E2 | `LLMError`, текст начинается с «HTTP » | «Сервер моделей отклонил запрос, и прогон остановился. Повторите запрос; если отказ повторится, передайте подробности администратору стенда.» |
| E3 | прочий `LLMError` (ответ не по схеме) | «Модель ответила не по схеме, и прогон остановился. Повторите запрос: такой сбой обычно разовый.» |
| E4 | `httpx.ConnectError`, `httpx.ConnectTimeout` | «Стенд не достучался до сервера моделей или поиска по корпусу, и прогон остановился. Повторите запрос позже; если отказ повторится, передайте подробности администратору стенда.» |
| E5 | `httpx.TimeoutException` | «Сервис стенда не ответил вовремя, и прогон остановился. Повторите запрос позже; если отказ повторится, передайте подробности администратору стенда.» |
| E6 | прочий `httpx.HTTPError` | «Сервис стенда ответил ошибкой, и прогон остановился. Повторите запрос позже; если отказ повторится, передайте подробности администратору стенда.» |
| E7 | `psycopg.OperationalError` | «База данных стенда недоступна, и прогон остановился. Повторите запрос позже; если отказ повторится, передайте подробности администратору стенда.» |
| E8 | любое другое исключение, `SystemExit` | «Прогон остановился на внутренней ошибке. Повторите запрос; если отказ повторится, передайте подробности администратору стенда.» |
| E9 | замок модели занят (`locks.Busy`, другой процесс: `rerun.py`, замер) | как E8 (`locks.Busy` — обычное исключение); `error_detail` «Busy: замок model занят» |

**E10. Обрыв Postgres в цикле исполнителя (шаг 1).** Исключение в `claim` → лог, закрытие
соединения, пауза `RECONNECT_S = 5` с, переподключение; поток жив (`NFR-18`).

**E11. Зависание без прогресса.** Сторож снимает задание через `STUCK_RUNS × typical_run_s`
(UC-32); зависший поток не освобождается.

## 7. Постусловия

- **Успех:** `reports` содержит строку `name` с `query`, `started = began`, `result` (JSON с
  `version = REPORT_VERSION`, `models`, `signals ≤ 15`, `thin`, `excluded`, `watch`,
  `categories`, `unassessed`, `live_by_source`, `live_errors`, `candidates`,
  `sources_processed`, `seconds`, `history_runs`, `stable`), `html`, `summary`; `jobs` —
  `done`, `finished`, `report = name`; события `done` и `stage{save}` записаны; замок снят.
- **Отказ:** `jobs` — `failed`, `finished`, `error`, `error_detail`; событие `failed`;
  `reports` без изменений; замок снят.
- **Отмена (A11):** `jobs` — `cancelled`, `finished`; `reports` без изменений; замок снят;
  слот лимита адреса свободен.
- **Инварианты выдачи:** каждый сигнал ТОП — `kind == emerging`, стадия из `rubric.STAGES`
  по лестнице вех (`rubric.evidence_stage`), компании только из показанных источников,
  `models` непуст; у сигнала ТОП `relevance` — 2, 3 или `None` по оговорке `BR-04` (сбой
  проверки); оценка < 2 переводит кандидата в `noise`, непроверенный хвост уходит в `thin`
  (`weak/ask.py _on_topic`); у каждого исключённого непустое `why` (`excluded_why`, `BR-03`).

## 8. Особые требования

- Бизнес-правила: BR-01…BR-13 (классы, причина, категория, неоценённые, ТОП-15, порядок,
  уверенность, предикторы, тренд не выводится, направление, устойчивость), BR-14…BR-16
  (стадия), BR-18…BR-23, BR-27 (источники и честность фактов), BR-28, BR-29, BR-31, BR-32
  (модели), BR-33, BR-34, BR-40, BR-42, BR-43, BR-44, BR-45, BR-47 (время, очередь, события,
  отчёт), BR-50 (флаги).
- NFR-01, 05 (русская выдача, ничего не выдумывается), NFR-17…22, 25, 26 (надёжность),
  NFR-27…29, 31, 32 (время и производительность), NFR-47, 55 (локальные модели, контур),
  NFR-61, 62 (версия и имя отчёта).
- Бюджет: `deadline = старт + BUDGET_S (1100 с)`; `cards_by = deadline − CARDS_RESERVE_S
  (260 с)`; патенты (кроме ветки слоёв, A5) и резюме получают дедлайн; цель замера ТЗ —
  1200 с, потолок прогона на стенде — 2400 с (`BR-33`).

## 9. Точки расширения и включения

- Включается в: UC-01 (постановка), UC-19 (REST), UC-14 и UC-18 (прототип: контекст
  `parent/focus`, `OQ-08`), UC-29 (перепрогон под тем же именем).
- Наблюдается: UC-02 (события; строки прогресса — запасной режим A3), UC-28 (логи, health).
- Прерывается: UC-02 A7 (отмена автором, A11; FEAT-41).
- Фон: UC-32 (сторож, возврат брошенных).
- Данные для: UC-04…UC-08 (отчёт), UC-23/UC-26 (замеры зовут `ask.run` напрямую).

## 10. Данные и интерфейсы

### 10.1 Контракт событий (`src/pelican/events.py`) — единственное полное описание

Исполнитель пишет каждое событие в `job_events` (`seq`, `job_id`, `at` = секунды от старта,
`kind`, `data`); API отдаёт его как `{…data, seq, at, t: kind}`. Контракт проверяет
`events.checked` (`REQUIRED`, `COUNTS`); нарушение — предупреждение, событие уходит.

Стадии `events.RUN_STAGES` (id, подпись; порядок = `n` из `of = 10`): `expand` «раскрываю
запрос в направления»; `science` «ищу по локальному корпусу и называю технологию в
работах»; `money` «денежный поток: читаю заголовки о раундах, пилотах, сделках»; `merge`
«свожу кандидатов к ядру, сверяю близнецов»; `evidence` «меряю след в корпусе, живой поиск,
нахожу компании»; `assess` «оцениваю зрелость кандидатов»; `score` «скоринг моделью роста,
проверяю соответствие запросу»; `patents` «патентная активность финалистов»; `cards`
«углубление и карточки»; `save` «сохраняю отчёт» (шлёт исполнитель API, не конвейер).

| `t` | Обязательные поля (`REQUIRED`/`COUNTS`) | Фактические поля | Когда | Читает фронт (UC-02) |
|---|---|---|---|---|
| `stage` | `id, name, n, of` | те же (`events.stage(sid)`) | начало каждой стадии; `save` — исполнитель; на каждой, кроме `save`, исполнитель проверяет флаг отмены (A11) | полоса, «этап n из 10», список стадий |
| `facets` | `facets_ru, submarkets` | + `facets_en, query_en` | после `expand` | секторы радара, чипы, «Рынки» |
| `counts` | одна из порций: `{works, works_named}`, `{headlines, directions}`, `{candidates, categories_removed}` | третья порция + `categories[]` | после разметки работ; после денежного потока; после отбора 45 | три счётчика; россыпь кандидатов |
| `verdict` | `label, title, kind, kind_label, stage, why, facet, sources` | + `companies[≤4], stream`; `title` = английский ярлык; при отказе оценки `kind:"error"`, `stage:""`, `sources:0` | на каждого кандидата в `assess` | точка в поясе/вне круга, лента, счётчик |
| `relevance` | `label, relevance` | + `relevance_why` | в `score` по каждому проверенному | < 2 → точка за круг |
| `top` | `labels, confidence` | списки по порядку ТОП | после отбора ТОП | номера на точках |
| `patents` | `patents` | `{label: число}` только у кандидатов с патентами (финалисты; со слоями — пул `LAYER_POOL`) | после `attach_patents`; со слоями — после `_split_layers`, до `top` | не читается |
| `card` | `label, title` | + `stage` (ступень лестницы вех), `description, advantage, case, hypothesis` | на каждый сигнал после карточек | русское название |
| `titles` | `titles` | `{label: русское название}` для исключённых, вне ТОП, «наблюдать» и сигналов ТОП с заголовком без кириллицы (`latin_top`; только где название ≠ ярлык) | после `russian_titles` | русские названия |
| `done` | `report` | имя отчёта | исполнитель после `finish` | конец — по статусу задания |
| `failed` | `error, error_detail` | тексты §6 | исполнитель после `fail` | отказ |

Порядок стадий в прогоне: `expand → science → money → merge → evidence → assess → score →
patents → cards → save`; при A2 прогон обрывается после `money`; со слоями `patents`
идёт до события `top`; при отмене (A11) событий после последней `stage` нет — конец виден по
статусу `cancelled`.

### 10.2 Константы (владельцы чисел)

`weak/ask.py`: `TOP = 15`, `REPORT_VERSION = 2`, `BUDGET_S = 1100` (`WEAK_BUDGET_S`),
`CARDS_RESERVE_S = 260`, `DEEPEN_MIN_S = 60`, `PER_FACET = 30`, `CANDIDATES = 45`,
`CORE_POOL = 80`, `MONEY_POOL = 35`, `MIN_PLAYERS = 2`, `NEWS_PER_CARD = 5`,
`PAPERS_PER_CARD = 2`, `EXTRA_FOR_COMPANIES = 3`, `PAPERS_FOR_CHECK = 4`,
`INDEPENDENT_KINDS`, `SUBMARKETS = True`, `LISTED_IN_ASK`, `LAYER_POOL = CANDIDATES`,
`WATCH_MAX = 10`; `weak/assess.JUDGMENTS` (12 чтений); `weak/rubric._STAGE_LADDER` (3 вехи);
`embed.THRESHOLD` (0.78);
`weak/model.PLAYERS_CAP`; `api.IDLE_POLL_S = 5`, `api.RECONNECT_S = 5`;
`runner.MODEL_LOCK = "model"`; `config.llm_timeout_s = 180`, `llm_pipeline_depth = 3`,
`llm_reasoning_effort = "none"`.

### 10.3 Хранилище

`jobs` (статусы `queued`/`running`/`done`/`failed`/`cancelled`, флаг `cancel`, `progress`,
`touched`, `error`, `error_detail`, `report`), `job_events`,
`reports` (`name`, `query`, `started`, `result`, `html`, `summary`), `tech_mentions`
(разметка), `works` (след корпуса, GIN `tsv`), кэши в `DATA_DIR` (живой поиск 24 ч, след
7 дней, аннотации), замки `DATA_DIR/locks/model.lock`.

### 10.4 Интерфейсы

`runner.run_query(query, say, emit) → (name, json, html)`; `weak.ask.run(store, query,
on_progress, on_event) → AskResult`; модели — OpenAI-совместимый `/v1` (`llm/client.py`,
structured output, `temperature = 0`); шард-сервер `POST /top_k`; живые источники и EPO —
`weak/live.py`, `weak/patents.py`.

## 11. Открытые вопросы

- `OQ-11` — `weak_eval_report.py` и замер `measure_ask.py --tz` после исправлений ревью и
  включения флагов не перемерены.
- `OQ-13` — размеры корпуса в документах расходятся.
- `OQ-16` — константы-догадки (§10.2).
- `OQ-17` — мерило STR-14; два флага включены на стенде решением владельца в обход правила
  включения (A8).
- Источники решений (закрыты): `OQ-01` — уверенность сначала доля проверок
  (`evidence_share`), затем `score()` заменяет её вероятностью модели роста, при недоступной
  модели остаётся доля (шаги 8, 10); `OQ-19` — соответствие < 2 — класс `noise` (шаг 10);
  `OQ-20` — «несколько независимых игроков» — подпись `scored=False` (шаг 8); `OQ-12` —
  отмена автором (A11).

## 12. Definition of Ready

Поверх общего чек-листа (08 §4.1):

- [x] Числа привязаны к константам (§10.2); бюджет и резерв названы с переменной окружения.
- [x] Влияние на контракт событий: этот сценарий **владеет** контрактом; любое изменение
      `RUN_STAGES`/`REQUIRED`/`COUNTS` — правка `web/src/lib/timeline.js` и таблицы §10.1,
      проверка `tests/test_events.py`.
- [x] Фикстура: прогон на заглушках `tests/test_events.py::_stub_pipeline` (настоящий
      `ask.run` без модели, корпуса и сети) и `FakeRunner`/`EventRunner` из
      `tests/test_api.py`; база `pelican_test`.
- [x] Для изменений, влияющих на состав/порядок ТОП: правило приёмки до прогона
      (`docs/final-measure.md`), замер `scripts/measure_ask.py --tz` (UC-26).
- [x] Стенд: замок `runner.MODEL_LOCK` и окно GPU — согласованы (стенд один, очередь одна).
- [x] Затронутые правила: BR-01…16, 18…23, 27…29, 31…34, 40, 42…45, 47, 50; NFR §8.
- [x] Экран не нужен (системный сценарий); наблюдатель — UC-02.
- [x] Блокирующих OQ нет: `OQ-01` закрыт (действует определение техдоки, `BR-09`).

## 13. Definition of Done

Критерии приёмки и свидетельства (реализовано; заполнено ретроактивно):

- [x] Настоящий `ask.run` на заглушках шлёт события только по контракту, стадии — в порядке
      `RUN_STAGES` без `save`, присутствуют `facets, counts, verdict, relevance, top, patents,
      card, titles`, `top.labels` совпадает с `result.signals`, у вердикта есть `facet` и
      стадия: `tests/test_events.py::test_real_run_emits_contract_events_in_stage_order`.
- [x] Со слоями (`WEAK_LAYERS`) стадия `patents` объявляется и событие `patents` шлётся,
      стадии — полный `RUN_STAGES` без `save`:
      `tests/test_events.py::test_layers_run_announces_patents_stage`.
- [x] Контракт: `events.stage`, `events.problems` (нехватка полей, неизвестный вид, порции
      `counts`), `events.checked` логирует и отправляет:
      `tests/test_events.py::test_stage_event_and_contract_check`, `::test_checked_logs_but_still_sends`.
- [x] Копия стадий фронта совпадает: `tests/test_events.py::test_frontend_timeline_has_the_same_stages`.
- [x] Исполнитель: задания идут по одному, следующее стартует само, отчёт доступен по
      `GET /api/reports/{name}`, сводка истории:
      `tests/test_api.py::test_second_request_waits_then_autostarts`.
- [x] Отмена (A11): идущее с флагом `cancel` снимается на границе стадии → `cancelled`,
      снятое из очереди не стартует, слот адреса освобождается:
      `tests/test_api.py::test_cancel_queued_now_running_at_stage_boundary`.
- [ ] Флаг `cancel` у брошенного `running` при старте → `cancelled` (`jobs.requeue_stale`) —
      **автотеста нет**.
- [x] Отказ прогона → `failed` с фразой «Прогон остановился…» и `error_detail`
      «RuntimeError: …», оба в событиях: `tests/test_api.py::test_failure_visible_in_status`.
- [x] `SystemExit` из прогона → отказ задания, исполнитель жив:
      `tests/test_api.py::test_system_exit_in_runner_fails_job_and_keeps_worker`.
- [x] Обрыв Postgres в `claim` → переподключение: `tests/test_api.py::test_lost_connection_reconnects`.
- [x] События `done`/`stage{save}` после `finish`, `report` совпадает:
      `tests/test_api.py::test_events_endpoint_replays_the_run_with_a_cursor`.
- [x] Брошенное `running` возвращается в очередь без событий:
      `tests/test_jobs.py::test_requeue_stale_running`, `::test_requeue_stale_drops_events_of_the_abandoned_run`.
- [x] Отчёт сохраняется и читается со сводкой истории:
      `tests/test_jobs.py::test_report_roundtrip`, `::test_summaries_read_stored_summary_and_survive_odd_reports`.
- [x] `started` — время старта прогона: строку, где записан конец, схема возвращает к старту
      из результата, верную не трогает — `tests/test_jobs.py::test_schema_moves_end_time_back_to_start`;
      запись `began` исполнителем (`api.Worker._run`) отдельным тестом не проверяется.
- [x] Правила выдачи на уровне конвейера (`tests/test_weak_ask.py`): фильтр мейнстрима
      (зрелое, стандарт, хайп, шум, общая категория не идут в ТОП) —
      `test_only_emerging_goes_to_top`; «не единственное основание» по уровню издателя —
      `test_corroboration_needs_something_above_low`, `test_trust_levels`; вывод без ссылок
      отбрасывается — `test_assess_parse_drops_uncited_why`; русские названия исключённых —
      `test_excluded_get_russian_titles`; гипотеза без ссылки пуста —
      `test_card_hypothesis_needs_citation`; резюме источников —
      `test_russian_summaries_fill_only_foreign_sources_without_one`,
      `test_russian_summaries_stop_at_deadline_with_a_note`; докстринги тестов цитируют ТЗ.
- [x] Стадия по лестнице вех, в том числе ниже выбора модели; без вех — пол
      (`BR-15`): `tests/test_weak_rubric.py::test_evidence_stage_climbs_the_ladder_of_milestones`,
      `::test_stage_floor_follows_what_the_evidence_reads`; схема оценки требует все 12 чтений —
      `tests/test_weak_ask.py::test_assess_schema_requires_every_judgment`.
- [x] Причина исключённого без ссылки — из чтений оценки (`BR-03`):
      `tests/test_weak_ask.py::test_excluded_why_names_the_judgments_when_model_why_was_dropped`;
      заголовок карточки без кириллицы не принимается — `::test_card_title_must_be_russian`
      (добор `latin_top` в `russian_titles` отдельным тестом конвейера не проверяется).
- [ ] Потолок времени: холодный прогон каждого из `TZ_QUERIES` ≤ 1200 с и 15 позиций —
      `scripts/measure_ask.py --tz --repeat 3`, свидетельство `reports/ask-tz.tsv`
      (1004–1185 с — замер без флагов; техдока §5: «около 15–19 минут»). Свидетельство
      создаётся на машине стенда и в репозиторий не входит (`.gitignore`: `reports/*`, кроме
      перечисленных исключений); проверка — по имени файла и дате на стенде. Полный ТОП с
      подрынками — в 9 прогонах из 12 (техдока §5). Со слоями и новыми источниками стенда
      (A8) ≈ +60 с на запрос, `--tz` не перемерен — `OQ-11`.
- [ ] Код расходится: со слоями патенты пула считаются без дедлайна — вызов
      `_split_layers` в `weak/ask.run` срок не передаёт (функция его принимает,
      `tests/test_weak_layers.py::test_pipeline_splits_trend_weak_and_watch`); включить — только
      с перемером ТОП на конфигурации стенда (`BR-33`, A5; 07 §8.6).
- [x] `BR-15` у всех списков: лестница у вне ТОП, исключённых и «наблюдать», отчёт старше
      правки выправляется при чтении —
      `tests/test_api.py::test_saved_report_stage_follows_ladder_in_every_list`.
- [x] `BR-04`: остановка на 15 тематичных и перенос оценки < 2 в исключённые классом
      `noise` — `tests/test_weak_ask.py::test_on_topic_stops_at_top_and_moves_off_topic_to_excluded`;
      оговорки (сбой проверки, нет `area_en`, непроверенный хвост) — в тексте `BR-04`.
- [x] `BR-50`, `BR-09`, `OQ-20`: `WEAK_LISTED_IN_ASK=0` выключает флаг, «несколько игроков»
      не условие и не в доле проверок — `tests/test_weak_ask.py::test_listed_in_ask_flag_parses_value`,
      `::test_players_check_is_neither_entry_nor_scored`.
- [x] Честность: `live_errors` заполняется при отказах источников, патентов, углубления,
      резюме, оценки (§6 A3–A5) и показывается в отчёте (`tz.test.jsx` «вторая строка чисел,
      отказы источников…»).
- [x] Модели ответа — в `result.models`, на странице и в строке лога `runner.py`.
- [x] `ruff check` чист по правилам `pyproject.toml`.
- [x] Документация: техдока §2 «Ход прогона», §5, §6, §9; `docs/architecture.puml`.
- [x] Трассировка: FEAT-02…13, 14 (за флагом; на стенде два подпункта включены), 19, 30, 41;
      BR §8; NFR §8; OQ-11, 16, 17 (открытые), 01, 12, 19, 20 (источники); тесты выше; код
      `api.Worker` (`_run`, `_cancel_requested`), `runner.run_query`, `runner.MODEL_LOCK`,
      `weak/ask.run`, `weak/ask.excluded_why`, `weak/rubric.evidence_stage`,
      `weak/assess.JUDGMENTS`, `events.py`, `jobs.claim/finish/fail/cancelled/cancel_requested/save_report`,
      `failure.explain`, `locks.hold`.
- [ ] Не применяется: раскладка 390–1440 px (экрана нет); `/api/glossary` (новых
      подсказок сценарий не добавляет).
