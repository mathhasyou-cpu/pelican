-- Схема Postgres. Применяется на старте приложения и перед переливом корпуса; всё
-- идемпотентно (`IF NOT EXISTS`).
--
-- Индексы корпуса вынесены за маркер `@indexes`: на 15 млн строк GIN, построенный один
-- раз после заливки, дешевле, чем индекс, обновляемый на каждой вставке.

-- Научный корпус: работа = факт публикации (arxiv, openalex).
CREATE TABLE IF NOT EXISTS works (
    id           BIGINT    PRIMARY KEY,   -- тот же id, что у строки в исходном хранилище
    source       TEXT      NOT NULL,      -- 'arxiv' | 'openalex'
    term_raw     TEXT      NOT NULL,      -- заголовок + аннотация
    observed_at  TIMESTAMP NOT NULL,      -- дата публикации
    url          TEXT,
    field        TEXT,                    -- срез openalex (payload.field); у arxiv NULL
    -- «Строка без содержания» (weak.corpus.is_junk): готовая колонка, а не
    -- вычисление по JSON на каждом запросе.
    junk         BOOLEAN   NOT NULL DEFAULT FALSE,
    -- Полнотекст для следа корпуса (weak.core). Конфигурация `simple`: без стемминга и
    -- стоп-слов — имя технологии сравнивается как написано, а короткие токены вроде «ai»
    -- остаются в индексе (у триграмм `pg_trgm` паттерн короче трёх символов индекса не
    -- использует и уходит в полный проход). STORED, потому что фразовый запрос
    -- перепроверяет строку по позициям, а пересчитывать tsvector из текста на каждой
    -- найденной строке — та же цена, от которой индекс избавляет.
    tsv          TSVECTOR  GENERATED ALWAYS AS (to_tsvector('simple', term_raw)) STORED
);

-- Приём, названный моделью в работе. `role` — две разные оси у Yoon (2012): `proposes`
-- даёт новизну, `uses` — диффузию, `reviews` — признак зрелости (обзоры пишут, когда
-- работ много). Ключ (signal_id, model): смена модели даёт очередь заново; пустой приём
-- пишется тоже, иначе очередь не опустеет.
CREATE TABLE IF NOT EXISTS tech_mentions (
    signal_id  BIGINT    NOT NULL,
    model      TEXT      NOT NULL,
    tech       TEXT      NOT NULL DEFAULT '',
    role       TEXT      NOT NULL DEFAULT 'none',
    created_at TIMESTAMP NOT NULL,
    PRIMARY KEY (signal_id, model)
);

-- Группы вариантов написания одного приёма: средняя связь (UPGMA), ярлык — медоид, то
-- есть самая типичная реальная формулировка, а не придуманная моделью.
CREATE TABLE IF NOT EXISTS tech_groups (
    id         BIGINT    PRIMARY KEY,
    label      TEXT      NOT NULL,
    model      TEXT      NOT NULL,   -- эмбеддер: смена модели = пересборка
    threshold  DOUBLE PRECISION NOT NULL,
    proposes   INTEGER   NOT NULL DEFAULT 0,
    uses       INTEGER   NOT NULL DEFAULT 0,
    reviews    INTEGER   NOT NULL DEFAULT 0,
    first_seen DATE,
    last_seen  DATE,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS tech_group_members (
    group_id BIGINT  NOT NULL,
    tech     TEXT    NOT NULL,
    border   BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (group_id, tech)
);

-- Очередь запросов. Стенд работает на одной локальной модели, поэтому запросы идут по
-- одному: новый встаёт в очередь, а не отвергается, и стартует сам, когда модель
-- освободится. Очередь в базе, а не в памяти процесса: перезапуск контейнера её не теряет.
CREATE TABLE IF NOT EXISTS jobs (
    id        BIGSERIAL   PRIMARY KEY,
    query     TEXT        NOT NULL,
    status    TEXT        NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
    created   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started   TIMESTAMPTZ,
    finished  TIMESTAMPTZ,
    progress  JSONB       NOT NULL DEFAULT '[]',      -- строки стадий: «секунды · что идёт»
    error     TEXT        NOT NULL DEFAULT '',
    report    TEXT                                    -- reports.name по готовности
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, id);
-- IP пользователя: лимит активных заданий на адрес (`jobs.enqueue`). '' — без лимита.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS client TEXT NOT NULL DEFAULT '';

-- Готовые ответы: результат целиком (JSON) и страница-отчёт (HTML).
CREATE TABLE IF NOT EXISTS reports (
    name     TEXT        PRIMARY KEY,                 -- время старта, YYYYmmdd-HHMMSS
    query    TEXT        NOT NULL,
    started  TIMESTAMPTZ NOT NULL,
    result   JSONB       NOT NULL,
    html     TEXT        NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_started ON reports (started DESC);

-- @indexes
CREATE INDEX IF NOT EXISTS works_tsv      ON works USING GIN (tsv);
CREATE INDEX IF NOT EXISTS works_observed ON works (observed_at);
CREATE INDEX IF NOT EXISTS tech_mentions_tech ON tech_mentions (tech);
