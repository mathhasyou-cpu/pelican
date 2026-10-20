"""Досягаемость целей ретро-теста для направлений из всплеска (`weak.burst`, docs/todo.md §112).

Направления, раскрытые моделью, на срезах называют технологии из будущего. Этот замер
проверяет шаг, который заменяет модель корпусом: попадает ли цель в список всплесков
области на дату среза и на каком месте. Модель не зовётся, только эмбеддинги.

    python scripts/measure_retro_reach.py              # строки домена ИИ
    python scripts/measure_retro_reach.py --domain биотех

Выход — `reports/retro/reach.md`: на строку — место цели в области (из `AREA_POOL`), попала
ли она в `FACETS` направлений запроса, и сколько раз её фраза есть в свежем окне (потолок
корпуса: ниже `MIN_RECENT` всплеска не бывает).

⚠️ Окно свежести одного только поиска (работы последних 12 или 24 месяцев, направления —
от модели) проверено на первой строке и целей не дало: трансформеры 0 из 19 работ окна.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
from retro_hits import OUT, load  # noqa: E402

#: Английская область запросов ретро-теста — как `area_en` у `ask.expand`, но записана рукой,
#: чтобы замер поиска не зависел от модели.
AREA = {
    "технологии в ИИ": "artificial intelligence and machine learning",
    "компьютерное зрение": "computer vision",
    "биотехнологии": "biotechnology",
    "перспективная энергетика": "energy technology",
    "квантовые технологии": "quantum technology",
    "перспективные вычисления": "computing hardware",
    "нейротехнологии": "neurotechnology",
}


def _hit(phrase: str, aliases: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", phrase) for a in aliases)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", default="ИИ")
    args = ap.parse_args()

    from pelican.config import settings
    from pelican.store import Store
    from pelican.weak import burst
    from pelican.weak.ground import embed_texts

    hits = [h for h in load() if h.domain == args.domain]
    lines = [
        "# Досягаемость целей ретро-теста: направления из всплеска",
        "",
        "Сгенерировано `scripts/measure_retro_reach.py`. Методика — в докстринге.",
        "",
        f"| технология | срез | место в области (из {burst.AREA_POOL}) | в {burst.FACETS} направлениях | фраза-цель: свежее / прежнее |",
        "|---|---|---|---|---|",
    ]
    found = in_facets = 0
    with Store(settings.storage_target) as store:
        cache: dict = {}
        for h in hits:
            key = (h.cut, h.query)
            if key not in cache:
                counted = burst.counts(store, h.cut)
                recent, before = counted[0], counted[1]
                bursts = burst.score(*counted)[: burst.GLOBAL_POOL]
                vectors = embed_texts([b.phrase for b in bursts])
                area = embed_texts([h.query, AREA[h.query]])
                keep = np.argsort(-(vectors @ area.T).max(axis=1))[: burst.AREA_POOL]
                pool = sorted((bursts[i] for i in keep), key=lambda b: -b.z)
                cache[key] = (recent, before, pool, burst._distinct(pool, burst.FACETS))
                print(f"\n{h.cut} «{h.query}»: " + ", ".join(b.phrase for b in pool[:30]), flush=True)
            recent, before, pool, chosen = cache[key]
            rank = next((i for i, b in enumerate(pool, 1) if _hit(b.phrase, h.aliases)), None)
            took = any(_hit(b.phrase, h.aliases) for b in chosen)
            best = max((g for g in recent if _hit(g, h.aliases)), key=lambda g: recent[g], default=None)
            trace = f"{best}: {recent[best]} / {before[best]}" if best else f"< {burst.MIN_RECENT} в свежем окне"
            found += rank is not None
            in_facets += took
            lines.append(f"| {h.tech} | {h.cut} | {rank or '—'} | {'да' if took else '—'} | {trace} |")
            print(f"  {h.tech}: место {rank}, в направлениях {took}; {trace}", flush=True)
    lines += ["", f"- **в области**: {found} из {len(hits)}; **в направлениях запроса**: {in_facets} из {len(hits)}"]
    out = OUT / f"reach-{args.domain}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
