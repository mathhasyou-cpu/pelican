"""Готовые отчёты с диска (`<имя>.json` + `<имя>.html`) → таблица `reports`.

    python scripts/import_reports.py ../reports/ask

Отчёты, посчитанные до того, как ответы стали храниться в Postgres, остаются историей
запросов: по ним интерфейс показывает прошлые выдачи, а карточка — «держится N из K
прошлых прогонов». Повторный импорт того же имени перезаписывает строку.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pelican import jobs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--url", default=os.environ.get(
        "DATABASE_URL", "postgresql://pelican:pelican@127.0.0.1:5436/pelican"))
    args = ap.parse_args()
    n = 0
    with jobs.connect(args.url) as conn:
        jobs.apply_schema(conn)
        for path in sorted(args.folder.glob("*.json")):
            page = path.with_suffix(".html")
            if not page.exists():
                continue
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
            started = datetime.fromisoformat(data["started"]).astimezone()
            jobs.save_report(conn, path.stem, data["query"], started, text,
                             page.read_text(encoding="utf-8"))
            n += 1
    print(f"импортировано отчётов: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
