"""Ворота переезда корпуса в Postgres: след имени — DuckDB `ILIKE` против PG полнотекста.

    python scripts/measure_pg_footprint.py --from quack:localhost reports/ask/*.json

След (`weak.core`) — три числа на имя: работ всего, год первого появления, работ за
последний год. Сегодня он считается подстрокой (`ILIKE '%…%'`) полным проходом по 15 млн
работ, и это минуты на каждый запрос. В Postgres та же мера — `tsvector` + GIN, но
семантика другая (токен, а не подстрока), поэтому числа сверяются, а не предполагаются.

Мера совпадения повторяет `weak.core._match`:
- ядро (точная фраза) — фраза токенов, последний с префиксом (`federated <-> learn:*`):
  подстрока «federated learning» ловит и «learnings»;
- короткое ядро (< 6 символов) — `\\bxs?\\b` в DuckDB, `x | xs` в PG;
- ярлык (набор слов) — до четырёх самых длинных значимых слов, каждое префиксом, через И.

Решение (⚠️ пороги — догадка, не замер): ранговая корреляция работ ≥ 0.9, год появления
совпадает у ≥ 90% имён, время PG не хуже DuckDB — корпус переезжает в Postgres.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pelican import quack  # noqa: E402

SHORT_CORE = 6
LOOSE_WORDS = 4
SCAN_CHUNK = 24
_STOP = frozenset(
    "a an the of for and or in on with via to by from using based as at into over".split()
)


def significant(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#-]{3,}", name.lower()) if w not in _STOP]


def _quote(text: str) -> str:
    return text.replace("'", "''").replace("%", "\\%").replace("_", "\\_")


def duck_match(name: str, loose: bool) -> str:
    """Дословно `weak.core._match`."""
    low = _quote(name.lower())
    words = significant(low)
    if loose and len(words) > 1:
        chosen = sorted(sorted(set(words), key=len, reverse=True)[:LOOSE_WORDS])
        return " AND ".join(f"term_raw ILIKE '%{w}%'" for w in chosen)
    if len(low) < SHORT_CORE:
        return f"regexp_matches(term_raw, '(?i)\\b{low}s?\\b')"
    return f"term_raw ILIKE '%{low}%'"


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def pg_query(name: str, loose: bool) -> str | None:
    """tsquery-строка для `to_tsquery('simple', …)`; None — мерить нечем."""
    low = name.lower()
    words = significant(low)
    if loose and len(words) > 1:
        chosen = sorted(sorted(set(words), key=len, reverse=True)[:LOOSE_WORDS])
        parts = [t for w in chosen for t in _tokens(w)]
        return " & ".join(f"{t}:*" for t in parts) or None
    toks = _tokens(low)
    if not toks:
        return None
    if len(low) < SHORT_CORE and len(toks) == 1:
        return f"{toks[0]} | {toks[0]}s"
    return " <-> ".join(toks[:-1] + [f"{toks[-1]}:*"])


def names_from(paths: list[Path]) -> tuple[list[str], list[str]]:
    cores, labels = set(), set()
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for s in data.get("signals", []):
            if s.get("core"):
                cores.add(s["core"].strip().lower())
            if s.get("label"):
                labels.add(s["label"].strip().lower())
    return sorted(cores), sorted(labels)


def duck_scan(
    conn, dsn: str, token: str, specs: list[tuple[str, str]], bound: str = ""
) -> list[tuple]:
    out = []
    for start in range(0, len(specs), SCAN_CHUNK):
        chunk = specs[start : start + SCAN_CHUNK]
        flags = ", ".join(f"({m}) AS m{i}" for i, (_n, m) in enumerate(chunk))
        aggs = ", ".join(
            f"count(*) FILTER (WHERE m{i}), "
            f"min(year(observed_at)) FILTER (WHERE m{i} AND NOT "
            f"(month(observed_at) = 1 AND day(observed_at) = 1)), "
            f"count(*) FILTER (WHERE m{i} AND observed_at >= now() - INTERVAL 365 DAY)"
            for i in range(len(chunk))
        )
        sql = (
            f"SELECT {aggs} FROM (SELECT observed_at, {flags} FROM signals "
            f"WHERE source IN ('arxiv', 'openalex'){bound})"
        )
        row = quack.query(conn, dsn, token, sql)[0]
        out += [tuple(row[3 * i : 3 * i + 3]) for i in range(len(chunk))]
    return out


PG_SQL = (
    "SELECT count(*), "
    "(min(extract(year FROM observed_at)) FILTER (WHERE NOT "
    "(extract(month FROM observed_at) = 1 AND extract(day FROM observed_at) = 1)))::int, "
    "count(*) FILTER (WHERE observed_at >= now() - interval '365 days') "
    "FROM works WHERE tsv @@ to_tsquery('simple', %s)"
)
#: `--max-id`: обе стороны на одном срезе id — сверка чисел по недолитому корпусу. Id —
#: хэш, поэтому срез по нему — случайная доля корпуса. Время на срезе не сравнивается.


def ranks(a: np.ndarray) -> np.ndarray:
    order = a.argsort(kind="stable")
    r = np.empty(len(a))
    r[order] = np.arange(len(a))
    return r


def spearman(a: list[int], b: list[int]) -> float:
    x, y = ranks(np.asarray(a, float)), ranks(np.asarray(b, float))
    return float(np.corrcoef(x, y)[0, 1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--from", dest="dsn", default="quack:localhost")
    ap.add_argument("--token", default=os.environ.get("QUACK_TOKEN", ""))
    ap.add_argument("--url", default=os.environ.get(
        "DATABASE_URL", "postgresql://pelican:pelican@127.0.0.1:5436/pelican"))
    ap.add_argument("--limit", type=int, default=60, help="имён каждого вида")
    ap.add_argument("--out", type=Path, default=Path("reports/pg-footprint.tsv"))
    ap.add_argument("--max-id", type=int, default=None)
    args = ap.parse_args()
    bound = f" AND id <= {args.max_id}" if args.max_id is not None else ""

    cores, labels = names_from(args.reports)
    rng = np.random.default_rng(0)
    pick = lambda xs: sorted(rng.choice(xs, min(args.limit, len(xs)), replace=False).tolist())  # noqa: E731
    cores, labels = pick(cores), pick(labels)
    specs = [(n, False) for n in cores] + [(n, True) for n in labels]
    print(f"имён: {len(cores)} ядер, {len(labels)} ярлыков", flush=True)

    conn = quack.connect()
    t = time.perf_counter()
    duck = duck_scan(conn, args.dsn, args.token, [(n, duck_match(n, lz)) for n, lz in specs], bound)
    t_duck = time.perf_counter() - t
    print(f"DuckDB ILIKE: {t_duck:.0f} с", flush=True)

    pg_rows, t_each = [], []
    with psycopg.connect(args.url) as pg:
        t = time.perf_counter()
        for n, lz in specs:
            q = pg_query(n, lz)
            t1 = time.perf_counter()
            pg_rows.append(
                tuple(pg.execute(PG_SQL + bound, [q]).fetchone()) if q else (0, None, 0)
            )
            t_each.append(time.perf_counter() - t1)
        t_pg = time.perf_counter() - t
    print(f"PG FTS: {t_pg:.0f} с (медиана {np.median(t_each):.2f} с, макс {max(t_each):.1f} с)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("kind\tname\tduck_works\tpg_works\tduck_year\tpg_year\tduck_1y\tpg_1y\tpg_s\n")
        for (n, lz), d, p, s in zip(specs, duck, pg_rows, t_each, strict=True):
            f.write(f"{'label' if lz else 'core'}\t{n}\t{d[0]}\t{p[0]}\t{d[1]}\t{p[1]}"
                    f"\t{d[2]}\t{p[2]}\t{s:.2f}\n")

    for kind, sel in (("ядра", [not lz for _n, lz in specs]), ("ярлыки", [lz for _n, lz in specs])):
        d = [r for r, k in zip(duck, sel, strict=True) if k]
        p = [r for r, k in zip(pg_rows, sel, strict=True) if k]
        both = [(a, b) for a, b in zip(d, p, strict=True) if a[0] and b[0]]
        year_same = sum(1 for a, b in both if a[1] == b[1]) / max(len(both), 1)
        ratio = np.median([b[0] / a[0] for a, b in both]) if both else float("nan")
        print(
            f"{kind}: n={len(d)}, Спирмен работ {spearman([r[0] for r in d], [r[0] for r in p]):.3f}, "
            f"за год {spearman([r[2] for r in d], [r[2] for r in p]):.3f}, "
            f"год совпал {year_same:.0%} (из {len(both)} с ненулём), медиана PG/DuckDB {ratio:.2f}, "
            f"ноль только в PG: {sum(1 for a, b in zip(d, p, strict=True) if a[0] and not b[0])}, "
            f"только в DuckDB: {sum(1 for a, b in zip(d, p, strict=True) if b[0] and not a[0])}"
        )
    print(f"таблица: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
