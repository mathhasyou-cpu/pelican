"""Список институтов OpenAlex с `type:company` → `data/openalex-companies.json`.

Нужен признаку индустриальной аффилиации (`weak.core.industry_share`): доля работ ядра, у
которых среди авторов есть компания, — university–industry co-publication (Wong & Singh
2013, Scientometrics) как индикатор коммерциализации. В корпусе у работы сохранены только
ИМЕНА институтов (`sources.openalex.affiliations_of`), тип — здесь.

    python scripts/fetch_openalex_companies.py

~36 тыс. имён, 180 страниц по 200, курсорная пагинация; повторный запуск перезаписывает файл.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pelican.config import DATA_DIR, settings  # noqa: E402

URL = "https://api.openalex.org/institutions"
OUT = DATA_DIR / "openalex-companies.json"
PER_PAGE = 200


def main() -> None:
    names: set[str] = set()
    cursor = "*"
    pages = 0
    with httpx.Client(timeout=60, headers={"User-Agent": settings.user_agent}) as http:
        while cursor:
            params = {
                "filter": "type:company",
                "select": "display_name",
                "per-page": PER_PAGE,
                "cursor": cursor,
            }
            if settings.openalex_api_key:
                params["api_key"] = settings.openalex_api_key
            for attempt in range(5):
                r = http.get(URL, params=params)
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            payload = r.json()
            names.update(n for i in payload["results"] if (n := i.get("display_name")))
            cursor = (payload.get("meta") or {}).get("next_cursor")
            pages += 1
            if pages % 20 == 0:
                print(f"{pages} страниц, {len(names)} имён", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(sorted(names), ensure_ascii=False), encoding="utf-8")
    print(f"{OUT}: {len(names)} компаний")


if __name__ == "__main__":
    main()
