# Трассировка требований

Артефакт RUP «Traceability»: единственное место, где связи между артефактами набора
перечислены таблицами (08 §7). В текстах сценариев связи дублируются только ссылками на
идентификаторы; при расхождении источник истины — этот файл, а расхождение записывается в
§8.6 и в `09-open-questions.md`.

Базовая линия: репозиторий `trends-anal`, `master@1dded3e` (2026-10-20); одна для всех
разделов, включая §8.6. Пути к тестам и коду — от корня `pelican/`; существование каждого
названного файла и тестовой функции проверено на этой линии, отсутствующие помечены явно.

Как читать: каждая матрица — «строка = один идентификатор, в ячейке — перечень связанных».
Двумерных сеток нет. Источники связей: STR→FEAT — таблица `02` §2; UC→FEAT — таблица
`05` §3; UC→BR, UC→NFR, UC→OQ, UC→тесты, UC→код — разделы §8, §10, §11, §13 файлов
`use-cases/UC-NN-*.md`; OQ→требования — колонка «Затрагивает» в `09`. Пометка «(текст UC)»
у идентификатора означает, что он назван в теле сценария (потоки, предусловия), но не в
разделе «Особые требования» и не в строке трассировки DoD.

Все 32 спецификации сценариев написаны; UC-13 — «Снято» (`c1c4ea8`), в действующих
матрицах §3–§6 его строка помечена «снято» и связей не несёт. Строки всех сценариев сверены
с их разделами «Особые требования» и строками трассировки DoD на базовой линии.

Статусы: Р реализовано, Ф за флагом, Пт прототип, Пр предложено, Снято (README).

## 1. STR ↔ FEAT

Что показывает: какими возможностями продукта закрыт каждый запрос заинтересованной
стороны. Источник — `02` §2. Все 29 запросов покрыты хотя бы одной возможностью.

| STR | Сторона | FEAT | Статус STR |
|---|---|---|---|
| STR-01 | Заказчик | FEAT-01, 11 | Р |
| STR-02 | Заказчик | FEAT-07, 22 | Р |
| STR-03 | Заказчик | FEAT-11, 12 | Р |
| STR-04 | Заказчик | FEAT-12 | Р |
| STR-05 | Заказчик | FEAT-09 | Р |
| STR-06 | Заказчик | FEAT-15, 19 | Р |
| STR-07 | Заказчик | FEAT-03, 04, 06, 11 | Р |
| STR-08 | Заказчик | FEAT-30 | Р |
| STR-09 | Заказчик | FEAT-35, 20 | Р |
| STR-10 | Заказчик | FEAT-24, 11 | Р |
| STR-11 | Заказчик | FEAT-33 | Р |
| STR-12 | Заказчик | FEAT-32 | Р |
| STR-13 | Методолог | FEAT-07 | Р |
| STR-14 | Заказчик (16.10) | FEAT-10, 14, 32 | Р / Ф (`OQ-17`) |
| STR-15 | Аналитик | FEAT-15, 17 | Р |
| STR-16 | Аналитик | FEAT-20, 22, 26 | Р |
| STR-17 | Аналитик | FEAT-11 | Р |
| STR-18 | Аналитик | FEAT-23, 24, 25, 16 | Р |
| STR-19 | Аналитик | FEAT-20, 27 | Р |
| STR-20 | Аналитик | FEAT-37, 38, 39 | Пт |
| STR-21 | Банк | FEAT-30 | Р |
| STR-22 | Банк | FEAT-27 | Р |
| STR-23 | Администратор | FEAT-35, 36 | Р |
| STR-24 | ML-инженер | FEAT-31, 32, 33, 34, 36 | Р |
| STR-25 | Жюри | FEAT-22, 32, 34 | Р |
| STR-26 | Внешний потребитель | FEAT-29 | Р |
| STR-27 | Внешний потребитель / Банк | FEAT-40 | Пр |
| STR-28 | Аналитик | FEAT-41 | Р |
| STR-29 | Администратор | FEAT-42 | Пр |

Обратная сводка (FEAT → STR) нужна для §8.1: возможности, у которых нет ни одного STR, —
FEAT-02, 05, 08, 13, 18, 21, 28, 43, 44. Все они выводятся из ТЗ через другие FEAT
(FEAT-02/05/08/13 — ступени конвейера под STR-01/07; FEAT-18/21 — честность и радар под
STR-15/16) либо из анализа (`OQ-05`, `OQ-07`); в `02` запрос для них не записан. FEAT-28 —
«Снято» (`c1c4ea8`).

## 2. FEAT ↔ UC

Что показывает: какими сценариями реализуется каждая возможность. Первая колонка связей —
таблица `05` §3 (источник истины); вторая — что дополнительно заявляет раздел
трассировки самого UC (расхождение с `05` устраняется правкой `05`).

| FEAT | С | UC по `05` | Дополнительно по тексту UC |
|---|---|---|---|
| FEAT-01 | Р | UC-01 | — |
| FEAT-02 | Р | UC-03 | — |
| FEAT-03 | Р | UC-03 | UC-20, UC-21 называют себя «научным потоком» FEAT-03 (без строки трассировки) |
| FEAT-04 | Р | UC-03 | — |
| FEAT-05 | Р | UC-03 | — |
| FEAT-06 | Р | UC-03 | — |
| FEAT-07 | Р | UC-03 | — |
| FEAT-08 | Р | UC-03 | — |
| FEAT-09 | Р | UC-03, UC-24 | — |
| FEAT-10 | Р | UC-03 | — |
| FEAT-11 | Р | UC-03, UC-05 | — |
| FEAT-12 | Р | UC-03, UC-05 | — |
| FEAT-13 | Р | UC-03 | UC-29 (строка трассировки DoD) |
| FEAT-14 | Ф | UC-26, UC-03 (ветви за флагами, A8), UC-06 (группа «наблюдать») | — |
| FEAT-15 | Р | UC-02, UC-32 | — |
| FEAT-16 | Р | UC-01 | — |
| FEAT-17 | Р | UC-02 | — |
| FEAT-18 | Р | UC-02, UC-32 | — |
| FEAT-19 | Р | UC-03 | — |
| FEAT-20 | Р | UC-04 | — |
| FEAT-21 | Р | UC-04 | — |
| FEAT-22 | Р | UC-06 | — |
| FEAT-23 | Р | UC-07 | — |
| FEAT-24 | Р | UC-08 | — |
| FEAT-25 | Р | UC-09 | — |
| FEAT-26 | Р | UC-11 | — |
| FEAT-27 | Р | UC-10, UC-12 | — |
| FEAT-28 | Снято | UC-13 (снято, `c1c4ea8`) | — |
| FEAT-29 | Р | UC-19 | — |
| FEAT-30 | Р | UC-03 | — |
| FEAT-31 | Р | UC-20, UC-21, UC-22, UC-30 | — |
| FEAT-32 | Р | UC-23, UC-25 | UC-31 (строка трассировки DoD) |
| FEAT-33 | Р | UC-24, UC-31 | — |
| FEAT-34 | Р | UC-26 | — |
| FEAT-35 | Р | UC-27 | — |
| FEAT-36 | Р | UC-28, UC-29 | — |
| FEAT-37 | Пт | UC-14, UC-15 | — |
| FEAT-38 | Пт | UC-16, UC-17, UC-18 | — |
| FEAT-39 | Пт | UC-17, UC-18 | — |
| FEAT-40 | Пр | — | `OQ-04`; расширение UC-19 (§9) |
| FEAT-41 | Р | UC-02 (A7), UC-03 (A11), UC-09 (A6), UC-19 (A7) | UC-01 (строка трассировки DoD), UC-28 (§8, A11); `OQ-12` закрыт |
| FEAT-42 | Пр | — | `OQ-06`; упомянуто в UC-20, UC-30 §9 |
| FEAT-43 | Р (сервер) / Пр (фронт) | UC-09 (фронтовая часть, Пр) | серверная часть (`/api/history` с `q`, `limit`, `offset`) — в UC-19; фронт — `OQ-05` |
| FEAT-44 | Пр | — | `OQ-07`; DoD всех экранных UC |

Обратная сводка (UC → FEAT) — колонка «FEAT» таблицы `05` §3; каждый из 31 действующего
сценария ссылается хотя бы на одну действующую возможность; снятый UC-13 — на снятую FEAT-28.

## 3. UC ↔ BR

Что показывает: какие бизнес-правила обязан соблюдать каждый сценарий. Источник —
раздел «Особые требования» (§8) и строка трассировки в DoD каждого UC; номера,
встречающиеся только в теле, помечены «(текст UC)».

| UC | BR |
|---|---|
| UC-01 | BR-34, 35, 36, 37, 38, 39 |
| UC-02 | BR-31, 35, 40, 41, 42, 44 |
| UC-03 | BR-01…16, 18…23, 27, 28, 29, 31, 32, 33, 34, 40, 42, 43, 44, 45, 47, 50 |
| UC-04 | BR-07, 08, 09, 12, 13, 17, 28, 30, 48; BR-06 (текст UC) |
| UC-05 | BR-04, 09, 10, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 30; BR-03 (текст UC, шаг 3) |
| UC-06 | BR-01, 03, 04, 05, 06, 07, 26 |
| UC-07 | BR-38, 45 |
| UC-08 | BR-06, 07, 18, 25, 28, 45, 48 |
| UC-09 | BR-45 |
| UC-10 | — (вспомогательный; правил не вводит) |
| UC-11 | BR-35, 44 |
| UC-12 | — (вспомогательный; только токены оформления) |
| UC-13 | — (снято, `c1c4ea8`) |
| UC-14 | BR-31, 35, 37, 39, 55 |
| UC-15 | BR-13, 31, 55 |
| UC-16 | BR-21, 24, 26, 28, 29, 42, 53, 56, 57 |
| UC-17 | BR-14, 17, 35, 37, 39, 54, 56, 58 |
| UC-18 | BR-17, 35, 39, 40, 42, 54, 56, 57, 58 |
| UC-19 | BR-35, 36, 37, 38, 39, 40, 41, 42, 43, 45, 47 |
| UC-20 | BR-49 |
| UC-21 | BR-49 |
| UC-22 | BR-49 |
| UC-23 | BR-01, 02, 11, 14, 49, 52 |
| UC-24 | BR-08, 09, 10, 28, 49, 50 |
| UC-25 | BR-49, 50, 52 |
| UC-26 | BR-07, 13, 33, 45, 49, 50, 51, 52 |
| UC-27 | BR-34, 36, 40; BR-28, 42 (текст UC) |
| UC-28 | BR-41, 42, 43, 47; BR-28, 34, 36, 40 (текст UC) |
| UC-29 | BR-03, 09, 10, 13, 21, 22, 28, 38, 45 |
| UC-30 | BR-49 |
| UC-31 | BR-01, 02, 10, 11, 49, 52; BR-08, 09 (текст UC) |
| UC-32 | BR-34, 40, 41, 43, 47 |

Обратная сводка (BR → UC) для §8.4: каждое из BR-01…45 и BR-47…58 привязано хотя бы к
одному сценарию (BR-51 — только к UC-26); BR-46 — «Снято» (`c1c4ea8`), связей не несёт.

## 4. UC ↔ NFR

Что показывает: какие нефункциональные требования проверяются в каждом сценарии.
Источник тот же, что в §3.

| UC | NFR |
|---|---|
| UC-01 | NFR-01, 06, 12, 16, 28, 51; NFR-42 (текст UC, предусловие) |
| UC-02 | NFR-06, 08, 10, 12, 24, 30, 35 |
| UC-03 | NFR-01, 05, 17, 18, 19, 20, 21, 22, 25, 26, 27, 28, 29, 31, 32, 47, 55, 61, 62 |
| UC-04 | NFR-01, 03, 04, 06, 07, 09, 12, 15, 49, 72 |
| UC-05 | NFR-01, 03, 04, 06, 53, 68 |
| UC-06 | NFR-04, 06, 09, 72 |
| UC-07 | NFR-12, 14, 49, 50, 52, 62 |
| UC-08 | NFR-04, 11, 13, 15 |
| UC-09 | NFR-06, 09, 12, 33, 52, 63, 72 |
| UC-10 | NFR-06, 09, 12 |
| UC-11 | NFR-03, 06, 09, 10, 12, 64 |
| UC-12 | NFR-10, 11, 15, 72; NFR-71 («по духу») |
| UC-13 | — (снято, `c1c4ea8`) |
| UC-14 | NFR-06, 09, 12, 16, 27 |
| UC-15 | NFR-06, 07, 12 |
| UC-16 | NFR-01, 06, 12, 13, 22, 26 |
| UC-17 | NFR-06, 09, 12, 16, 28 |
| UC-18 | NFR-17, 18, 19, 27, 28; NFR-52 (текст UC) |
| UC-19 | NFR-27, 28, 30, 33, 34, 50, 51, 52, 67; NFR-58 (текст UC) |
| UC-20 | NFR-23, 36, 41, 57, 59, 60, 69 |
| UC-21 | NFR-26, 32, 36, 47, 48, 56 |
| UC-22 | NFR-41, 48, 57, 59, 60; NFR-31 (текст UC) |
| UC-23 | NFR-26, 39, 44, 66 |
| UC-24 | NFR-39, 44 |
| UC-25 | NFR-26, 39, 44 |
| UC-26 | NFR-26, 27, 36, 39, 40, 44, 61, 62 |
| UC-27 | NFR-36, 37, 41, 42, 48, 50, 51, 54, 56; NFR-17, 32, 38 (текст UC) |
| UC-28 | NFR-18, 19, 25, 38, 50, 51; NFR-17 (A10), 20, 47, 52 (текст UC) |
| UC-29 | NFR-01, 26, 33, 58, 61, 62, 63 |
| UC-30 | NFR-59, 60, 69 |
| UC-31 | NFR-39, 44; NFR-66 (текст UC, по аналогии) |
| UC-32 | NFR-17, 18, 19, 25 |

Обратная сводка (NFR → UC) для §8.4: не привязаны ни к одному сценарию NFR-43, 45, 46,
65, 70; NFR-02 — «Снято» (`c1c4ea8`), связей не несёт; NFR-71 — только «по духу» в UC-12;
NFR-20 — в §8 только у UC-03; NFR-17 покрывает и снятие отменённого при перезапуске
(UC-32 A1, UC-28 A10).

## 5. UC ↔ тесты

Что показывает: чем подтверждается DoD каждого сценария. Пути — к файлам тестов; имена
функций — как в DoD сценария. Все названные файлы и функции существуют на базовой линии,
кроме отмеченных «нет в репозитории» / «создать». «Ручная» — процедура на стенде,
записанная в DoD; автотеста нет. Пометка «(в DoD не назван)» — тест на базовой линии есть и
проверяет поведение сценария, но DoD сценария его не цитирует.

| UC | Автотесты | Ручные процедуры / свидетельства |
|---|---|---|
| UC-01 | `tests/test_api.py`: `test_short_query_rejected`, `test_three_active_per_ip_fourth_rejected`, `test_forwarded_for_limits_funnel_clients`, `test_repeat_query_served_from_history`, `test_stale_report_version_not_served_from_history`, `test_second_request_waits_then_autostarts`, `test_queue_shows_own_queries_only`; `tests/test_jobs.py`: `test_active_limit_per_client`, `test_active_limit_holds_under_concurrency`, `test_same_query_at_once_is_one_job`, `test_fifo_positions_and_autostart_order`, `test_eta_grows_with_queue`; `web/src/test/active.test.jsx` («Сейчас в работе»); `web/src/test/rules.test.jsx` → `describe("главная (NFR-16, UC-01)")` | форма < 3 символов, чипы примеров, тосты 422/429/сеть — ручная; `home.test.jsx` рекомендован, не создан |
| UC-02 | `web/src/test/wait.test.jsx` (5 тестов); `web/src/test/active.test.jsx` (отмена на ожидании); `tests/test_api.py`: `test_events_endpoint_replays_the_run_with_a_cursor`, `test_failure_visible_in_status`, `test_cancel_queued_now_running_at_stage_boundary`; `tests/test_jobs.py`: `test_fifo_positions_and_autostart_order`, `test_events_come_back_in_order_after_the_cursor`, `test_stage_typical_is_the_median_over_done_jobs`, `test_running_snapshot_has_typical_duration`, `test_progress_appends` (оба в DoD не названы); `tests/test_events.py::test_frontend_timeline_has_the_same_stages` | отступ опроса до 20 с, «Задание пропало», `replay` — ручная (вторая вкладка на 10-й минуте); снятие при перезапуске идущего с флагом `cancel` (`jobs.requeue_stale`) — автотеста нет |
| UC-03 | `tests/test_events.py`: `test_real_run_emits_contract_events_in_stage_order`, `test_layers_run_announces_patents_stage`, `test_stage_event_and_contract_check`, `test_checked_logs_but_still_sends`, `test_frontend_timeline_has_the_same_stages`; `tests/test_api.py`: `test_second_request_waits_then_autostarts`, `test_cancel_queued_now_running_at_stage_boundary`, `test_failure_visible_in_status`, `test_system_exit_in_runner_fails_job_and_keeps_worker`, `test_lost_connection_reconnects`, `test_events_endpoint_replays_the_run_with_a_cursor`; `tests/test_jobs.py`: `test_requeue_stale_running`, `test_requeue_stale_drops_events_of_the_abandoned_run`, `test_report_roundtrip`, `test_summaries_read_stored_summary_and_survive_odd_reports`, `test_schema_moves_end_time_back_to_start`; `tests/test_weak_rubric.py`: `test_evidence_stage_climbs_the_ladder_of_milestones`, `test_stage_floor_follows_what_the_evidence_reads`; `tests/test_api.py::test_saved_report_stage_follows_ladder_in_every_list`; `tests/test_weak_layers.py::test_pipeline_splits_trend_weak_and_watch`; `tests/test_weak_ask.py`: `test_only_emerging_goes_to_top`, `test_corroboration_needs_something_above_low`, `test_trust_levels`, `test_assess_parse_drops_uncited_why`, `test_assess_schema_requires_every_judgment`, `test_excluded_get_russian_titles`, `test_excluded_why_names_the_judgments_when_model_why_was_dropped`, `test_card_hypothesis_needs_citation`, `test_card_title_must_be_russian`, `test_russian_summaries_fill_only_foreign_sources_without_one`, `test_russian_summaries_stop_at_deadline_with_a_note`, `test_on_topic_stops_at_top_and_moves_off_topic_to_excluded`, `test_listed_in_ask_flag_parses_value`, `test_players_check_is_neither_entry_nor_scored`; `web/src/test/tz.test.jsx` («вторая строка чисел, отказы источников…») | потолок 1200 с — `scripts/measure_ask.py --tz --repeat 3`; свидетельство `reports/ask-tz.tsv` создаётся на машине стенда и в репозиторий не входит (`.gitignore: reports/*`); полный ТОП в 9 прогонах из 12 — замер без флагов (`OQ-11`), на конфигурации стенда `--tz` не перемерен (`OQ-25`); `requeue_stale` с флагом `cancel`, добор `latin_top` в `russian_titles`, запись `began` исполнителем — автотестов нет |
| UC-04 | `web/src/test/tz.test.jsx`: `describe("экран отчёта")` (2), `describe("стадии")` (2); `web/src/test/links.test.jsx`; `web/src/test/rules.test.jsx`: «место сигнала одно на экран и печать…» (`placeOf`), `describe("адрес отчёта (NFR-12, NFR-49)")` (2), «↓ листает, а Alt+← остаётся браузеру», «Enter и пробел на строке стола открывают карточку»; `tests/test_api.py::test_glossary_serves_the_report_page_texts` | раскладка 390–1440 px — ручная (`OQ-07`); контраст WCAG AA замерен, 0 нарушений (`NFR-72`) |
| UC-05 | `web/src/test/tz.test.jsx`: `describe("карточка сигнала")` (7), `describe("инсайт документом")`; `web/src/test/rules.test.jsx`: «место сигнала одно на экран и печать…», ««держится» — из числа прошлых прогонов отчёта…», ««k из n» проверок — только по доле уверенности…», «пометки доверенности и непроверенных новостей…», «подсказки ⓘ карточки — из глоссария»; `tests/test_weak_ask.py` (правила BR-20, 21, 23), `::test_excluded_why_names_the_judgments_when_model_why_was_dropped`; `tests/test_retext.py::test_fill_why_only_empty_reasons_from_judgments` | раскладка — ручная |
| UC-06 | `web/src/test/tz.test.jsx`: «экран отчёта» (2 теста, `#cutreg`, группа «наблюдать»); `web/src/test/rules.test.jsx`: «место сигнала одно на экран и печать…», «с раскрытым исключённым листание идёт в порядке таблицы»; `tests/test_weak_ask.py` (фильтр мейнстрима), `::test_excluded_why_names_the_judgments_when_model_why_was_dropped` | раскладка таблицы на 390 px — ручная; Enter и пробел на `CutRow` — автотеста нет; контраст замерен, 0 нарушений (`NFR-72`) |
| UC-07 | `web/src/test/links.test.jsx`; `web/src/test/tz.test.jsx` («Открыть как документ»); `web/src/test/rules.test.jsx` → `describe("адрес отчёта (NFR-12, NFR-49)")`; `tests/test_api.py`: `test_second_request_waits_then_autostarts` (`GET /api/reports/{name}` → 200), `test_report_name_outside_br45_is_404`, `test_cancel_queued_now_running_at_stage_boundary` (403 с чужого адреса, A3) | «Поделиться» → приватное окно — ручная; 404 на отсутствующее имя в формате BR-45 — автотеста нет |
| UC-08 | `web/src/test/tz.test.jsx`: `describe("инсайт документом")`, «экран отчёта»; `web/src/test/links.test.jsx` (значок печати); `web/src/test/rules.test.jsx`: «место сигнала одно на экран и печать…», «руководство и печать видят этапы без медиан последнего задания»; `tests/test_events.py::test_frontend_timeline_has_the_same_stages` | PDF ≥ 20 страниц, колонтитулы, бренд ГПБ, титул целиком — ручная |
| UC-09 | `web/src/test/links.test.jsx` (2 теста: строка истории, карточка последнего отчёта); `web/src/test/active.test.jsx` («Сейчас в работе», первые три теста); `web/src/test/rules.test.jsx` → `describe("история (NFR-09, UC-09)")` (2); `tests/test_api.py::test_history_endpoint_has_total_search_and_summary`, `::test_queue_shows_own_queries_only` | группировка, поиск, пустая история, фокус по `/` — ручная; контраст замерен, 0 нарушений (`NFR-72`) |
| UC-10 | `web/src/test/links.test.jsx` «пункт палитры с адресом — ссылка…»; косвенно `tests/test_api.py::test_second_request_waits_then_autostarts`, `::test_repeat_query_served_from_history` (ответы API перехода) | остальные критерии DoD — ручные |
| UC-11 | `tests/test_events.py::test_frontend_timeline_has_the_same_stages` (список стадий раздела `wait`); `web/src/test/rules.test.jsx` «руководство и печать видят этапы без медиан последнего задания» | разделы, оглавление, образец радара, классы `cut` — ручная |
| UC-12 | — (автотестов оформления нет) | все критерии DoD — ручные; `?theme=dark` на стенде не проверен; контраст замерен, 0 нарушений (`NFR-72`) |
| UC-13 | — (снято, `c1c4ea8`; поведение API — в UC-01, UC-02, UC-19) | — |
| UC-14 | **создать**: `tests/test_jobs.py`, `tests/test_api.py`, `tests/test_weak_ask.py` (дополнить); `web/src/test/deepen.test.jsx` — не существует | приёмка 6 `IMPLEMENTATION.md` |
| UC-15 | **создать**: `tests/test_jobs.py`, `tests/test_api.py` (дополнить); `web/src/test/thread.test.jsx` — не существует | приёмка 6 |
| UC-16 | **создать**: `tests/test_article.py`, `web/src/test/article.test.jsx` — не существуют; `tests/test_api.py`, `tests/test_jobs.py` (дополнить) | приёмка 6–7; замер качества статьи по правилу DoR |
| UC-17 | **создать**: `web/src/test/topics.test.jsx` — не существует; `tests/test_jobs.py`, `tests/test_api.py` (дополнить) | приёмка 7 |
| UC-18 | **создать**: `tests/test_jobs.py`, `tests/test_api.py` (дополнить) | приёмка 7 («через минуту») |
| UC-19 | `tests/test_api.py`: `test_second_request_waits_then_autostarts`, `test_repeat_query_served_from_history`, `test_stale_report_version_not_served_from_history`, `test_short_query_rejected`, `test_three_active_per_ip_fourth_rejected`, `test_forwarded_for_limits_funnel_clients`, `test_failure_visible_in_status`, `test_events_endpoint_replays_the_run_with_a_cursor`, `test_cancel_queued_now_running_at_stage_boundary`, `test_queue_shows_own_queries_only`, `test_report_name_outside_br45_is_404`, `test_watchdog_fails_job_without_progress`, `test_watchdog_restarts_dead_worker`, `test_history_endpoint_has_total_search_and_summary`, `test_glossary_serves_the_report_page_texts`; `tests/test_jobs.py`: `test_same_query_at_once_is_one_job`, `test_active_limit_holds_under_concurrency`, `test_history_searches_by_substring_and_pages` (в DoD не назван), `test_prune_events_drops_cancelled_jobs` | `curl` по README; Swagger `/api/docs`; `requeue_stale` с флагом `cancel` (A7) — автотеста нет |
| UC-20 | `tests/test_science_sources.py` (27 функций, перечислены в DoD, в т. ч. `test_second_version_of_a_preprint_is_not_a_second_work`); `tests/test_store.py`: `test_work_id_matches_source`, `test_work_id_aware_equals_naive_utc` | суточный сбор, повтор окна, UC-30 — ручная |
| UC-21 | `tests/test_weak_shards.py`: `test_dates_survive_the_round_trip`, `test_old_shard_has_no_dates_and_that_is_not_an_error`, `test_search_works_across_both_shapes` | докачка `--limit`, идемпотентность, замок (код 75), шард-сервер — ручная |
| UC-22 | `tests/test_store.py::test_work_id_matches_source` | перелив, возобновление, индексы — ручная |
| UC-23 | `tests/test_weak_rubric.py` (5), `tests/test_weak_dataset.py` (3), `tests/test_weak_audit.py` (3) — функции перечислены в DoD | `reports/weak-eval.md` (существует); `judge-precision.tsv`, `judge-coverage.tsv` — в папках прогонов, в репозитории нет; перемер после ревью (`OQ-11`) не сделан |
| UC-24 | `tests/test_train_growth_model.py` (4), `tests/test_weak_model.py` (5) — перечислены в DoD | `reports/weak-growth-holdout.*` (существуют); `src/pelican/weak/growth.json` |
| UC-25 | `tests/test_backtest_classifier.py` (4), `tests/test_retro_hits.py` (4), `tests/test_weak_tech_asof.py` (2) | `reports/bt-*/`, `reports/retro/` — в репозитории нет |
| UC-26 | `tests/test_weak_layers.py`, `tests/test_weak_extra.py`, `tests/test_weak_burst.py`, `tests/test_retro_hits.py`, `tests/test_weak_history.py::test_overlap_uses_same_comparison`, `tests/test_weak_ask.py::test_page_tells_about_spread_or_history`, `::test_page_explains_short_or_empty_top`; сами `scripts/measure_ask.py`, `scripts/measure_arms_tz.py` — ручная процедура | `reports/ask-tz.tsv`, `reports/arms-tz/` — нет в репозитории |
| UC-27 | `tests/test_jobs.py`: фикстура `conn` (`apply_schema` повторно), `test_requeue_stale_running`, `test_requeue_stale_drops_events_of_the_abandoned_run`; `tests/test_api.py::test_forwarded_for_limits_funnel_clients`; `tests/test_api.py::test_static_hashed_assets_immutable_entry_points_revalidated`, `::test_text_responses_gzipped_when_client_accepts` (`NFR-42`; в DoD не названы) | `docker compose up`, `ps`, health, Funnel, шард-сервер — стенд |
| UC-28 | `tests/test_api.py`: `test_failure_visible_in_status`, `test_system_exit_in_runner_fails_job_and_keeps_worker`, `test_lost_connection_reconnects`, `test_watchdog_fails_job_without_progress`, `test_watchdog_restarts_dead_worker`, `test_queue_shows_own_queries_only`, `test_cancel_queued_now_running_at_stage_boundary`; `tests/test_jobs.py`: `test_fifo_positions_and_autostart_order`, `test_prune_events_drops_cancelled_jobs` | 503, строка лога с моделями — стенд; `requeue_stale` с флагом `cancel` (A10) — автотеста нет |
| UC-29 | `tests/test_weak_history.py` (`test_prior_runs_same_query_only_earlier_and_deduped`, `test_prior_runs_capped`, `test_annotate_counts_held_by_direction_and_stable_by_half`, `test_overlap_uses_same_comparison`); `tests/test_jobs.py::test_report_roundtrip`, `::test_history_reads_only_labels_of_the_same_query`, `::test_summaries_read_stored_summary_and_survive_odd_reports`; `tests/test_weak_ask.py::test_saved_report_reads_back_old_and_new`, `::test_stored_report_drops_foreign_script_on_render`, `::test_score_orders_by_model_probability_with_its_predictors`, `::test_page_tells_about_spread_or_history`; `tests/test_retext.py::test_fill_why_only_empty_reasons_from_judgments`, `::test_latin_titles_across_sections`; `tests/test_import_reports.py::test_rule_names_are_kept`, `::test_other_names_become_start_time`; сами `scripts/rerun.py`, `rescore.py`, `recard.py` — без собственных тестов, у `retext.py` и `import_reports.py` покрыты разбор и имя, но не запись | `rerun.py <имя>`, `rescore.py --all`, `import_reports.py` на стенде; перепрогон истории после ревью (`OQ-11`) не сделан |
| UC-30 | — (прямых тестов у скрипта нет); косвенно `tests/test_science_sources.py`: `test_second_version_of_a_preprint_is_not_a_second_work`, `test_arxiv_slices_join_without_gap_or_overlap`, `test_skip_is_refused_where_it_would_lie` | сверка, закрытие дыры, бюджет, уборка версий — ручная |
| UC-31 | `tests/test_weak_zoo.py`, `tests/test_weak_model.py`, `tests/test_weak_dataset.py`; `scripts/train_signal_model.py` — без собственных тестов | `reports/weak-model-zoo.md`, `reports/weak-model.md` |
| UC-32 | `tests/test_api.py`: `test_watchdog_fails_job_without_progress`, `test_watchdog_restarts_dead_worker`, `test_lost_connection_reconnects`; `tests/test_jobs.py`: `test_requeue_stale_running`, `test_requeue_stale_drops_events_of_the_abandoned_run`, `test_prune_events_keeps_only_recent_done_jobs_and_live_ones`, `test_prune_events_drops_cancelled_jobs`, `test_events_go_with_their_job`, `test_stage_typical_is_the_median_over_done_jobs` | `requeue_stale` с флагом `cancel` (A1) — автотеста нет |

Фикстуры: `web/src/test/report.json` (экранные сценарии), база `pelican_test` из
`tests/conftest.py` (API и очередь), `tests/fixtures/` (ответы arXiv/OpenAlex),
`scripts/weak_signals_100.tsv`, `scripts/weak_controls.tsv`, `scripts/weak_controls_hard.tsv`,
`scripts/retro_hits.tsv` — все существуют.

## 6. UC ↔ код (справочно)

Что показывает: модули, в которых живёт поведение сценария. Справочно — источник истины
о коде есть сам код и `../techdoc.md`; здесь только точка входа для чтения. Пути
проверены; у сценариев-прототипов перечислено, что предстоит создать.

| UC | Код |
|---|---|
| UC-01 | `src/pelican/api.py` (`ask`, `_client`), `src/pelican/jobs.py` (`enqueue`, `cached_report`, `active_job`, `queue`, `snapshot`), `web/src/screens/Home.jsx`, `web/src/components/Active.jsx`, `web/src/App.jsx` (`startJob`, `sending`, `openReport`, `nextOpts`, `items`), `web/src/screens/Report.jsx` (`reportHash`), `web/src/appctx.jsx` (`Loading`, `Fail`), `web/src/lib/api.js`, `web/src/lib/timeline.js` (`runText`) |
| UC-02 | `web/src/screens/Wait.jsx`, `web/src/appctx.jsx` (`Loading`, `Fail`), `web/src/lib/live.js`, `web/src/lib/timeline.js`, `web/src/lib/radar.js`, `web/src/components/Radar.jsx`; `src/pelican/api.py` (`job`, `job_events`, `cancel`, `Worker._run`), `src/pelican/jobs.py` (`snapshot`, `stage_typical`, `events`, `cancel`, `requeue_stale`) |
| UC-03 | `src/pelican/api.py` (`Worker._run`, `Worker._cancel_requested`), `src/pelican/runner.py` (`run_query`, `MODEL_LOCK`), `src/pelican/weak/ask.py` (`run`, `excluded_why`), `src/pelican/events.py`, `src/pelican/jobs.py` (`claim`, `finish`, `fail`, `cancelled`, `cancel_requested`, `save_report`), `src/pelican/failure.py` (`explain`), `src/pelican/locks.py` (`hold`), `src/pelican/weak/` (`assess.py` (`JUDGMENTS`, 12 чтений), `rubric.py` (`evidence_stage`), `topic.py`, `tech.py`, `money.py`, `live.py`, `patents.py`, `card.py`, `deepen.py`, `model.py`, `history.py`, `flags.py`), `src/pelican/llm/client.py` |
| UC-04 | `web/src/screens/Report.jsx` (`Result`, `toCandidates`, `onKey`, `isSig`, `reportHash`), `web/src/components/RunNotes.jsx`, `web/src/components/Hint.jsx`, `web/src/lib/format.js` (`placeOf`), `web/src/lib/stages.js`, `web/src/styles/app.css`; `src/pelican/api.py` (`report`, `glossary`), `src/pelican/weak/page.py` (`HINTS`, `LIVE_NAMES`) |
| UC-05 | `web/src/components/Card.jsx` (`Stamp`, `Section`, `Assessments`, `SourcesList`, `SourcesTable`), `web/src/components/Cited.jsx`, `web/src/lib/format.js` (`safeUrl`, `RELEVANCE`, `placeOf`), `web/src/print/Print.jsx` (`SignalDoc`), `web/src/styles/system.css` (`.preds`); `src/pelican/weak/ask.py` (`Signal`, `Source`, `Check`), `src/pelican/weak/model.py` (`phrase`), `src/pelican/weak/trust.py`, `src/pelican/weak/card.py` |
| UC-06 | `web/src/screens/Report.jsx` (`CutRow`, `onKey`, `toCandidates`), `web/src/lib/format.js` (`excludedKey`, `excludedSummary`, `placeOf`, `EXCLUDED_ORDER`, `EXCLUDED_LABEL`), `web/src/print/Print.jsx` (`Doc`); `src/pelican/weak/kinds.py`, `src/pelican/weak/ask.py` (`excluded_why`), `src/pelican/weak/page.py` (`_NO_WHY`) |
| UC-07 | `web/src/App.jsx` (`copyLink`), `web/src/screens/Report.jsx` (`share`, `reportHash`), `web/src/screens/Wait.jsx` (`share`), `web/src/components/Splash.jsx`, `web/index.html` (`sessionStorage pelican-splash`), `web/src/lib/format.js` (`docURL`, `printURL`); `src/pelican/api.py` (`report`), `src/pelican/jobs.py` (`REPORT_NAME`) |
| UC-08 | `web/print.html`, `web/src/print/Print.jsx`, `web/src/print/main.jsx`, `web/src/styles/print.css`, `web/src/lib/radar.js` (`toSVG`), `web/src/lib/format.js` (`printURL`, `docURL`, `candidatesOf`, `placeOf`), `web/src/lib/timeline.js` (`BASE`) |
| UC-09 | `web/src/screens/History.jsx`, `web/src/components/HistoryList.jsx`, `web/src/components/Active.jsx`, `web/src/screens/Home.jsx`, `web/src/lib/format.js` (`fmtStarted`); `src/pelican/api.py` (`history`, `queue`), `src/pelican/jobs.py` (`summaries`, `queue`) |
| UC-10 | `web/src/components/CommandPalette.jsx`, `web/src/App.jsx` (`items`, `setCommands`) |
| UC-11 | `web/src/screens/Guide.jsx`, `web/src/components/Icon.jsx`, `web/src/lib/stages.js`, `web/src/lib/timeline.js` |
| UC-12 | `web/index.html`, `web/src/App.jsx` (`initialTheme`, `toggleTheme`, `toggleBrand`), `web/src/styles/brand-gpb.css`, `web/src/styles/system.css`, `web/print.html` |
| UC-13 | — (снято, `c1c4ea8`: `ui/app.py`, сервис `ui` в `docker-compose.yml` и маршрут `/classic` в `tailscale/serve.json` убраны) |
| UC-14 | нет (прототип `prototypes/ui-v2/`); изменить `src/pelican/api.py`, `jobs.py`, `runner.py`, `weak/ask.py`, `schema.sql`, `web/src/screens/Report.jsx`, `Wait.jsx` |
| UC-15 | нет (прототип); изменить `src/pelican/api.py`, `jobs.py`; создать `web/src/screens/Research.jsx`, `Thread.jsx` |
| UC-16 | нет (прототип); создать `src/pelican/weak/article.py`, `web/src/screens/Article.jsx`; переиспользовать `src/pelican/weak/card.py` (`refs`, `invented_numbers`, `drop_foreign`, `sentences`); изменить `api.py`, `jobs.py`, `schema.sql`; правило `@page` для статьи в `web/src/styles/screens.css` (файл есть, правило создать; образец — `prototypes/ui-v2/shared/screens.css`) |
| UC-17 | нет (прототип); создать `src/pelican/weak/topics.py`, `web/src/screens/Topics.jsx`, `Topic.jsx`; изменить `api.py`, `jobs.py`, `schema.sql` |
| UC-18 | нет (прототип); изменить `src/pelican/api.py` (`Worker`, планировщик), `jobs.py` (`enqueue`, `claim`, `active_job` — присоединение), `schema.sql`; создать `src/pelican/weak/topics.py` |
| UC-19 | `src/pelican/api.py` (все маршруты, `create_app`, `cancel`, `report`), `src/pelican/jobs.py` (`snapshot`, `events`, `summaries`, `queue`, `cancel`, `REPORT_NAME`), `src/pelican/events.py`, `src/pelican/weak/page.py` (`to_json`) |
| UC-20 | `src/pelican/collect.py`, `src/pelican/sources/__init__.py`, `sources/arxiv.py`, `sources/openalex.py`, `sources/base.py`, `sources/_http.py`, `src/pelican/store.py`, `scripts/science_progress.py` |
| UC-21 | `scripts/embed_science.py`, `src/pelican/embed.py`, `src/pelican/shard_server.py`, `src/pelican/weak/shards.py`, `src/pelican/config.py`, `scripts/science_progress.py` |
| UC-22 | `src/pelican/load_corpus.py`, `src/pelican/quack.py`, `src/pelican/schema.sql`, `src/pelican/weak/corpus.py` |
| UC-23 | `scripts/measure_signal_kinds.py`, `scripts/weak_eval_report.py`, `scripts/judge_precision.py`, `scripts/judge_match.py`, `scripts/make_hard_controls.py`, `scripts/label_listed.py`, `scripts/predict_dataset.py`, `src/pelican/weak/dataset.py`, `weak/rubric.py` (`evidence_stage`, `stage_floor`), `weak/assess.py` (`JUDGMENTS`) |
| UC-24 | `scripts/train_growth_model.py`, `src/pelican/weak/model.py`, `src/pelican/weak/growth.json` |
| UC-25 | `scripts/measure_ask.py` (`--as-of`), `src/pelican/weak/asof.py`, `scripts/backtest_signals.py`, `scripts/backtest_classifier.py`, `scripts/backtest_horizons.py`, `scripts/retro_hits.py`, `scripts/judge_match.py`, `src/pelican/weak/core.py` (`SLICES`) |
| UC-26 | `scripts/measure_ask.py`, `scripts/measure_arms_tz.py`, `scripts/retro_hits.py`, `scripts/measure_retro_reach.py`, `scripts/night_demo_measure.sh`, `scripts/summarize_demo_measure.py`, `src/pelican/weak/flags.py`, `weak/ask.py` (точки применения флагов), `weak/layers.py`, `docs/final-measure.md` |
| UC-27 | `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, `tailscale/serve.json`, `src/pelican/api.py` (`create_app`, `WEB_CANDIDATES`, `_SiteStatic`), `src/pelican/jobs.py` (`apply_schema`, `requeue_stale`), `src/pelican/shard_server.py`, `src/pelican/config.py` |
| UC-28 | `src/pelican/api.py` (`health`, `queue`, `job`, `cancel`, `Worker`), `src/pelican/failure.py`, `src/pelican/jobs.py` (`queue`, `cancel`, `requeue_stale`, `prune_events`), `src/pelican/runner.py` |
| UC-29 | `scripts/rerun.py`, `scripts/recard.py`, `scripts/rescore.py`, `scripts/retext.py`, `scripts/import_reports.py`, `src/pelican/jobs.py` (`save_report`, `history`), `src/pelican/weak/history.py`, `src/pelican/schema.sql` (`reports`), `src/pelican/locks.py` |
| UC-30 | `scripts/verify_science_corpus.py`, `scripts/dedupe_arxiv_versions.py`, `src/pelican/sources/arxiv.py`, `sources/openalex.py` |
| UC-31 | `scripts/model_zoo.py`, `scripts/train_signal_model.py`, `src/pelican/weak/model.py`, `weak/model.json`, `weak/dataset.py` (`JUDGMENT_FEATURES` = `assess.JUDGMENTS`); потребители `scripts/predict_dataset.py`, `scripts/backtest_classifier.py` |
| UC-32 | `src/pelican/api.py` (`Worker.start`, `_watch`, `check`, `alive`), `src/pelican/jobs.py` (`fail_stuck`, `prune_events`, `requeue_stale`, `typical_run_s`), `src/pelican/schema.sql` |

## 7. OQ ↔ требования

Что показывает: что затрагивает каждый вопрос реестра, открытый или закрытый. Колонка
«По `09`» — поле «Затрагивает» из реестра; колонка «UC, которые его называют» — раздел §11
сценариев (обратная связь); закрытый вопрос сценарий называет как источник решения.
Колонка «Статус» — из `09`.

| OQ | По `09` («Затрагивает») | UC, которые его называют в §11 | Статус |
|---|---|---|---|
| OQ-01 | BR-09, UC-03, 04, 05, FEAT-09 | UC-03, 04, 05, 06, 23, 24, 31 | Закрыт |
| OQ-02 | BR-13, UC-05 | UC-04, 05, 08, 29 | Закрыт |
| OQ-03 | 01-vision §1, UC-19 | UC-12 («косвенно»); UC-13 (снят: «к сценарию больше не относится») | Открыт |
| OQ-04 | FEAT-40, STR-27, NFR-52, UC-19 | UC-01, 07, 17, 19, 27, 28 | Открыт |
| OQ-05 | FEAT-43, UC-09 | UC-04, 09 | Открыт |
| OQ-06 | FEAT-42, STR-29, UC-20 | UC-20, 27, 30 | Открыт |
| OQ-07 | FEAT-44, NFR-06, DoD экранных UC | UC-02, 04, 08; DoD UC-01, 05, 06, 09, 10, 11, 12 | Открыт |
| OQ-08 | FEAT-37…39, UC-14…18, BR-53…56 | UC-11, 14, 15, 16, 17, 18, 19; §9 UC-01, UC-03 (прототип `parent/focus`) | Открыт |
| OQ-09 | UC-13, UC-19, FEAT-28 | UC-07, 13, 19 | Закрыт |
| OQ-10 | 02, 04 | — | Открыт |
| OQ-11 | 01-vision §7, UC-23, UC-24, UC-25, UC-26 | UC-03, 05, 23, 24, 25, 26, 29, 31 | Открыт |
| OQ-12 | FEAT-41, STR-28, UC-01, 02, 03, 07, 09, 18, 19, 28, 32, BR-40, 47, 06 §7.1, 03-glossary, `diagrams/job-states.puml`, `domain-model.puml`, `uc-03-pipeline.puml` | UC-03, 07, 18, 19, 28, 32 | Закрыт |
| OQ-13 | 03-glossary, NFR-48 | UC-03, 20, 21, 22, 27, 30 | Открыт |
| OQ-14 | 01-vision §1.4, NFR-32 | UC-26, 29, 31 | Открыт |
| OQ-15 | NFR-57 | UC-22 | Открыт |
| OQ-16 | 08 §2 (стабильность), BR-49, NFR-44 | UC-01, 02, 03, 19, 20, 21, 22, 24, 25, 26, 28, 29, 32; UC-30, 31 («в `OQ-16` не входят») | Принято решение |
| OQ-17 | FEAT-14, STR-14, BR-50, UC-26, 03-glossary | UC-03, 06, 23, 25, 26, 31 | Принято решение |
| OQ-18 | BR-45, NFR-62, UC-26, UC-29 | — (UC-29 — в строке трассировки DoD) | Закрыт |
| OQ-19 | BR-04, 03-glossary, UC-03, UC-06, UC-08, UC-11 | UC-03, 06, 08 (UC-11 — в строке трассировки DoD) | Закрыт |
| OQ-20 | 03-glossary, BR-03, UC-03, UC-05 | UC-03, 05 | Закрыт |
| OQ-21 | NFR-72, DoD экранных UC, UC-04, UC-06, UC-09, UC-12 | UC-04, 06, 09, 12 | Открыт |
| OQ-22 | UC-12, NFR-15 | — (UC-12 — в потоке и DoD) | Закрыт |
| OQ-23 | UC-01, 03, 04, 05, 06, 07, 13, 19, 27, 28, 29, FEAT-23, 28, 35, BR-36, 42, 46, NFR-02, 30, 33, 37, 42, 51, 01-vision §4.1, 03-glossary, 05 §1–2 и §5, 06 §7.1, 07, `diagrams/context.puml`, `use-cases.puml` | — | Закрыт |
| OQ-24 | UC-25, UC-26, FEAT-32, STR-14 | UC-25 | Открыт |
| OQ-25 | BR-33, FEAT-19, STR-06, UC-03, UC-26, 01-vision §7, 08 §4.2 | — | Открыт |
| OQ-26 | BR-39, BR-55, UC-14 | — (UC-14 §11 ставит тот же вопрос без номера) | Открыт |
| OQ-27 | UC-24, UC-25, OQ-11, 07 §8.6 | UC-24 | Открыт |

Вопросы без номера, записанные в §11 сценариев и не внесённые в `09`: UC-08 (шрифт с
Google Fonts в закрытом контуре — 1), UC-14 (поведение флажка «Не выдавать заново»;
«новое» по ярлыку или по направлению — 2), UC-15 (отдельный эндпоинт веток или группировка
истории — 1), UC-16 (повторная генерация статьи; формула «N мин чтения»; отдельный
исполнитель — 3), UC-17 (расписание `at_dow`/`at_time`; дубль темы; владение темой — 3),
UC-18 (снимать ли задание темы при паузе; прогон темы как шаг ветки; причина `gone` — 3).
Итого 13. Третий вопрос UC-14 (ключ присоединения углубления, E4) внесён в `09` как
`OQ-26`, но сценарий его номером не называет. Строки «Прототип расходится с требованием» в
§11 UC-14…18 — не вопросы, а расхождения прототипа (§8.6).

## 8. Пробелы покрытия

Проверка полноты по 08 §7: каждый FEAT покрыт UC, каждый UC ссылается на FEAT, каждый BR и
NFR привязан к UC или помечен «сквозное». Ниже — всё, что этой проверке не отвечает.

### 8.1 STR без FEAT; FEAT без STR

- STR без FEAT: **нет**.
- FEAT без STR (9): FEAT-02, 05, 08, 13, 18, 21, 28, 43, 44. FEAT-02, 05, 08, 13, 18,
  21 — внутренние ступени конвейера и элементы интерфейса, выведенные из STR-01/07/15/16 без
  собственного запроса; FEAT-28 — «Снято» (`c1c4ea8`); FEAT-43/44 — предложения аналитика
  (`OQ-05`, `OQ-07`) без стороны-заказчика.

### 8.2 FEAT без UC

- FEAT-40, 42, 44 — со статусом «Предложено»; сценариев нет, решение за заказчиком
  (`OQ-04`, `OQ-06`, `OQ-07`). Для FEAT-40 точка входа — расширение UC-19 / UC-01; для
  FEAT-42 — UC-20 / UC-30; для FEAT-44 — DoD всех экранных сценариев.
- FEAT-28 — «Снято» вместе со своим сценарием UC-13 (`c1c4ea8`); в пробел не входит.
- FEAT-43 — серверная половина реализована (`/api/history` с `q`, `limit`, `offset`,
  UC-19), фронт берёт `limit=1000` и ищет на клиенте (`OQ-05`); фронтовая половина
  записана в UC-09 как предложение, не как реализованное поведение.
- UC без FEAT: **нет** (все 32 строки таблицы `05` заполнены; у снятого UC-13 — снятая
  FEAT-28).

### 8.3 UC без автотестов

Реализованные сценарии, у которых в DoD нет ни одного автотеста (только ручные
процедуры): **UC-12**. Реализованные с одним автотестом: **UC-10** (пункт палитры —
ссылка, `links.test.jsx`; остальное ручное), **UC-30** (косвенно — арифметика окон
докачки). У **UC-11** автотестами покрыты только этапы раздела `wait` (`test_events.py`,
`rules.test.jsx`). У **UC-01** не покрыты форма главной (< 3 символов, чипы, тосты), у
**UC-02** — отступ опроса, «Задание пропало» и `replay`, у **UC-07** и **UC-08** — ключевая
часть (буфер обмена, PDF) только ручная.
Пробелы автотестов в отдельных ветках реализованного поведения:
- снятие брошенного `running` с флагом `cancel` в `jobs.requeue_stale` → `cancelled`
  (`NFR-17`; UC-02, UC-03, UC-19 A7, UC-28 A10, UC-32 A1) — `test_requeue_stale_running`
  проверяет только возврат без флага;
- добор `latin_top` в `ask.russian_titles` и запись `began` исполнителем (UC-03);
- 404 на отсутствующее в `reports` имя правильного формата BR-45 (ветка `row is None` в
  `api.report`, UC-07);
- Enter и пробел на строке исключённого `CutRow` (UC-06; тест есть только для строки стола).
UC-13 снят (`c1c4ea8`) и в проверку не входит.
Прототипы **UC-14…18**: все тесты в DoD помечены «создать», ни одного файла нет.
У **UC-26, UC-29, UC-31** скрипты (`measure_ask.py`, `measure_arms_tz.py`, `rerun.py`,
`rescore.py`, `recard.py`, `train_signal_model.py`) собственных тестов не имеют; у
`retext.py` и `import_reports.py` покрыты разбор и имя, но не запись; покрытие идёт через
тесты вызываемых модулей. Тесты `test_running_snapshot_has_typical_duration`,
`test_progress_appends` (UC-02), `test_history_searches_by_substring_and_pages` (UC-19),
`test_static_hashed_assets_immutable_entry_points_revalidated` и
`test_text_responses_gzipped_when_client_accepts` (`NFR-42`, UC-27) есть, но DoD
сценариев их не цитирует.

### 8.4 BR и NFR, на которые не ссылается ни один UC

- BR: нет (BR-51 — в UC-26; BR-46 — «Снято»).
- NFR без привязки к UC (NFR-02 — «Снято», в счёт не входит):
  - **NFR-43** (документация без истории), **NFR-65** (техдока и `architecture.puml`
    актуальны) — сквозные правила проекта; помечать «сквозное»;
  - **NFR-45** (три слоя), **NFR-46** (стек) — ограничения проектирования; не названы
    даже в UC-27, где три слоя разворачиваются; помечать «сквозное» или добавить в UC-27;
  - **NFR-70** (условия EPO OPS) — стадия патентов в UC-03 не ссылается; кандидат в §8
    UC-03;
  - **NFR-71** (лицензии) — только «по духу» в UC-12; помечать «сквозное».
- Только в теле, без §8/DoD: NFR-58 (UC-19), NFR-42 (UC-01), NFR-17/32/38 (UC-27),
  NFR-17/20/47/52 (UC-28), NFR-31 (UC-22), NFR-52 (UC-18); BR-06 (UC-04), BR-03 (UC-05),
  BR-28/42 (UC-27), BR-28/34/36/40 (UC-28), BR-08/09 (UC-31).

### 8.5 OQ без привязки

- Не названы в §11 ни одного UC: **OQ-10** (текст ТЗ отсутствует; затрагивает 02 и 04
  целиком) — вопрос к документам, привязка по `09` достаточна; **OQ-25** (цель 1200 с на
  конфигурации стенда) — затрагивает UC-03 и UC-26 по `09`, но их §11 его не называют;
  **OQ-26** (ключ присоединения углубления) — UC-14 §11 ставит тот же вопрос без номера.
  Закрытые OQ-18 (DoD UC-29) и OQ-22 (поток и DoD UC-12) названы вне §11; закрытый
  OQ-23 (сверка пакета с базовой линией) сценарии не называют.
- 13 вопросов без номера в §11 сценариев (см. §7) — не зарегистрированы в `09`, и их
  статус не отслеживается.

### 8.6 Расхождения кода с требованиями

Что показывает: где код базовой линии (`master@1dded3e`) нарушает требование, которое
остаётся в силе. У сценария соответствующий пункт DoD снят (`[ ]`, «код расходится») и сценарий
считается Detailed, а не Validated (08 §4). Строка снимается, когда код приведён к правилу;
история — в git. Колонка OQ заполнена там, где нужно решение, а не починка.

| Требование | Код | В чём расхождение | UC / DoD | OQ |
|---|---|---|---|---|
| 08 §4.2, NFR-27, BR-33 | `scripts/measure_ask.py --tz` | полный ТОП (15 позиций) дали 9 прогонов из 12 — замер без флагов (01 §7); на конфигурации стенда (`WEAK_LAYERS=1`, `WEAK_EXTRA_SOURCES=1`) `--tz` не перемерен, медиана ≈ 1214 с по одному прогону на плечо (`OQ-25`) | UC-03, UC-26 | OQ-11, OQ-25 |
| BR-33, FEAT-19 | `src/pelican/weak/ask.py` (`run` → `_split_layers` без `deadline`) | со слоями патенты пула считаются без срока бюджета; функция срок принимает, вызов его не передаёт: срок срабатывает в большинстве прогонов стенда, и без патентов кандидаты теряют подтверждение слоя — ТОП сдвинулся бы против замера Big Tech 20 → 10. Включать — с перемером ТОП. На стенде `WEAK_LAYERS=1` | UC-03 (DoD) | OQ-25 |
| UC-24 E3 | `scripts/train_growth_model.py` (требование только в докстринге, `:39-40`) | сверки `HORIZON_YEARS`/`SLICES` обучающих и проверочных срезов нет; разделы openalex у срезов различаются по построению (`slices_at`), что делать при расхождении — не решено | UC-24 | OQ-27 |

Не расхождение кода, а предложенное требование: **NFR-72** («Пр», `OQ-21`) — код ему
соответствует: контраст WCAG 2.1 AA замерен (способ и охват — в NFR-72, экран ожидания не
замерен), клавиатурная часть (Enter и пробел на строках) покрыта `rules.test.jsx`. Открыт
только уровень WCAG как требование заказчика (`OQ-21`).

Прототип этапа 6 (`prototypes/ui-v2/`) — не код базовой линии; его расхождения с
требованиями, которые остаются в силе (макет доработать или принять расхождение при DoR
UC-14…18):
- штамп «новое» ставится только в строке стола (`pelican.html:436-443`), радар получает
  отчёт без пометок (`pelican.html:490`) — UC-14 шаг 13;
- тема из флажка «Следить…» диалога углубления создаётся без `thread_id`
  (`shared/research.js:375`) — UC-14 A4, UC-17 A1;
- переходы к отчётам, веткам и сигналам — `<button>`, а не `<a href>` (`pelican.html:237`;
  `shared/research.js:118`, `:120`, `:128`, `:188`, `:272`, `:277`) — `NFR-12`; UC-15, UC-16,
  UC-17;
- у первого прогона темы выпуск не исключён: `finishTopic` сравнивает с пустым списком и
  решает по порогу (`shared/research.js:299-314`) — `BR-56`, UC-18 A1;
- `gone` пишется как `{title, why}` без `label` и с одной причиной «нет в этом прогоне»
  (`shared/research.js:310`) — UC-17 §8, UC-18 шаг 5;
- у выпуска темы — плашка статьи по ветке вместо плашки выпуска
  (`shared/research.js:214-215`) — UC-16 A2.
