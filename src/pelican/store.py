"""Хранилище корпуса и разметки — Postgres.

Корпус (`works`) — научные работы arxiv и openalex, 15 млн строк; поиск по тексту идёт
полнотекстовым индексом (`works.tsv`, GIN), а не проходом по столбцу. Векторы работ лежат
файлами-шардами рядом (`weak.shards`), в базу не кладутся: 14 млн × 768 — это отдельное
хранилище, а поиск по ним — проход по памяти, которому индекс не нужен.

Код ветки пишет SQL с плейсхолдерами `?` (как в DuckDB, на которой он вырос); обёртка
соединения переводит их в `%s` psycopg. Литеральный `%` в таком запросе удваивается там
же, поэтому `ILIKE '%x%'` рядом с параметром не ломает подстановку.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg

SCHEMA = Path(__file__).with_name("schema.sql")
SCIENCE_SOURCES = ("arxiv", "openalex")


def work_id(source: str, external_id: str, metric: str, observed_at: datetime) -> int:
    """Id работы из её естественного ключа — тот же, что у исходного хранилища.

    Content-addressed surrogate key: младшие 8 байт md5 от
    `source ␟ external_id ␟ metric ␟ epoch_us(observed_at)`, сдвиг вправо на бит, чтобы
    влезть в знаковый BIGINT. Одна и та же работа из двух прогонов получает один id, и
    повторный сбор дублей не даёт.
    """
    if observed_at.tzinfo is not None:
        observed_at = observed_at.astimezone(UTC).replace(tzinfo=None)
    delta = observed_at - datetime(1970, 1, 1)
    epoch_us = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    key = "\x1f".join((source, external_id, metric, str(epoch_us)))
    digest = hashlib.md5(key.encode("utf-8")).digest()
    # `md5_number_lower` DuckDB — это байты 8..16 дайджеста как little-endian UBIGINT
    # (сверено на строках исходного хранилища, тест `test_work_id_matches_source`).
    (lower,) = struct.unpack("<Q", digest[8:])
    return lower >> 1


class _Conn:
    """Соединение psycopg с интерфейсом `execute(sql, params)` → курсор."""

    def __init__(self, url: str) -> None:
        self.raw = psycopg.connect(url, autocommit=True)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> psycopg.Cursor:
        if params:
            sql = sql.replace("%", "%%").replace("?", "%s")
            return self.raw.execute(sql, list(params))
        return self.raw.execute(sql)

    def close(self) -> None:
        self.raw.close()


class Store:
    def __init__(self, url: str) -> None:
        self.conn = _Conn(url)

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self, indexes: bool = True) -> None:
        tables, _, idx = SCHEMA.read_text(encoding="utf-8").partition("-- @indexes")
        self.conn.execute(tables)
        if indexes:
            self.conn.execute(idx)

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        return self.conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ корпус

    def insert_works(self, rows: Iterable[tuple]) -> int:
        """(id, source, term_raw, observed_at, url, field, junk) → `works`, без дублей.

        `COPY` во временную таблицу и одна вставка с `ON CONFLICT DO NOTHING`: построчный
        `INSERT` на суточном окне openalex (тысячи работ) стоил бы секунды на каждую
        сотню строк. Возвращает число реально вставленных.
        """
        rows = list(rows)
        if not rows:
            return 0
        with self.conn.raw.transaction():
            self.conn.raw.execute(
                "CREATE TEMP TABLE _w (id BIGINT, source TEXT, term_raw TEXT, "
                "observed_at TIMESTAMP, url TEXT, field TEXT, junk BOOLEAN) ON COMMIT DROP"
            )
            with self.conn.raw.cursor().copy(
                "COPY _w (id, source, term_raw, observed_at, url, field, junk) FROM STDIN"
            ) as copy:
                for row in rows:
                    copy.write_row(row)
            cur = self.conn.raw.execute(
                "INSERT INTO works (id, source, term_raw, observed_at, url, field, junk) "
                "SELECT DISTINCT ON (id) * FROM _w ON CONFLICT (id) DO NOTHING"
            )
            return cur.rowcount

    def insert_signals(self, signals: Iterable[Any]) -> int:
        """Работы от сборщика (`sources.base.Signal`) → `works`. Не наука — пропускается.

        Id — из естественного ключа (`work_id`), «строка без содержания» и срез openalex
        считаются здесь один раз, а не на каждом запросе.
        """
        from pelican.weak.corpus import is_junk

        rows = []
        for s in signals:
            if s.source not in SCIENCE_SOURCES:
                continue
            payload = s.payload or {}
            rows.append((
                work_id(s.source, s.external_id, s.metric, s.observed_at),
                s.source,
                s.term_raw,
                _naive(s.observed_at),
                s.url,
                payload.get("field"),
                is_junk(s.source, s.term_raw, payload.get("type")),
            ))
        return self.insert_works(rows)

    def untagged_science(
        self,
        model: str,
        since: datetime,
        limit: int | None = None,
        ids: Sequence[int] | None = None,
    ) -> list[tuple[int, str]]:
        """(id, текст) — из чего модель ещё не доставала приём.

        Очередь — `LEFT JOIN tech_mentions` с проверкой на NULL: прерванный прогон
        продолжается с того же места, повторный не платит за сделанное. Строки без
        содержания (`works.junk`) в очередь не идут. `ids` сужает очередь до найденного
        поиском (`ask`); горизонт `since` действует и тогда.
        """
        only = ""
        if ids is not None:
            listed = ",".join(str(int(i)) for i in ids)
            if not listed:
                return []
            only = f"AND w.id IN ({listed})"
        tail = f"LIMIT {int(limit)}" if limit is not None else ""
        return self.conn.execute(
            f"""
            SELECT w.id, w.term_raw
            FROM works w
            LEFT JOIN tech_mentions t ON t.signal_id = w.id AND t.model = ?
            WHERE t.signal_id IS NULL AND NOT w.junk AND w.observed_at >= ?
              {only}
            ORDER BY w.observed_at DESC
            {tail}
            """,
            [model, _naive(since)],
        ).fetchall()

    def save_tech(self, model: str, rows: Sequence[tuple[int, str, str]]) -> None:
        """rows: (signal_id, tech, role). Пустой приём пишется тоже — иначе очередь
        никогда не опустеет."""
        if not rows:
            return
        with self.conn.raw.cursor() as cur:
            cur.executemany(
                "INSERT INTO tech_mentions (signal_id, model, tech, role, created_at) "
                "VALUES (%s, %s, %s, %s, now() AT TIME ZONE 'UTC') "
                "ON CONFLICT (signal_id, model) DO UPDATE SET "
                "tech = excluded.tech, role = excluded.role",
                [(sid, model, tech, role) for sid, tech, role in rows],
            )

    def tech_counts(self, model: str) -> list[tuple[str, int]]:
        return self.query(
            "SELECT role, count(*) FROM tech_mentions WHERE model = ? GROUP BY 1 ORDER BY 2 DESC",
            [model],
        )

    def distinct_techs(self, model: str) -> list[tuple[str, int, int, int]]:
        """(приём, предлагают, применяют, обозревают).

        Три счётчика врозь, а не одна сумма: `proposes` — новизна, `uses` — диффузия,
        `reviews` — признак зрелости (Yoon 2012). Сложенные, они перестают различать
        зарождающийся приём и доживающий.
        """
        return self.query(
            """
            SELECT tech,
                   count(*) FILTER (WHERE role = 'proposes'),
                   count(*) FILTER (WHERE role = 'uses'),
                   count(*) FILTER (WHERE role = 'reviews')
            FROM tech_mentions WHERE model = ? AND tech <> ''
            GROUP BY tech ORDER BY tech
            """,
            [model],
        )

    def tech_daily_counts(self, model: str) -> list[tuple[str, str, date, int]]:
        """(приём, источник, день, работ). 1 января выброшено: у openalex это заглушка
        вместо даты, и детектор всплеска находил бы её первой на каждом годовом стыке."""
        return self.query(
            f"""
            SELECT t.tech, w.source, w.observed_at::date AS day, count(*)
            FROM tech_mentions t JOIN works w ON w.id = t.signal_id
            WHERE t.model = ? AND t.tech <> '' AND NOT {_JAN1}
            GROUP BY 1, 2, 3
            """,
            [model],
        )

    def tech_spans(self, model: str) -> dict[str, tuple[date, date]]:
        """{приём: (первая работа, последняя работа)}, без заглушки 1 января."""
        rows = self.query(
            f"""
            SELECT t.tech, min(w.observed_at::date), max(w.observed_at::date)
            FROM tech_mentions t JOIN works w ON w.id = t.signal_id
            WHERE t.model = ? AND t.tech <> '' AND NOT {_JAN1}
            GROUP BY 1
            """,
            [model],
        )
        return {r[0]: (r[1], r[2]) for r in rows}

    def replace_tech_groups(self, model: str, threshold: float, groups: Sequence[Any]) -> None:
        """Заменить группы приёмов эмбеддера `model` целиком, одной транзакцией."""
        with self.conn.raw.transaction(), self.conn.raw.cursor() as cur:
            cur.execute(
                "DELETE FROM tech_group_members WHERE group_id IN "
                "(SELECT id FROM tech_groups WHERE model = %s)",
                [model],
            )
            cur.execute("DELETE FROM tech_groups WHERE model = %s", [model])
            if not groups:
                return
            base = cur.execute("SELECT coalesce(max(id), 0) + 1 FROM tech_groups").fetchone()[0]
            cur.executemany(
                "INSERT INTO tech_groups (id, label, model, threshold, proposes, uses, reviews,"
                " first_seen, last_seen, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now() AT TIME ZONE 'UTC')",
                [
                    (base + i, g.label, model, threshold, g.proposes, g.uses, g.reviews,
                     g.first_seen, g.last_seen)
                    for i, g in enumerate(groups)
                ],
            )
            cur.executemany(
                "INSERT INTO tech_group_members (group_id, tech, border) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [
                    (base + i, tech, border)
                    for i, g in enumerate(groups)
                    for tech, border in [*((t, False) for t in g.members),
                                         *((t, True) for t in g.border)]
                ],
            )


_JAN1 = "(extract(month FROM w.observed_at) = 1 AND extract(day FROM w.observed_at) = 1)"


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
