"""Где ретро-тест теряет цель: атрибуция промаха по этапам конвейера, без вызова модели.

    python scripts/retro_diagnose.py --hits scripts/retro_lead.tsv --arm final
    python scripts/retro_diagnose.py --hits scripts/retro_lead.tsv --arm final --no-db

Приёмы:
- **атрибуция ошибки по этапам** (error analysis / funnel): на строку (технология, срез) —
  первый этап, где цель потерялась: корпус → направления → пул → модель роста → слои → ТОП;
- **подстановка оракула** (perfect-upstream ablation): цель кладётся в пул отчёта со своим
  настоящим на дату среза следом корпуса, и модель роста (`growth.json`) ставит ей место
  среди зарождающихся — так модель отделяется от полноты поиска;
- **расхождение признаков обучения и выдачи** (training–serving skew, TFX Data Validation):
  `core_last_year_share`, `core_works` в TSV бэктеста против ретро-отчётов;
- **утечка будущего через знания LLM** (lookahead bias, Sarkar & Vafa 2024): направления
  `expand`, которые называют цель раньше, чем она появилась в корпусе;
- **строгое и мягкое совпадение** (решение асессора, как в пулинге TREC): строгое —
  `retro_hits.locate` по псевдонимам из TSV; мягкое — кандидаты, у которых в ярлыке или ядре
  есть корневое слово технологии (`SOFT`). Мягкое — список НА РЕШЕНИЕ человека, а не
  засчитанное попадание: псевдонимы задним числом не правятся.

Только чтение: отчёты `reports/retro/<плечо>/*.json`, TSV бэктеста, база (след корпуса на
дату). Выход — `reports/retro/diagnose.md`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from retro_hits import HITS, OUT, Hit, load, locate, report_path  # noqa: E402

#: Корневые слова технологии для мягкого совпадения — по началу имени строки TSV.
#: Записаны до просмотра ярлыков прогона, кроме «attention»: его показал срез 2017-12
#: (ядро «attention mechanisms» на №1), поэтому мягкая колонка — только кандидаты асессору.
SOFT = {
    "Трансформеры": r"attention|transformer",
    "Диффузионные": r"diffus|score[- ]based|denois",
    "Vision Transformer": r"vision transformer|\bvit\b|image transformer|patch[- ]based transformer",
    "Генерация с поиском": r"retriev",
    "Обучение по обратной связи": r"human feedback|rlhf|preference|reward model",
    "Низкоранговая адаптация": r"low[- ]rank|\blora\b|adapter|parameter[- ]efficient",
    "Цепочка рассуждений": r"chain[- ]of[- ]thought|step[- ]by[- ]step|reasoning",
}
LISTS = ("signals", "watch", "thin", "excluded")
EMERGING = ("signals", "watch", "thin")


def soft_pattern(tech: str) -> str:
    return next((p for k, p in SOFT.items() if tech.startswith(k)), "")


def soft_hits(report: dict, pattern: str) -> list[str]:
    """Кандидаты отчёта, у которых в ярлыке/названии/ядре есть корневое слово."""
    out = []
    for where in LISTS:
        for i, s in enumerate(report.get(where) or [], 1):
            text = " | ".join([s.get("label", ""), s.get("title", ""), s.get("core") or ""]).lower()
            if pattern and re.search(pattern, text):
                out.append(f"{where}#{i} «{s.get('label')}» (ядро «{s.get('core') or '—'}», "
                           f"ув. {s.get('confidence', 0):.2f})")
    return out


def facets_name(report: dict, hit: Hit, pattern: str) -> bool:
    text = " | ".join((report.get("facets_en") or []) + (report.get("facets") or [])).lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", text) for a in hit.aliases) or (
        bool(pattern) and bool(re.search(pattern, text))
    )


def layer_summary(report: dict) -> tuple[float, float, int]:
    """(мин. уверенность в ТОП, макс. в «наблюдать», сколько «наблюдать» выше худшего в ТОП)."""
    top = [s.get("confidence") or 0.0 for s in report.get("signals") or []]
    watch = [s.get("confidence") or 0.0 for s in report.get("watch") or []]
    lo = min(top) if top else 0.0
    return lo, max(watch, default=0.0), sum(c > lo for c in watch)


def oracle_rank(report: dict, fp, params: dict) -> tuple[float, int, int] | None:
    """Уверенность модели роста для цели с её следом на дату и место среди зарождающихся.

    Признаки считаются `model.query_features` по пулу отчёта плюс цель — той же функцией, что
    при обучении и в `ask.score`. Игроков и чтений у цели нет: ровно то, что у дебютной темы.
    """
    from pelican.weak import model as model_mod

    if fp is None or not fp.works or fp.last_year_works < 0:
        return None
    pool = [s for w in EMERGING for s in report.get(w) or []]
    rows = [
        {
            "core_last_year_share": s.get("core_recent_share"),
            "core_works": float(s.get("core_works") or 0),
            "players": float(len(s.get("companies") or [])),
            "money_stream": 1.0 if s.get("stream") == "money" else 0.0,
            **{src: (s.get("judgments") or {}).get(src) for src in model_mod.COMMERCE_SOURCE.values()},
        }
        for s in pool
    ]
    rows.append({"core_last_year_share": fp.last_year_works / fp.works, "core_works": float(fp.works),
                 "players": 0.0, "money_stream": 0.0})
    feats = model_mod.query_features(rows)
    probs = [model_mod.probability(f, params).probability for f in feats]
    mine = probs[-1]
    return mine, 1 + sum(p > mine for p in probs[:-1]), len(probs)


def skew(arm_dir: Path) -> list[str]:
    """Распределение признаков модели роста: обучение (TSV бэктеста) против ретро-отчётов."""

    def q(xs: list[float]) -> str:
        if not xs:
            return "—"
        qs = statistics.quantiles(xs, n=4) if len(xs) > 1 else [xs[0]] * 3
        return f"n={len(xs)} · {qs[0]:.2f} / {qs[1]:.2f} / {qs[2]:.2f}"

    lines = ["| набор | доля `core_last_year_share` (квартили) | пропусков | `core_works` (квартили) |",
             "|---|---|---|---|"]
    for tsv in sorted((REPO / "reports").glob("bt-*/backtest-classifier.tsv")):
        rows = list(csv.DictReader(tsv.open(encoding="utf-8"), delimiter="\t"))
        rows = [r for r in rows if r.get("gemma на срезе") == "emerging"] or rows
        share = [float(r["core_last_year_share"]) for r in rows if r.get("core_last_year_share") not in ("", None, "-1", "-1.0")]
        miss = len(rows) - len(share)
        works = [float(r["core_works"]) for r in rows if r.get("core_works") not in ("", None)]
        lines.append(f"| обучение {tsv.parent.name} | {q(share)} | {miss} из {len(rows)} | {q(works)} |")
    share, works, miss, n = [], [], 0, 0
    for p in sorted(arm_dir.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        for s in (x for w in EMERGING for x in r.get(w) or []):
            n += 1
            if s.get("core_recent_share") is None:
                miss += 1
            else:
                share.append(float(s["core_recent_share"]))
            works.append(float(s.get("core_works") or 0))
    lines.append(f"| ретро `{arm_dir.name}` | {q(share)} | {miss} из {n} | {q(works)} |")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hits", type=Path, default=HITS)
    ap.add_argument("--arm", default="final")
    ap.add_argument("--no-db", action="store_true", help="без базы: только отчёты и TSV")
    args = ap.parse_args()

    arm_dir = OUT / args.arm
    hits = [h for h in load(args.hits) if report_path(h.cut, h.query, arm_dir).exists()]
    params = json.loads((REPO / "src" / "pelican" / "weak" / "growth.json").read_text(encoding="utf-8"))

    store = None
    if not args.no_db:
        from pelican.config import settings
        from pelican.store import Store

        store = Store(settings.storage_target).__enter__()

    def trace(cut: date, aliases: list[str]):
        """Лучший по числу работ след псевдонимов цели на дату среза."""
        from pelican.weak import asof
        from pelican.weak.core import footprints

        asof.AS_OF = cut
        try:
            prints = footprints(store, aliases)
        finally:
            asof.AS_OF = None
        return max(prints.values(), key=lambda f: f.works, default=None)

    rows, soft_lines, verdicts = [], [], {}
    for h in hits:
        report = json.loads(report_path(h.cut, h.query, arm_dir).read_text(encoding="utf-8"))
        pattern = soft_pattern(h.tech)
        place = locate(report, h.aliases)
        soft = soft_hits(report, pattern)
        fp = trace(h.cut, h.aliases) if store else None
        works = fp.works if fp else None
        named = facets_name(report, h, pattern)
        orc = oracle_rank(report, fp, params) if fp else None
        lo, hi, inverted = layer_summary(report)
        # Первый этап, где цель потерялась.
        if place.where in ("top",):
            stage = "найдена строго"
        elif soft:
            stage = "оценщик: есть мягкое совпадение — на решение асессора"
        elif works == 0:
            stage = "корпус: ноль работ на дату"
        elif not named:
            stage = "направления: не названа, в пул не попала"
        else:
            stage = "пул: направление названо, кандидата нет"
        if place.where == "watch":
            stage = "слои: «наблюдать»"
        verdicts[stage] = verdicts.get(stage, 0) + 1
        orc_txt = f"{orc[0]:.2f}, место {orc[1]} из {orc[2]}" if orc else "—"
        rows.append(
            f"| {h.tech} | {h.cut} | {place.where}{' #' + str(place.rank) if place.rank else ''} | "
            f"{len(soft)} | {'—' if works is None else works}"
            f"{'' if not fp or fp.last_year_works < 0 else f' ({fp.last_year_works} за год)'} | "
            f"{'да' if named else 'нет'} | {orc_txt} | {lo:.2f} / {hi:.2f} / {inverted} | {stage} |"
        )
        if soft:
            soft_lines.append(f"- **{h.tech} @ {h.cut}**: " + "; ".join(soft))

    lines = [
        "# Ретро-тест: где теряется цель",
        "",
        f"Сгенерировано `scripts/retro_diagnose.py` по `reports/retro/{args.arm}`, набор "
        f"`{args.hits.name}`, готовых срезов: {len(hits)}. Методика — в докстринге скрипта.",
        "",
        "Колонки: строгое место (`retro_hits.locate`) · мягких совпадений · работ в корпусе на "
        "дату (за последний год) · названа ли в направлениях · оракул модели роста "
        "(уверенность цели и место среди зарождающихся пула) · слои: мин. уверенность в ТОП / "
        "макс. в «наблюдать» / сколько «наблюдать» выше худшего в ТОП · первый этап потери.",
        "",
        "| технология | срез | строго | мягко | корпус | в направлениях | оракул роста | слои | этап |",
        "|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## Сводка по этапам",
        "",
        *(f"- {k}: {v}" for k, v in sorted(verdicts.items(), key=lambda kv: -kv[1])),
        "",
        "## Мягкие совпадения — на решение асессора",
        "",
        *(soft_lines or ["нет"]),
        "",
        "## Признаки модели роста: обучение против выдачи",
        "",
        *skew(arm_dir),
        "",
    ]
    out = OUT / "diagnose.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"→ {out}")


if __name__ == "__main__":
    main()
