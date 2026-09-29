"""Лесенка горизонтов: за какой срок после среза исход «вырос» вообще становится виден.

    python scripts/backtest_horizons.py --dir reports/bt-2020-09-15,reports/bt-2022-09-15,reports/bt-2024-09-15

## Зачем

Бэктест меряет «взлетело» через `HORIZON_YEARS = 2` после среза — это наш выбор, не
требование заказчика. Вопрос: виден ли тот же исход раньше — через год, полгода, квартал,
месяц, неделю — и с какого срока ранжиры (momentum ядра, модель роста, ТОП конвейера)
перестают его делить. Ответ решает, какой горизонт честно обещать и на каком учить.

## Как

Ядра — те же, что в `backtest.tsv` (пул ТОП + контроль), «на срезе» — оттуда же (один
набор срезов корпуса `weak.core.SLICES`, тот же корпус). Прирост считается ОДНИМ проходом
по корпусу на все горизонты сразу: работы ядра с `observed_at` в (срез, срез + h].
Исход на каждом горизонте — тот же, что у `backtest_classifier.outcomes`: log(во сколько
раз) минус медиана ДОМЕНА, флаг «выше медианы домена» (`grew_excess`).

⚠️ Работы с датой 1 января выброшены из прироста на ВСЕХ горизонтах: у openalex это
заглушка вместо даты (docs/sources-science.md), и горизонт, пересекающий Новый год
(полгода и дальше от сентябрьского среза), получал бы скачок у всех ядер разом. Из-за
этого «2 года» здесь чуть ниже `работ после` в `backtest.tsv` — сверка печатается.
⚠️ Дата работы — дата публикации, а не появления в корпусе: «через неделю» — это работы,
датированные той неделей, которые в реальности стали видны позже (задержка индексации
openalex — недели). Короткие горизонты — про сам сигнал, а не про то, что мы бы увидели.
⚠️ `p роста` на срезах 2020 и 2022 — модель, обученная на 2024 (внутри выборки на 2024);
честная линия — momentum ядра, у неё обучения нет.

Пишет `reports/backtest-horizons.md` и `.json`.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.weak import core as core_mod  # noqa: E402

_spec = importlib.util.spec_from_file_location("backtest_signals", REPO / "scripts" / "backtest_signals.py")
backtest_signals = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backtest_signals)

#: Горизонты лесенки: имя → сдвиг от среза.
HORIZONS: tuple[tuple[str, timedelta | int], ...] = (
    ("1 нед", timedelta(days=7)),
    ("2 нед", timedelta(days=14)),
    ("1 мес", timedelta(days=30)),
    ("3 мес", timedelta(days=91)),
    ("6 мес", timedelta(days=182)),
    ("1 год", 1),
    ("2 года", 2),
)
#: Оценщики: имя → столбец `backtest-classifier.tsv` (или правило).
SCORERS: tuple[tuple[str, str], ...] = (
    ("ТОП конвейера", "где"),
    ("модель этапа 1 (p)", "p модели"),
    ("momentum ядра", "core_momentum"),
    ("доля свежих работ ядра", "core_last_year_share"),
    ("модель роста (p)", "p роста"),
    ("игроков в карточке", "players"),
    ("в списке заказчика", "listed"),
)
GROWTH = backtest_signals.GROWTH
SHUFFLES = 200
OUT_MD = REPO / "reports" / "backtest-horizons.md"
OUT_JSON = REPO / "reports" / "backtest-horizons.json"


def horizon_date(cut: date, shift: timedelta | int) -> date:
    return cut + shift if isinstance(shift, timedelta) else cut.replace(year=cut.year + shift)


def grown_by_horizon(store: Store, cut: date, cores: list[str]) -> dict[str, list[int]]:
    """Ядро → прирост работ на каждом горизонте, одним проходом по корпусу на пачку ядер."""
    ends = [horizon_date(cut, s) for _n, s in HORIZONS]
    last = max(ends)
    specs = [(c, core_mod._match(c, False)) for c in cores]
    out: dict[str, list[int]] = {}
    for start in range(0, len(specs), core_mod.SCAN_CHUNK):
        chunk = specs[start : start + core_mod.SCAN_CHUNK]
        flags = ", ".join(f"({core_mod.match_sql(m)}) AS m{i}" for i, (_c, m) in enumerate(chunk))
        aggs = ", ".join(
            f"count(*) FILTER (WHERE m{i} AND observed_at <= DATE '{end.isoformat()}') AS c{i}_{j}"
            for i in range(len(chunk))
            for j, end in enumerate(ends)
        )
        sql = (
            f"SELECT {aggs} FROM (SELECT observed_at, {flags} FROM works "
            f"WHERE source IN ('arxiv', 'openalex') AND observed_at > DATE '{cut.isoformat()}' "
            f"AND observed_at <= DATE '{last.isoformat()}' "
            f"AND NOT (extract(month FROM observed_at) = 1 AND extract(day FROM observed_at) = 1){core_mod._slice()}) AS t"
        )
        row = store.query(sql)[0]
        for i, (c, _m) in enumerate(chunk):
            out[c] = [int(row[i * len(ends) + j] or 0) for j in range(len(ends))]
        print(f"  ядер {min(start + len(chunk), len(specs))}/{len(specs)}", flush=True)
    return out


def auc(pos: list[float], neg: list[float]) -> float:
    wins = sum(1 for x in pos for y in neg if x > y)
    ties = sum(1 for x in pos for y in neg if x == y)
    return (wins + 0.5 * ties) / max(len(pos) * len(neg), 1)


def floor(scores: list[float], flags: list[int], seed: int = 20260921) -> float:
    rnd = random.Random(seed)
    labels = list(flags)
    got = []
    for _ in range(SHUFFLES):
        rnd.shuffle(labels)
        got.append(auc([s for s, g in zip(scores, labels, strict=True) if g],
                       [s for s, g in zip(scores, labels, strict=True) if not g]))  # fmt: skip
    got.sort()
    return got[int(0.95 * len(got))]


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else 0.0


def score_of(r: dict, column: str) -> float | None:
    if column == "где":
        return 1.0 if r["где"] == "ТОП" else 0.0
    v = r.get(column, "")
    return float(v) if v not in ("", None) else None


def excess_flags(rows: list[dict], j: int) -> tuple[list[float], list[int]]:
    """log(во сколько раз) минус медиана домена и флаг «выше медианы» на горизонте j."""
    logs = []
    for r in rows:
        before, grown = r["before"], r["grown"][j]
        after = before + grown
        ratio = (after / before) if before else (GROWTH if after else 0.0)
        logs.append(math.log(max(ratio, 0.05)))
    by_domain: dict[str, list[float]] = {}
    for r, v in zip(rows, logs, strict=True):
        by_domain.setdefault(r["домен"], []).append(v)
    median = {d: statistics.median(v) for d, v in by_domain.items()}
    excess = [v - median[r["домен"]] for r, v in zip(rows, logs, strict=True)]
    flags = [int(v > median[r["домен"]]) for r, v in zip(rows, logs, strict=True)]
    return excess, flags


def load(folder: Path) -> tuple[date, list[dict]]:
    cut = backtest_signals.as_of_of(folder, None)
    with (folder / "backtest.tsv").open(encoding="utf-8", newline="") as f:
        before = {(r["домен"], r["направление"]): r for r in csv.DictReader(f, delimiter="\t")}
    with (folder / "backtest-classifier.tsv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    for r in rows:
        b = before[(r["домен"], r["направление"])]
        r["ядро"] = b["ядро"]
        r["before"] = int(b["работ на срезе"])
        r["after_2y_tsv"] = int(b["работ после"])
        r["срез"] = cut.isoformat()
    return cut, rows


def measure(rows: list[dict], title: str) -> dict:
    """Таблица горизонт × оценщик (AUC, пол) и корреляция каждого горизонта с двухлетним."""
    names = [n for n, _s in HORIZONS]
    excess_2y, _ = excess_flags(rows, len(HORIZONS) - 1)
    table: dict[str, dict] = {}
    print(f"\n=== {title}: {len(rows)} кандидатов ===")
    head = f"{'горизонт':8s} {'растут':>7s} {'ρ с 2 г':>8s} " + " ".join(f"{n[:14]:>14s}" for n, _c in SCORERS)
    print(head)
    for j, name in enumerate(names):
        excess, flags = excess_flags(rows, j)
        any_growth = sum(1 for r in rows if r["grown"][j] > 0) / len(rows)
        rho = spearman(excess, excess_2y)
        cells = {}
        line = f"{name:8s} {any_growth:7.0%} {rho:8.2f} "
        for sname, col in SCORERS:
            paired = [(score_of(r, col), g) for r, g in zip(rows, flags, strict=True)]
            paired = [(s, g) for s, g in paired if s is not None]
            if not paired or len({g for _s, g in paired}) < 2:
                cells[sname] = None
                line += f"{'—':>14s} "
                continue
            scores = [s for s, _g in paired]
            pos = [s for s, g in paired if g]
            neg = [s for s, g in paired if not g]
            a = auc(pos, neg)
            fl = floor(scores, [g for _s, g in paired])
            cells[sname] = {"auc": round(a, 2), "пол": round(fl, 2), "n": len(paired)}
            mark = "*" if a >= fl else " "
            line += f"{a:5.2f} ({fl:.2f}){mark:1s}{'':6s} "
        table[name] = {"растут": round(any_growth, 3), "rho_2y": round(rho, 2),
                       "положительных": sum(flags), **cells}
        print(line)
    print("  * — AUC не ниже пола случайности (95-й перцентиль перестановок)")
    return table


def main() -> None:
    ap = argparse.ArgumentParser(description="лесенка горизонтов исхода «вырос»")
    ap.add_argument("--dir", required=True, help="папки прогонов под срезами, через запятую")
    args = ap.parse_args()
    folders = [REPO / d.strip() for d in args.dir.split(",")]

    pooled: list[dict] = []
    per_cut: dict[str, dict] = {}
    with Store(settings.storage_target) as store:
        for folder in folders:
            cut, rows = load(folder)
            core_mod.SLICES = backtest_signals.slices_at(store, cut)
            cores = sorted({r["ядро"] for r in rows if r["ядро"]})
            print(f"{folder.name}: срез {cut}, ядер {len(cores)}, срезы openalex "
                  f"{', '.join(sorted(core_mod.SLICES)) or '—'}; прирост по {len(HORIZONS)} "
                  f"горизонтам (это минуты)...", flush=True)
            grown = grown_by_horizon(store, cut, cores)
            for r in rows:
                r["grown"] = grown.get(r["ядро"], [0] * len(HORIZONS))
            # Сверка с двухлетним «работ после» из backtest.tsv (там 1 января не выброшено).
            ours = [r["before"] + r["grown"][-1] for r in rows]
            theirs = [r["after_2y_tsv"] for r in rows]
            print(f"  сверка 2 лет с backtest.tsv: ρ Спирмена {spearman(ours, theirs):.3f}, "
                  f"наш прирост / их: {sum(o - r['before'] for o, r in zip(ours, rows)) / max(sum(t - r['before'] for t, r in zip(theirs, rows)), 1):.2f}")
            per_cut[cut.isoformat()] = measure(rows, f"срез {cut}")
            pooled += rows
    core_mod.SLICES = None
    if len(folders) > 1:
        per_cut["пул срезов"] = measure(pooled, "пул срезов")

    OUT_JSON.write_text(json.dumps(per_cut, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        "# Лесенка горизонтов: когда исход «вырос» становится виден",
        "",
        f"Сгенерировано `scripts/backtest_horizons.py` {date.today():%d.%m.%Y} по "
        f"{', '.join(f.name for f in folders)}. Исход — рост следа ядра сверх медианы домена "
        "за горизонт после среза; в клетке AUC оценщика (пол случайности), `*` — не ниже пола. "
        "«растут» — доля ядер хоть с одной работой за горизонт; ρ — Спирмен избытка на горизонте "
        "с избытком за 2 года. ⚠️ Работы с датой 1 января выброшены на всех горизонтах.",
        "",
    ]
    for title, table in per_cut.items():
        lines += [f"## {title}", "",
                  "| горизонт | растут | ρ с 2 г | " + " | ".join(n for n, _c in SCORERS) + " |",
                  "|---|---|---|" + "---|" * len(SCORERS)]
        for name, row in table.items():
            cells = []
            for sname, _c in SCORERS:
                c = row.get(sname)
                cells.append("—" if not c else f"{c['auc']:.2f} ({c['пол']:.2f}){'*' if c['auc'] >= c['пол'] else ''}")
            lines.append(f"| {name} | {row['растут']:.0%} | {row['rho_2y']:.2f} | " + " | ".join(cells) + " |")
        lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nвыписано: {OUT_MD}")


if __name__ == "__main__":
    main()
