"""Флаги на живых примерах ТЗ — третье правило включения (docs/final-measure.md, шаг 2).

Прогоняет запросы ТЗ (`measure_ask.TZ_QUERIES`) на сегодня в каждом плече
`retro_hits.ARMS` и считает в ТОП две вещи, против которых флаги и заводились:
карточки-категории и Big Tech-направления с продажами.

    python scripts/measure_arms_tz.py                     # все плечи
    python scripts/measure_arms_tz.py --report-only       # таблица по готовым JSON

Выход — `reports/arms-tz/<плечо>/<запрос>.json` и `reports/arms-tz/arms-tz.md`.

⚠️ Не `measure_ask.py --tz`: тот пишет каждый прогон в таблицу `reports` стенда, и
экспериментальные плечи попали бы в историю интерфейса и в «держится N из K».
⚠️ Плечи идут подряд на каждом запросе с включённым кэшем живого поиска: все плечи видят
одни и те же новости (см. `retro_hits.ARMS`).
⚠️ Список категорий `CATEGORIES` записан ДО прогона плеч и по его итогам не правится.
Категория — ярлык, который целиком (после нормализации) есть зонтичный термин без приёма
за ним. Список от руки — догадка (docs/todo.md §111).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from measure_ask import TZ_QUERIES  # noqa: E402
from retro_hits import ARMS, _slug, set_arm  # noqa: E402

OUT = REPO / "reports" / "arms-tz"

#: Зонтичные термины: ярлык, равный одному из них, — категория, а не слабый сигнал.
CATEGORIES = frozenset(
    {
        # ИИ
        "agentic ai", "ai agents", "autonomous agents", "generative ai", "genai",
        "large language models", "llm", "llms", "machine learning", "deep learning",
        "artificial intelligence", "rag", "retrieval augmented generation",
        "retrieval-augmented generation", "computer vision", "natural language processing",
        "nlp", "multimodal ai", "edge ai", "explainable ai", "ai safety", "foundation models",
        # финтех
        "defi", "decentralized finance", "blockchain", "cryptocurrency", "web3", "open banking",
        "embedded finance", "bnpl", "buy now pay later", "cbdc", "digital currency",
        "tokenization", "neobank", "neobanks", "regtech", "insurtech", "digital payments",
        # кибербезопасность
        "zero trust", "zero trust architecture", "cybersecurity", "threat intelligence",
        "siem", "xdr", "edr", "soar", "sase", "identity and access management", "iam",
        "post-quantum cryptography", "quantum cryptography", "deepfake detection",
        # космос и материалы
        "satellite internet", "small satellites", "cubesats", "space debris", "reusable rockets",
        "advanced materials", "nanomaterials", "graphene", "metamaterials", "2d materials",
        "smart materials", "composite materials",
        # общее
        "quantum computing", "internet of things", "iot", "cloud computing", "edge computing",
        "digital twin", "digital twins", "metaverse", "robotics", "5g", "6g",
    }
)


def norm(label: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", label.lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", text)).strip()


def is_category(sig: dict) -> bool:
    return norm(sig.get("label", "")) in CATEGORIES


def is_bigtech_selling(sig: dict) -> bool:
    """Правило слоя «уже тренд» из `weak.layers` без лидеров рынка: крупный игрок и продажи."""
    from pelican.weak.layers import big_players

    j = sig.get("judgments") or {}
    selling = bool(j.get("commercial_deployment")) or bool(j.get("funding_mentioned"))
    return bool(big_players(sig.get("companies") or [])) and selling


def run_one(store, query: str, path: Path) -> dict:
    from pelican.weak import ask, page

    result = ask.run(store, query, on_progress=lambda m: print(f"    {m}", flush=True))
    payload = json.loads(page.to_json(result))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def table(runs: dict[tuple[str, str], dict], arms: list[str]) -> str:
    lines = [
        "# Флаги на живых примерах ТЗ",
        "",
        "Сгенерировано `scripts/measure_arms_tz.py`. Методика — в докстринге скрипта.",
        "",
        "| плечо | запрос | ТОП | наблюдать | категорий в ТОП | Big Tech с продажами в ТОП | секунд |",
        "|---|---|---|---|---|---|---|",
    ]
    for arm in arms:
        for q in TZ_QUERIES:
            r = runs.get((arm, q))
            if r is None:
                continue
            top = r.get("signals") or []
            cats = [s["label"] for s in top if is_category(s)]
            big = [s["label"] for s in top if is_bigtech_selling(s)]
            lines.append(
                f"| {arm} | {q} | {len(top)} | {len(r.get('watch') or [])} | "
                f"{len(cats)}{': ' + ', '.join(cats) if cats else ''} | "
                f"{len(big)}{': ' + ', '.join(big) if big else ''} | {round(r.get('seconds') or 0)} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arms", default=",".join(ARMS), help="плечи через запятую")
    ap.add_argument("--report-only", action="store_true", help="только таблица по готовым JSON")
    ap.add_argument(
        "--reps",
        type=int,
        default=0,
        help="нулевой контроль: N повторов каждого плеча, в папки <плечо>-r1…rN (кэш тот же)",
    )
    args = ap.parse_args()
    arms = [a.strip() for a in args.arms.split(",")]
    if args.reps:
        arms = [f"{a}-r{i}" for i in range(1, args.reps + 1) for a in arms]

    from pelican import locks
    from pelican.config import settings
    from pelican.store import Store
    from pelican.weak import cache

    cache.ENABLED = True
    runs: dict[tuple[str, str], dict] = {}
    try:
        with locks.hold("model"), Store(settings.storage_target) as store:
            for q in TZ_QUERIES:
                for arm in arms:
                    out = OUT / arm
                    out.mkdir(parents=True, exist_ok=True)
                    path = out / f"{_slug(q)}.json"
                    if path.exists():
                        runs[(arm, q)] = json.loads(path.read_text(encoding="utf-8"))
                    elif not args.report_only:
                        print(f"«{q}» · плечо {arm}", flush=True)
                        set_arm(re.sub(r"-r\d+$", "", arm))
                        runs[(arm, q)] = run_one(store, q, path)
    except locks.Busy as exc:
        # Модель одна на стенд (NFR-26): «занято» — код 75, как у остальных скриптов замеров.
        print(f"занято: {exc}")
        return 75
    md = OUT / "arms-tz.md"
    md.write_text(table(runs, arms), encoding="utf-8")
    print(f"→ {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
