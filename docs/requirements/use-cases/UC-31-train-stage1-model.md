# UC-31 Обучить модель этапа 1 и выбрать её в зоопарке

## 1. Шапка

| Атрибут | Значение |
|---|---|
| ID | UC-31 |
| Название | Обучить модель этапа 1 и выбрать её в зоопарке |
| Пакет | E. Качество и модели |
| Первичный актор | ML-инженер |
| Вторичные акторы | нет (обучение офлайн на TSV признаков; сервер моделей нужен только на этапе признаков — UC-23) |
| Приоритет | Must |
| Статус | Реализовано |
| Стабильность | Высокая: правило парсимонии (`model_zoo.REFERENCE = "lr_l1 / N+J"`, `ALPHA = 0.05`) и пороги годности `STAGE1_ACC = 0.80`, `STAGE1_AUC = 0.90` записаны до прогона и следуют порогу ТЗ 75–80 %; Средняя у правила второго вопроса (`GAIN_MIN = 3` — потолок 8 FN, `AUC_MIN = 0.96` — от recall и specificity LLM) |
| Риск | Высокий: 130 строк, контроль лёгкий и размечен командой — порог годности на нём проходится легче, чем на закрытой разметке; на ретроспективном бэктесте модель вперёд не смотрит (AUC 0.48 / 0.38 при поле 0.57, UC-25) и в живой выдаче не считается |
| Источник | ТЗ, схема 1, этап 1: «обучить модель, обосновать критерии отбора признаков, математически отделять ранние индикаторы от шума и зрелых технологий, feature importance» (02, A); техдока §7, §9; `scripts/model_zoo.py`, `scripts/train_signal_model.py`, `src/pelican/weak/model.py` |
| Проверено на | `master@1dded3e`; `reports/weak-model-zoo.md` и `reports/weak-model.md` (в git) сгенерированы 18.09.2026; `src/pelican/weak/model.json` (`trained: 2026-09-18`, `winner: lr_l1 / N+J`, 29 признаков = 20 `N` + 9 `J`, `gate.passed: true`) обучена на прежнем наборе чтений: в коде `J` = 12 (`assess.JUDGMENTS` с вехами стадии `prototype_built`, `pilot_with_customer`, `generally_available`, `3f5dc56`), при переобучении `N+J` = 32; не перемерено |

## 2. Краткое описание

ТЗ требует обученную модель с обоснованием признаков и их важностью. ML-инженер по TSV
признаков датасета (UC-23, плечо `corpus` с `--features-out --embed-features`) прогоняет
зоопарк: 9 оценщиков scikit-learn (плюс TabPFN, если пакет установлен и не задан
`--no-tabpfn`) × наборы признаков без вердикта
LLM — числа конвейера `N`, атомарные чтения свидетельств `J`, соседство в векторах `E` — на
одних и тех же фолдах; выбирает модель **правилом парсимонии** (опорная L1-логистическая
регрессия на `N+J`; нелинейное семейство заменяет её, только если бьёт по corrected resampled
t-test с поправкой Холма); обучает победителя с вложенной кросс-валидацией, калибровкой
Platt и permutation importance и ставит его в `src/pelican/weak/model.json` только при
выполненном **правиле годности** (accuracy ≥ 0.80, balanced accuracy ≥ 0.80, AUC ≥ 0.90).
Применение — на numpy без sklearn (`weak/model.py`): `load()` отказывается грузить модель,
не прошедшую правило. Вердикт LLM (`kind`) в признаки не входит: с ним классификатор выучивает
any-yes, а важность признаков говорит только «важен голос».

## 3. Предусловия

- `reports/weak-features-corpus.tsv` получен замером UC-23 (шаг 4) с колонками
  `weak/dataset.FEATURE_NAMES`, `JUDGMENT_FEATURES`, `EMBED_FEATURES`, `set` (`pos`/контроль)
  и тремя голосами LLM `llm_papers`, `llm_mixed`, `llm_corpus` у **каждой** строки (зоопарк
  иначе не стартует, E1).
- `reports/weak-label-vectors-corpus.npz` — для контрольной линии `probe` (без него строка
  `probe` пропускается).
- Установлены `.[ml]` (scikit-learn, scipy); TabPFN — только если пакет установлен: без него
  или с `--no-tabpfn` строка `tabpfn` в зоопарке пропускается («tabpfn не измерен»), на
  вердикт этапа 1 это не влияет.
- Контрольных строк не меньше 10 (`train_signal_model.py`, E2).
- Контракт признаков не менялся без обновления `weak/dataset.py` и
  `tests/test_weak_model.py::test_shipped_params_match_feature_contract`.

## 4. Триггер

Изменение набора признаков `weak/dataset.py`, новый замер датасета (UC-23: изменился промпт
оценки или состав свидетельств), трудный контроль (UC-23 A3), подготовка сдачи этапа 1,
перемер после исправлений ревью (`OQ-11`).

## 5. Основной поток

1. ML-инженер получает признаки (UC-23):
   ```bash
   python scripts/measure_signal_kinds.py --evidence corpus --features-out --embed-features
   ```
2. ML-инженер сверяет правила, записанные в докстринге `model_zoo.py` до прогона: парсимония —
   `REFERENCE` остаётся, пока кандидат не бьёт его по F1 на тех же фолдах с p < `ALPHA`
   после Холма (one-standard-error rule); годность — `STAGE1_ACC` для accuracy и balanced
   accuracy, `STAGE1_AUC`; второй вопрос «помогает ли связка с LLM» — медиана чистой
   прибавки ≥ `GAIN_MIN`, p < 0.05 после Холма, AUC ≥ `AUC_MIN`. Сравнение с gemma
   печатается рядом без правила «должен превзойти»: вопрос этапа 1 — годна ли модель.
3. ML-инженер запускает зоопарк:
   ```bash
   python scripts/model_zoo.py                        # → reports/weak-model-zoo.md/.json
   python scripts/model_zoo.py --repeats 3 --no-tabpfn # быстрая отладка
   ```
4. Система читает TSV (`y = set == "pos"`), печатает базовые линии без обучения (LLM по
   каждому плечу, any-yes, большинство, persistence трёх плеч — FN / FP / F1); строит
   `RepeatedStratifiedKFold(10 × repeats, SEED = 20260917)` — одни фолды у всех кандидатов;
   для каждого оценщика × набора (`SETS`) обучает конвейер «импутация медианой →
   стандартизация → GridSearch» внутри фолда и считает `summarize`: F1 с разбросом по повторам,
   AUC, accuracy, balanced accuracy, recall, specificity, прибавку против LLM (исправлено −
   сломано), corrected resampled t (Nadeau–Bengio) и точный McNemar; вложенный выбор
   (Cawley–Talbot) — оценка процедуры, а не лучшей клетки; `probe` — LR на 768 координатах
   вектора названия — контрольная линия, не кандидат.
5. Система выносит вердикт этапа 1 (`stage1_verdict`): кандидаты — только `STAGE1_SETS`
   (`N`, `J`, `N+J`, `N+J+E`) без `probe`; разность F1 по фолдам против опорной, corrected t,
   Холм; победитель — лучший из значимо бьющих, иначе `REFERENCE`; `passed` — пороги
   годности; пишет `reports/weak-model-zoo.json` (`stage1`, `stage1_rule`, `verdict`, `rule`,
   `sets`, `folds`, `repeats`) и `reports/weak-model-zoo.md` (таблица кандидатов с «← модель
   этапа 1» и p против опорной; второй вопрос).
6. ML-инженер читает вердикт (базовая линия: модель — `lr_l1 / N+J`, порог выполнен, опорную
   значимо не бьёт никто; связка с LLM никому не помогает — `predict_dataset.py` остаётся на
   any-yes трёх плеч).
7. ML-инженер обучает победителя:
   ```bash
   python scripts/train_signal_model.py --from-zoo     # семейство и набор из stage1.winner
   python scripts/train_signal_model.py                # без зоопарка — опорная lr_l1 / N+J
   ```
8. Система берёт `stage1.winner` из `weak-model-zoo.json`; `select_features` выбрасывает
   признак, пустой у всех строк, и называет его в `dropped`, а признак, постоянный внутри
   положительных (`standard_or_regulation`), оставляет и называет вслух — это класс
   исключения ТЗ, а не условие отбора; печатает константу «всё — сигнал» (accuracy 0.77,
   F1 0.87, AUC 0.50 — метрика ниже неё результатом не является); стратифицированная
   5-кратная CV × 10 повторов, гиперпараметр — nested CV, импутация и стандартизация внутри
   фолда; Platt на out-of-fold отступах, Brier до и после, таблица надёжности из 5 бинов;
   permutation importance (5 фолдов × 20 перестановок) и коэффициенты LR; дерево глубины 3
   как читаемое правило; рядом — `lr_l1 / N`, `lr_l1 / J` и gemma в одиночку; печатает
   «правило годности … ВЫПОЛНЕНО / НЕ выполнено»; пишет `reports/weak-model.md` и
   `reports/weak-model.json` (`gate.passed`).
9. При выполненном правиле ML-инженер устанавливает модель:
   ```bash
   python scripts/train_signal_model.py --from-zoo --install   # → src/pelican/weak/model.json
   ```
   Система копирует `reports/weak-model.json` в `INSTALL_JSON` (и `weak-model.pkl` рядом,
   если победитель не регрессия); при невыполненном — «⚠️ правило не выполнено — не копирую».
10. Система применяет модель без sklearn: `weak/model.load()` → параметры или `None`
    (нет файла, `gate.passed` ложь, pickle не грузится); `probability(features, params)` —
    импутация медианой обучения, z-значения, `coef · z + intercept`, сигмоида Platt — и
    `TOP = 3` предиктора из того же разложения (вклад = вес × z). Потребители:
    `scripts/predict_dataset.py` (колонка `probability`, UC-23 A5) и
    `scripts/backtest_classifier.py` (модель этапа 1 на срезе, UC-25 шаг 7). В живой выдаче
    модель не считается (техдока §7, §9).
11. ML-инженер обновляет техдоку §7 (веса: «название — область, а не технология» −2.40,
    «подаётся как новое» +1.23, «рутинный инструмент» −1.11, «стандарт или регламент» −1.06;
    F1 0.99, AUC 0.995) и §9 (строка модели этапа 1) и запускает `pytest`.

## 6. Альтернативные потоки и потоки исключений

**A1. Трудный контроль** (шаг 7) — `--extra reports/weak-features-corpus-weak_controls_hard.tsv`
(строки добавляются по ключу `(set, n)`, можно несколько раз); `--score <tsv>` — непроверенные
кандидаты трудного контроля не обучают модель, а только оцениваются готовой — таблицей в
отчёт. Specificity на лёгком контроле (1.00 у LLM) означает, что любая связка может только
терять точность, и видно это будет лишь на трудном контроле.

**A2. Нелинейный победитель** (шаги 5, 9). Семейство не регрессия → `estimator: "pickle"`,
`weak-model.pkl` рядом с JSON, важность — permutation importance × знак («грубая подсказка»);
`load()` требует joblib и sklearn, иначе возвращает `None` — выдача без вероятности лучше
выдачи, упавшей на импорте.

**A3. Второй вопрос — связка с LLM** (шаг 5). Наборы `N+L1` (голос `corpus`), `L3` (три голоса и
стадии), `N+L3` — голоса как признаки (stacking; голоса не обучаются, утечки в CV нет).
Потолок помощи назван до прогона: у LLM `corpus` FN 8, FP 0 — чинить есть что на восьми
строках. Итог «никто не помогает» — тоже результат; `--vote any` в `predict_dataset.py`
остаётся self-consistency трёх плеч.

**A4. Частичный перегон** (шаг 3) — `--only est1,est2`: результат вливается в готовый
`weak-model-zoo.json` на тех же фолдах (по `SEED`), вложенный выбор не пересчитывается;
`--skip` исключает оценщики.

**E1. Не у всех строк три голоса LLM** (шаг 4). «⚠️ не у всех строк есть три голоса LLM —
прогнать все три плеча замера», выход 1 — прогнать UC-23 по плечам `papers`, `mixed`, `corpus`.

**E2. Контрольных строк меньше 10** (шаг 8). «контрольных строк N: обучать не на чем», выход 1.

**E3. Пустой `--extra`** (A1). «<файл>: ни одной строки — трудного контроля нет», выход:
иначе отчёт подписывал бы модель как обученную с трудным контролем.

**E4. Правило годности не выполнено** (шаг 9). Модель не ставится; `gate.passed: false` в
отчёте; даже скопированная руками, она не загрузится
(`tests/test_weak_model.py::test_load_refuses_model_that_failed_the_gate`), и `predict_dataset.py`
отдаёт пустую `probability`. Пороги не крутятся — результат записывается.

**E5. Нет `reports/weak-model-zoo.json` при `--from-zoo`** (шаг 8). «нет …: сначала
scripts/model_zoo.py», выход 1.

**E6. Контракт признаков разошёлся** (шаг 11). `test_shipped_params_match_feature_contract`
красный: имена не из `weak/dataset.py`, вердикт LLM среди признаков или длины
`impute_median` / `scale_mean` / `scale_std` не равны числу признаков — переобучить, не
править JSON.

## 7. Постусловия

- Успех: `reports/weak-model-zoo.md/.json` с вердиктом этапа 1 и ответом на второй вопрос;
  `reports/weak-model.md/.json` с правилом годности; `src/pelican/weak/model.json` обновлён
  только при `passed`; `pytest` зелёный; техдока §7, §9. Базовая линия: `lr_l1 / N+J`,
  29 признаков; F1 0.99 [0.98; 0.99], accuracy 0.98, balanced 0.98, AUC 1.00 [0.99; 1.00],
  specificity 0.97 [0.95; 0.99] (Уилсон по сводным счётчикам); L1 обнулила 19 признаков из 29.
- Отказ (правило не выполнено или бьющего кандидата нет при провале опорной): отчёты
  записаны с «НЕ выполнено», `model.json` прежний.

## 8. Особые требования

- BR-01, BR-02 (класс по свидетельствам: модель читает свидетельства — чтения `J`, решение
  принимает классификатор; вердикт LLM в признаках отсутствует), BR-10 (тот же механизм
  `weak/model.probability`: вероятность и предикторы — одно линейное разложение), BR-11
  (балл стадия + тренд в выход не идёт), BR-49 (пороги и правила записаны до прогона;
  константы без замера помечены догадками), BR-52 (разброс по повторам CV подписан как
  изменчивость оценки, не доверительный интервал; Уилсон — для сводных счётчиков; «контроль
  написан нами и лёгкий» — в каждом отчёте).
- NFR-39, NFR-44; отчёты `weak-model-zoo.md`, `weak-model.md` генерируются скриптами, не
  руками (как NFR-66 для `weak-eval.md`).
- Одни фолды у всех кандидатов; corrected resampled t вместо наивного парного t (фолды делят
  обучающие данные); Холм по числу кандидатов (~40, лучший по внешней CV лучший отчасти
  случайно).
- Импутация и стандартизация — внутри фолда; `None` в признаке — «измерить нечем», не ноль.
- Калибровка Platt привязана к доле положительных выборки (100 : 30): на потоке с другой долей
  зрелого вероятность смещена — prior shift, свойство калибровки.
- Применение в выдаче и в `predict_dataset.py` — numpy, без sklearn; формат JSON общий с
  `growth.json` и `listed.json` (UC-24).

## 9. Точки расширения и включения

- Вход — UC-23 шаг 4 (`--features-out --embed-features`), UC-23 A3 (трудный контроль).
- Выход `weak/model.json` → UC-23 A5 (`predict_dataset.py`, колонка `probability`), UC-25
  шаг 7 (`backtest_classifier.py` — модель этапа 1 на срезе), UC-24 шаг 4 (базовая линия
  «модель этапа 1» рядом с моделью роста).
- В UC-03 не включается: в живой выдаче вероятность модели этапа 1 не считается; порядок и
  уверенность — модель роста (UC-24, BR-08/BR-09).

## 10. Данные и интерфейсы

| Элемент | Путь / значение |
|---|---|
| Зоопарк | `scripts/model_zoo.py [--features TSV] [--repeats 10] [--no-tabpfn] [--skip a,b] [--only a,b]`; `FOLDS = 10`, `SEED = 20260917` |
| Правила зоопарка | `REFERENCE = "lr_l1 / N+J"`, `ALPHA = 0.05`, `STAGE1_ACC = 0.80`, `STAGE1_AUC = 0.90`; второй вопрос — `GAIN_MIN = 3`, `AUC_MIN = 0.96` |
| Наборы | `STAGE1_SETS`: `N` = `FEATURE_NAMES` (20), `J` = `JUDGMENT_FEATURES` (12: 9 чтений и 3 вехи стадии; установленная `model.json` обучена на 9), `N+J`, `N+J+E` (+ `EMBED_FEATURES`); `SETS` добавляет `N+L1`, `L3` = `LLM_FEATURES`, `N+L3`; `VECTORS = reports/weak-label-vectors-corpus.npz` |
| Оценщики | `estimators()`: `lr_l1`, `lr_l2`, `svm_rbf`, `knn`, `nb`, `tree`, `rf`, `et`, `hgb`, `tabpfn` (опционально), `probe` (контроль) |
| Выход зоопарка | `reports/weak-model-zoo.md`, `reports/weak-model-zoo.json` (`stage1.winner`, `stage1.passed`, `stage1.beats_reference`, `p_vs_reference`, `stage1_rule`) |
| Обучение | `scripts/train_signal_model.py [--features] [--extra TSV]… [--score TSV]… [--from-zoo] [--install]`; `FOLDS = 5`, `REPEATS = 10`; `GATE_ACC = zoo.STAGE1_ACC`, `GATE_AUC = zoo.STAGE1_AUC` |
| Выход обучения | `reports/weak-model.md`, `reports/weak-model.json` (`trained`, `source`, `winner`, `feature_set`, `rows`, `features`, `dropped`, `impute_median`, `scale_mean`, `scale_std`, `platt {a, b}`, `metrics`, `pooled`, `permutation`, `gate {accuracy, auc, passed}`, `constant`, `log1p`, `compare`, `llm_alone`, `reliability`, `brier`, `coef`, `intercept`; для нелинейного — `estimator: "pickle"`, `pickle`), `reports/weak-model.pkl` |
| Установка | `INSTALL_JSON = src/pelican/weak/model.json` (+ `weak-model.pkl` рядом) |
| Применение | `weak/model.PARAMS`, `load(path)`, `probability(features, params) → Prediction`, `TOP = 3`, `phrase(name, value, part)`, `FEATURE_LABELS`, `LOG_FEATURES` (`log1p` перед стандартизацией) |
| Признаки | `weak/dataset.py`: `FEATURE_NAMES`, `JUDGMENT_FEATURES` (= `assess.JUDGMENTS`), `EMBED_FEATURES` (`weak/neighbours.py`), `LLM_FEATURES`, `FEATURE_LABELS`; `None` — «измерить нечем» |
| Потребители | `scripts/predict_dataset.py` (`probability`), `scripts/backtest_classifier.py` |

## 11. Открытые вопросы

- `OQ-11` — признаки и модель не перемерены после исправлений ревью; `J` расширен до 12
  (`3f5dc56`), установленная модель — на 9.
- `OQ-17` — модель этапа 1 отделяет от классов исключения датасета, а не «зарождающееся от
  слабого сигнала»; мерило заказчика не определено.
- `OQ-01` (закрыт, `6d42c3a`) — в CSV `predict_dataset.py` колонки `checks_share` и
  `probability` — разные величины и не `confidence` страницы.
- `GAIN_MIN = 3` (потолок — 8 FN у LLM `corpus`), `AUC_MIN = 0.96` ((recall 0.92 +
  specificity 1.00) / 2 у LLM по бинарному ответу) и `SEED` (фиксация фолдов) — пороги,
  выведенные из замера, и выбор, не догадки; в `OQ-16` не входят (комментарии в `model_zoo.py`).
- `OQ-14` — скрипты ссылаются на `docs/weak-measurements.md`, `docs/weak-audit.md`,
  `docs/todo.md`, которых нет в `pelican/docs/`.

## 12. Definition of Ready

- [x] Правило парсимонии и пороги годности выписаны до прогона (докстринг `model_zoo.py`);
      порог ТЗ 75–80 % назван как источник `STAGE1_ACC`.
- [ ] TSV признаков получен плечом `corpus` с `--features-out --embed-features`; у каждой
      строки три голоса LLM; дата и коммит замера записаны — для нынешнего `J` = 12 не
      перемерен (установленная модель — на TSV от 18.09.2026 с 9 чтениями, `OQ-11`).
- [x] Контракт признаков закреплён тестом `test_shipped_params_match_feature_contract`;
      вердикт LLM в кандидаты этапа 1 не входит (тот же тест).
- [x] Числа привязаны: `REFERENCE`, `ALPHA = 0.05`, `STAGE1_ACC = 0.80`, `STAGE1_AUC = 0.90`,
      `GAIN_MIN = 3`, `AUC_MIN = 0.96`, `FOLDS`, `REPEATS`, `SEED` (`model_zoo.py`,
      `train_signal_model.py`).
- [ ] Для A1: файл трудного контроля непустой — `scripts/weak_controls_hard.tsv` содержит
      только заголовок; оговорка «контроль размечен нами» есть в каждом отчёте.
- [x] Не применяется: сервер моделей и замок модели (офлайн на TSV), контракт событий,
      экран и макет, фикстура `report.json`.

## 13. Definition of Done

- [x] Автотесты зелёные: `tests/test_weak_zoo.py::test_corrected_t_inflates_variance_against_naive`,
      `::test_mcnemar_counts_only_discordant_rows`, `::test_holm_is_monotone_and_capped`,
      `::test_stage1_keeps_reference_unless_beaten_significantly`,
      `::test_stage1_gate_fails_below_threshold_and_without_reference`,
      `::test_usable_drops_only_all_empty_columns`, `::test_summarize_gain_is_fixed_minus_broken`;
      `tests/test_weak_model.py::test_probability_is_platt_of_linear_margin`,
      `::test_binary_judgment_phrase_names_absence_before_direction`,
      `::test_missing_feature_is_imputed_with_training_median`,
      `::test_load_refuses_model_that_failed_the_gate`, `::test_pickle_model_unloadable_means_no_model`,
      `::test_pickle_model_predicts_and_ranks_by_permutation`, `::test_shipped_params_match_feature_contract`;
      `tests/test_weak_dataset.py::test_feature_names_are_stable_and_labelled`,
      `::test_judgment_features_come_from_assessment_or_are_none`,
      `::test_embed_features_are_none_without_neighbourhood_and_derive_gain_and_ratio`,
      `::test_empty_row_measures_nothing_rather_than_zero`, `::test_llm_votes_missing_arm_is_none_not_zero`.
- [x] `reports/weak-model-zoo.md` содержит правило парсимонии и пороги годности словами,
      таблицу кандидатов с «← модель этапа 1» и p против опорной, оговорки про лёгкий контроль
      и про `probe`; вердикт: `lr_l1 / N+J`, порог выполнен, опорную значимо не бьёт никто.
- [x] `reports/weak-model.md` содержит метрики с разбросом по повторам, константу «всё —
      сигнал», правило годности с вердиктом «выполнено», таблицу важности (коэффициент,
      ΔAUC при перестановке, прочтение), пять строк с разложением «за / против», таблицу
      надёжности и дерево глубины 3.
- [x] `src/pelican/weak/model.json` установлен только при `gate.passed`; `git log` файла
      ссылается на отчёт обучения.
- [x] Бэктест (UC-25) показал, что модель вперёд не смотрит, — записано в техдоке §7 и §9
      словами «в выдаче не считается»; её роль — `predict_dataset.py`.
- [x] Техдока §7 (веса четырёх сильнейших признаков, F1 0.99, AUC 0.995) и §9 (строка
      модели этапа 1: `lr_l1 / N+J`, 29 признаков, Platt) соответствуют `model.json`.
- [ ] После перемера признаков (`OQ-11`; `J` = 12, `N+J` = 32): зоопарк и обучение повторены тем же правилом,
      модель переустановлена или вердикт «не ставится» записан.
- [x] Строки трассировки в `07-traceability.md`: FEAT-33, FEAT-32; BR-01, 02, 10, 11, 49, 52;
      NFR-39, 44.
