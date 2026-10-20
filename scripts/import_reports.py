"""Готовые отчёты с диска (`<имя>.json` + `<имя>.html`) → таблица `reports`.

    python scripts/import_reports.py ../reports/ask

Отчёты, посчитанные до того, как ответы стали храниться в Postgres, остаются историей
запросов: по ним интерфейс показывает прошлые выдачи, а карточка — «держится N из K
прошлых прогонов». Повторный импорт того же имени перезаписывает строку.

Имя в таблице — по BR-45: время старта `YYYYmmdd-HHMMSS` или служебное `tz-<запрос>-<время>`.
Файл с иным именем («cyber2») импортируется под временем своего `started`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pelican import jobs  # noqa: E402
from pelican.config import settings  # noqa: E402

#: Имена по BR-45 — то же правило, что у `/api/reports/{name}`.
NAME = jobs.REPORT_NAME


def report_name(stem: str, started: datetime) -> str:
    """Имя файла, если оно по правилу; иначе — время старта прогона."""
    return stem if NAME.fullmatch(stem) else f"{started:%Y%m%d-%H%M%S}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--url", default=settings.database_url)
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
            jobs.save_report(conn, report_name(path.stem, started), data["query"], started, text,
                             page.read_text(encoding="utf-8"))
            n += 1
    print(f"импортировано отчётов: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
