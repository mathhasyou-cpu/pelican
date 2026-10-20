"""Разовая уборка: повторные версии одного препринта arXiv в `works` (PEL-J-02).

    python scripts/dedupe_arxiv_versions.py            # что будет удалено, без удаления
    python scripts/dedupe_arxiv_versions.py --apply    # удалить, сохранив строки в TSV

Суточный сбор до `Store._new_arxiv_versions_only` писал v1 и v2 одной работы двумя
строками с одной датой. Выживает младшая версия (она пришла первой, к ней чаще уже есть
разметка приёма); у удаляемых строк уходят и их `tech_mentions`. Векторы удалённых id в
шардах остаются: поиск может вернуть такой id, но работа по нему в базе не найдётся и в
свидетельства не попадёт (`ask._papers`).

⚠️ Перед удалением строки целиком пишутся в `data/arxiv-version-dupes-<дата>.tsv`:
вернуть их можно `COPY works (...) FROM`.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg  # noqa: E402

from pelican.config import DATA_DIR, settings  # noqa: E402

DUPES = """
WITH v AS (
    SELECT id, url, regexp_replace(url, 'v[0-9]+$', '') AS base,
           COALESCE(substring(url FROM 'v([0-9]+)$')::int, 0) AS ver
    FROM works WHERE source = 'arxiv'
), ranked AS (
    SELECT id, row_number() OVER (PARTITION BY base ORDER BY ver, id) AS rn FROM v
)
SELECT id FROM ranked WHERE rn > 1
"""
COLUMNS = ("id", "source", "term_raw", "observed_at", "url", "field", "junk")


def main() -> int:
    ap = argparse.ArgumentParser(description="удалить повторные версии препринтов arXiv")
    ap.add_argument("--apply", action="store_true", help="удалить (по умолчанию — только показать)")
    args = ap.parse_args()
    with psycopg.connect(settings.database_url) as pg:
        ids = [r[0] for r in pg.execute(DUPES).fetchall()]
        print(f"повторных версий: {len(ids)}")
        if not ids:
            return 0
        rows = pg.execute(
            f"SELECT {', '.join(COLUMNS)} FROM works WHERE id = ANY(%s) ORDER BY url", [ids]
        ).fetchall()
        for r in rows[:5]:
            print("  ", r[4], str(r[3])[:10])
        if not args.apply:
            print("ничего не удалено; удалить — с --apply")
            return 0
        backup = DATA_DIR / f"arxiv-version-dupes-{date.today().isoformat()}.tsv"
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("w", encoding="utf-8", newline="") as fh:
            out = csv.writer(fh, delimiter="\t")
            out.writerow(COLUMNS)
            out.writerows(rows)
        with pg.transaction():
            tags = pg.execute("DELETE FROM tech_mentions WHERE signal_id = ANY(%s)", [ids]).rowcount
            gone = pg.execute("DELETE FROM works WHERE id = ANY(%s)", [ids]).rowcount
        print(f"удалено работ {gone}, разметок {tags}; копия строк — {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
