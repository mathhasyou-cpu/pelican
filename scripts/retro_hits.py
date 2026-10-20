"""Ретро-тест «увидеть Attention в 2018»: находит ли система будущие прорывы на дату, когда
они были одной-двумя работами.

Тест экспертов заказчика (консультация 16.10): «в 2017–2018 году видим статью "Attention
is all you need" и понимаем, что GenAI будет главным трендом, — когда ещё нет ни OpenAI,
ни Anthropic. В этом и смысл всего упражнения».

Приём — **оценка на «дебютных» темах** (debutant topics): эталон — темы, которые потом
стали большими, и вопрос, видит ли их метод на дату до взлёта. Так оценивали Augur
(Salatino, Osborne, Motta: 1 408 тем 2000–2011, точность и полнота раннего обнаружения) и
«trend archaeology» Фраунгофера (ретроспективная библиометрия). Срез — тот же
point-in-time, что у бэктеста (`weak.asof`): корпус обрезан датой, новости — окном
`after:`/`before:`, всё из будущего отбрасывается.

    python scripts/retro_hits.py                       # все строки scripts/retro_hits.tsv
    python scripts/retro_hits.py --domain ИИ           # только ИИ
    python scripts/retro_hits.py --cached              # разрешить кэш живого поиска
    python scripts/retro_hits.py --report-only         # пересчитать таблицу по готовым JSON

Выход — `reports/retro/<срез>-<запрос>.json` (отчёт прогона, как у `measure_ask.py`) и
`reports/retro/retro-hits.md` — таблица «технология / срез / где оказалась / след в
корпусе на дату».

⚠️ **Читать вместе со следом корпуса на дату.** arXiv в корпусе — с 2002 года, а срезы
OpenAlex по CS и Engineering — только с 2023-01 (docs/science-backfill.md): на срезах
биотеха и энергетики до 2023 научная половина тонкая. «Не найдено» при нуле работ в корпусе
— это потолок источников, а не промах ранжирования (как «потолок 82 из 100» у датасета).
⚠️ **Пол случайного порядка.** Доля попаданий в ТОП-15 сравнивается с ожидаемой при
случайном порядке того же пула: для технологии, дошедшей до пула из N кандидатов, это
15 / N. Попадание в пул без попадания в ТОП — вина порядка, не поиска.
⚠️ Даты срезов — через год-полтора после ключевой работы: сигнал уже есть, бума ещё нет.
Это догадка, а не замер (docs/todo.md); правило назначать ДО прогона — менять даты по его
итогам значит подгонять тест под ответ.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HITS = REPO / "scripts" / "retro_hits.tsv"
OUT = REPO / "reports" / "retro"
TOP = 15


@dataclass(slots=True)
class Hit:
    tech: str
    aliases: list[str]
    cut: date
    query: str
    domain: str
    why: str


@dataclass(slots=True)
class Place:
    """Где технология оказалась в выдаче прогона."""

    where: str  # top / watch / beyond / excluded / missing
    rank: int | None = None  # место в ТОП (с единицы)
    label: str = ""
    detail: str = ""  # жанр исключённого
    pool: int = 0  # размер пула кандидатов прогона


def load(path: Path = HITS) -> list[Hit]:
    with path.open(encoding="utf-8") as f:
        return [
            Hit(
                r["tech"],
                [a.strip().lower() for a in r["aliases"].split("|") if a.strip()],
                date.fromisoformat(r["cut"]),
                r["query"],
                r["domain"],
                r["why"],
            )
            for r in csv.DictReader(f, delimiter="\t")
        ]


def _texts(sig: dict) -> str:
    parts = [sig.get("label", ""), sig.get("title", ""), sig.get("core", "")]
    parts += sig.get("variants") or []
    return " | ".join(p for p in parts if p).lower()


def matches(sig: dict, aliases: list[str]) -> bool:
    """Направление — эта технология, если хоть один псевдоним стоит в его ярлыке, названии,
    ядре или вариантах написания ЦЕЛЫМ словом («vit» не ловит «activity»)."""
    text = _texts(sig)
    return any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", text) for a in aliases)


def locate(report: dict, aliases: list[str]) -> Place:
    """Место технологии в отчёте: ТОП (с номером), «наблюдать», за пределами ТОП,
    исключена (с жанром) или не найдена вовсе. Берётся лучшее из мест."""
    signals = report.get("signals") or []
    watch = report.get("watch") or []
    thin = report.get("thin") or []
    excluded = report.get("excluded") or []
    pool = len(signals) + len(watch) + len(thin) + len(excluded)
    for i, s in enumerate(signals, 1):
        if matches(s, aliases):
            return Place("top", i, s.get("label", ""), pool=pool)
    for where, items in (("watch", watch), ("beyond", thin)):
        for s in items:
            if matches(s, aliases):
                return Place(where, None, s.get("label", ""), pool=pool)
    for s in excluded:
        if matches(s, aliases):
            return Place("excluded", None, s.get("label", ""), s.get("kind_label", ""), pool)
    return Place("missing", pool=pool)


def _slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-")[:40]


def report_path(cut: date, query: str, out: Path = OUT) -> Path:
    return out / f"{cut.isoformat()}-{_slug(query)}.json"


#: Плечи замера флагов (docs/final-measure.md, шаг 2): имя → включённые флаги `weak.flags`.
#: ⚠️ Плечи идут ПОДРЯД на каждом срезе, а не прогонами по очереди: прогон всех срезов —
#: часы, а суточный кэш живого поиска (`weak.cache`) должен отдать всем плечам одни и те же
#: новости, иначе разница плеч смешается с дрейфом выдачи Google News.
ARMS: dict[str, tuple[str, ...]] = {
    "base": (),
    "layers": ("LAYERS",),
    "technique": ("TECHNIQUE",),
    "onpoint": ("ONPOINT_COMPANIES",),
    "extra-layers": ("EXTRA_SOURCES", "LAYERS"),
    "burst": ("BURST",),
    "burst-only": ("BURST_ONLY",),
    # Те же флаги после починки слоёв (docs/todo.md §116): свои папки, чтобы не смешать
    # с прогонами старого правила.
    "base2": (),
    "layers2": ("LAYERS",),
    "extra-layers2": ("EXTRA_SOURCES", "LAYERS"),
    # То же с добором ТОП до 15 из «наблюдать» (docs/todo.md §117).
    "extra-layers3": ("EXTRA_SOURCES", "LAYERS"),
    # Финал к сдаче — поверх исправлений ревью (REPORT_VERSION 2): база и связка заново.
    "base4": (),
    "final": ("EXTRA_SOURCES", "LAYERS"),
    # Финал + направления на дату среза (lookahead bias в `expand`, scripts/retro_diagnose.py).
    "dated": ("EXTRA_SOURCES", "LAYERS", "DATED_FACETS"),
}
_FLAGS = ("LAYERS", "TECHNIQUE", "ONPOINT_COMPANIES", "EXTRA_SOURCES", "DATED_FACETS")


def set_arm(arm: str) -> None:
    from pelican.weak import flags

    for name in _FLAGS:
        setattr(flags, name, name in ARMS[arm])
    on = ARMS[arm]
    flags.BURST = "only" if "BURST_ONLY" in on else ("add" if "BURST" in on else "")


def run_one(store, cut: date, query: str, out: Path = OUT) -> dict:
    """Прогон конвейера на срезе. ⚠️ Срез ставится ДО первого обращения к источникам."""
    from pelican.weak import ask, asof, page

    asof.AS_OF = cut
    try:
        result = ask.run(store, query, on_progress=lambda m: print(f"    {m}"))
    finally:
        asof.AS_OF = None
    payload = json.loads(page.to_json(result))
    report_path(cut, query, out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    return payload


def corpus_trace(store, cut: date, aliases: list[str]) -> int:
    """Сколько работ корпуса называли технологию к дате среза (наибольшее по псевдонимам)."""
    from pelican.weak import asof
    from pelican.weak.core import footprints

    asof.AS_OF = cut
    try:
        prints = footprints(store, aliases)
    finally:
        asof.AS_OF = None
    return max((fp.works for fp in prints.values()), default=0)


_WHERE = {
    "top": "ТОП",
    "watch": "наблюдать",
    "beyond": "за пределами ТОП",
    "excluded": "исключена",
    "missing": "не найдена",
}


def table(rows: list[tuple[Hit, Place, int | None]]) -> str:
    lines = [
        "# Ретро-тест «увидеть Attention в 2018»",
        "",
        "Сгенерировано `scripts/retro_hits.py`. Методика — в докстринге скрипта.",
        "",
        "| технология | срез | запрос | где | направление в выдаче | работ в корпусе на дату |",
        "|---|---|---|---|---|---|",
    ]
    for hit, place, works in rows:
        where = _WHERE[place.where] + (f" №{place.rank}" if place.rank else "")
        if place.detail:
            where += f" ({place.detail})"
        lines.append(
            f"| {hit.tech} | {hit.cut} | {hit.query} | {where} | {place.label or '—'} | "
            f"{'—' if works is None else works} |"
        )
    measured = [(h, p) for h, p, _ in rows]
    in_top = sum(p.where == "top" for _, p in measured)
    seen = sum(p.where in ("top", "watch") for _, p in measured)
    in_pool = sum(p.where != "missing" for _, p in measured)
    floor = sum(min(TOP / p.pool, 1.0) for _, p in measured if p.where != "missing" and p.pool)
    n = len(measured)
    lines += [
        "",
        f"- **в ТОП-15**: {in_top} из {n}; **в ТОП или «наблюдать»**: {seen} из {n}",
        (
            f"- **дошли до пула кандидатов**: {in_pool} из {n} (не дошедшие — промах поиска "
            "или потолок корпуса, см. последнюю колонку)"
        ),
        (
            "- **пол случайного порядка**: в ТОП-15 при случайном порядке пула ожидалось бы "
            f"{floor:.1f} из {in_pool} дошедших до пула"
        ),
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", help="только строки этого домена (колонка domain)")
    ap.add_argument("--cached", action="store_true", help="разрешить кэш живого поиска")
    ap.add_argument("--fresh", action="store_true", help="перезапустить прогоны, даже если JSON есть")
    ap.add_argument("--report-only", action="store_true", help="только таблица по готовым JSON")
    ap.add_argument(
        "--hits",
        type=Path,
        default=HITS,
        help="набор срезов; scripts/retro_lead.tsv — срок раннего обнаружения (+6/+12/+18 мес)",
    )
    ap.add_argument(
        "--arms",
        help=f"плечи замера флагов через запятую ({', '.join(ARMS)}); каждое — в reports/retro/<плечо>/",
    )
    args = ap.parse_args()

    from pelican import locks
    from pelican.config import settings
    from pelican.store import Store
    from pelican.weak import cache

    hits = [h for h in load(args.hits) if not args.domain or h.domain == args.domain]
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else [None]
    unknown = [a for a in arms if a is not None and a not in ARMS]
    if unknown:
        ap.error(f"неизвестные плечи: {', '.join(unknown)}")
    outs = {a: OUT / a if a else OUT for a in arms}
    for out in outs.values():
        out.mkdir(parents=True, exist_ok=True)
    # Плечам нужен кэш: он и делает их сравнимыми (см. ARMS).
    cache.ENABLED = args.cached or args.arms is not None
    runs: dict[tuple[str | None, date, str], dict] = {}
    with locks.hold("model"), Store(settings.storage_target) as store:
        for cut, query in dict.fromkeys((h.cut, h.query) for h in hits):
            for arm in arms:
                path = report_path(cut, query, outs[arm])
                if path.exists() and not args.fresh:
                    runs[(arm, cut, query)] = json.loads(path.read_text(encoding="utf-8"))
                elif args.report_only:
                    continue
                else:
                    print(f"СРЕЗ {cut} · «{query}»" + (f" · плечо {arm}" if arm else ""))
                    if arm:
                        set_arm(arm)
                    runs[(arm, cut, query)] = run_one(store, cut, query, outs[arm])
        works_of = {h.tech: corpus_trace(store, h.cut, h.aliases) for h in hits}
    for arm in arms:
        rows: list[tuple[Hit, Place, int | None]] = []
        for h in hits:
            report = runs.get((arm, h.cut, h.query))
            if report is None:
                continue
            place = locate(report, h.aliases)
            rows.append((h, place, works_of[h.tech]))
            print(f"{arm or ''} {h.tech} @ {h.cut}: {_WHERE[place.where]} {place.rank or ''}")
        out = outs[arm] / "retro-hits.md"
        out.write_text(table(rows), encoding="utf-8")
        print(f"→ {out}")


if __name__ == "__main__":
    from pelican import locks

    # Исполнитель API держит замок `model` на время задания: «занято» — код 75, как у
    # `measure_ask`, а не трассировка.
    try:
        main()
    except locks.Busy as exc:
        print(f"занято: {exc}")
        sys.exit(75)
