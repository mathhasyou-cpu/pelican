"""Где теряем тренды — в пуле или в ранжире; заодно метка «вошёл в список заказчика».

    python scripts/label_listed.py --dir reports/bt-2024-09-15
    python scripts/label_listed.py --dir reports/bt-2020-09-15,reports/bt-2022-09-15,...
    python scripts/label_listed.py --dir reports/ask-D        # живой прогон, без среза

## Зачем

Покрытие списка методолога (`judge_match.py`) считается только по ТОП конвейера. Сколько
строк списка вообще есть в ПУЛЕ кандидатов (ТОП + снятые по жанру + с одним игроком) —
неизвестно, а от этого зависит, что чинить: пул ≈ ТОП — тренды не попадают в кандидаты, и
ранжир бесполезен; пул ≫ ТОП — тренды есть, мы их не поднимаем.

Тем же судьёй (`judge_match._judge_many`, видит ТОЛЬКО названия) размечается каждый
кандидат среза: `listed` = 1, если судья отнёс его к строке списка 2026. Метка честная по
построению для бэктеста — список составлен в 2026, признаки кандидата на срезе — и это
цель обучения, по которой нас судят, а не рост публикаций (`train_growth_model.py
--outcome listed`).

⚠️ Судья снисходителен (зачёт зонтика, 13 из 14 на ручной разметке) и один на ТОП и пул —
число «в пуле» такая же ВЕРХНЯЯ оценка, как покрытие ТОП, и сравнивать их между собой
можно, а с чужими числами — нет.

Пишет `<папка>/judge-pool.tsv` (кандидат → строка списка) и дописывает столбец `listed`
в `<папка>/backtest-classifier.tsv` (остальные столбцы не трогает).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_spec = importlib.util.spec_from_file_location("judge_match", REPO / "scripts" / "judge_match.py")
judge_match = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(judge_match)


def pool(folder: Path) -> list[tuple[str, str, str]]:
    """(домен, где, название) по всем группам прогона — тот же пул, что у `backtest_classifier`."""
    out = []
    for path in sorted(folder.glob("ask-cards-*.json")):
        domain = path.stem.removeprefix("ask-cards-").replace("-", " ")
        run = json.loads(path.read_text(encoding="utf-8"))
        out += [(domain, "ТОП", s["label"]) for s in run.get("signals", [])]
        out += [
            (domain, "контроль", s["label"])
            for s in run.get("excluded", []) + run.get("thin", [])
        ]
    return out


def read_prior(path: Path) -> dict[tuple[str, str], str]:
    """Прошлая разметка: судья не гоняется заново по уже суженным названиям."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {(r["домен"], r["кандидат"]): r["строка датасета"] for r in csv.DictReader(f, delimiter="\t")}  # noqa: E501


def label(folder: Path, reassess: bool = False) -> dict[str, int]:
    data = judge_match._dataset()
    cands = pool(folder)
    out = folder / "judge-pool.tsv"
    prior = {} if reassess else read_prior(out)
    todo = [(d, w, lab) for d, w, lab in cands if (d, lab) not in prior and data.get(d)]
    print(f"{folder.name}: пул {len(cands)}, судить {len(todo)} (из кэша {len(cands) - len(todo)})")
    verdicts = asyncio.run(judge_match._judge_many([(lab, data[d]) for d, _w, lab in todo]))
    hit: dict[tuple[str, str], str] = dict(prior)
    for (d, _w, lab), (n, _why) in zip(todo, verdicts, strict=True):
        hit[(d, lab)] = data[d][n - 1] if n else ""
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["домен", "где", "кандидат", "строка датасета"])
        for d, where, lab in cands:
            w.writerow([d, where, lab, hit.get((d, lab), "")])

    in_top = {hit[(d, lab)] for d, w, lab in cands if w == "ТОП" and hit.get((d, lab))}
    in_pool = {hit[(d, lab)] for d, _w, lab in cands if hit.get((d, lab))}
    total = sum(len(v) for v in data.values())
    print(
        f"  строк списка 2026: в ТОП {len(in_top)}, в ПУЛЕ {len(in_pool)} из {total}; "
        f"пул {len(cands)} (ТОП {sum(w == 'ТОП' for _d, w, _l in cands)})"
    )
    for row in sorted(in_pool - in_top):
        print(f"   только в пуле: {row[:88]}")

    tsv = folder / "backtest-classifier.tsv"
    if tsv.exists():
        with tsv.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            rows = list(reader)
            fields = list(reader.fieldnames or [])
        if "listed" not in fields:
            fields.append("listed")
        for r in rows:
            r["listed"] = int(bool(hit.get((r["домен"], r["направление"]))))
        with tsv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
            w.writeheader()
            w.writerows(rows)
        print(f"  listed=1 у {sum(int(r['listed']) for r in rows)} из {len(rows)} строк {tsv.name}")
    return {"top": len(in_top), "pool": len(in_pool), "n": len(cands)}


def main() -> None:
    ap = argparse.ArgumentParser(description="строки списка в пуле против ТОП; метка listed")
    ap.add_argument("--dir", required=True, help="папки прогонов под срезом, через запятую")
    ap.add_argument("--reassess", action="store_true", help="судить заново, не брать из кэша")
    args = ap.parse_args()
    for d in args.dir.split(","):
        label(REPO / d.strip(), args.reassess)
        rankings(REPO / d.strip())



#: Оценщики для сравнения правил верха: столбцы `backtest-classifier.tsv`.
RANKERS: tuple[str, ...] = ("core_momentum", "core_last_year_share", "nn_sim_now", "p роста")

#: То же на ЖИВОМ прогоне: `backtest-classifier.tsv` там не бывает, а всё, из чего сложен
#: ключ порядка, лежит в самих карточках. Имена — как в `weak/ask.py`, чтобы правило из
#: выдачи и правило из замера читались одним словом.
LIVE_RANKERS: tuple[str, ...] = (
    "listed",
    "core_recent_share",
    "игроков",
    "emerging + ≥2 игрока",
)


def live_rows(folder: Path) -> list[dict[str, str]]:
    """Строки для `rankings()` из `ask-cards-*.json` живого прогона.

    ⚠️ У `noise` модели нет по построению (`ask._score_by_model` считается по пулу ПОСЛЕ
    отсева шума) — пустая клетка, а не ноль: правила порядка всё равно сравниваются на
    «не noise», а случайный верх из всего пула шум включает.
    """
    rows: list[dict[str, str]] = []
    for path in sorted(folder.glob("ask-cards-*.json")):
        domain = path.stem.removeprefix("ask-cards-").replace("-", " ")
        run = json.loads(path.read_text(encoding="utf-8"))
        for where, group in (("ТОП", "signals"), ("контроль", "excluded"), ("контроль", "thin")):
            for s in run.get(group, []):
                players = len(s.get("companies") or [])
                rows.append(
                    {
                        "домен": domain,
                        "направление": s["label"],
                        "где": where,
                        "gemma на срезе": s.get("kind", ""),
                        "listed": _num(s.get("listed")),
                        "core_recent_share": _num(s.get("core_recent_share")),
                        "игроков": str(players),
                        "emerging + ≥2 игрока": str(
                            int(s.get("kind") == "emerging" and players >= 2)
                        ),
                    }
                )
    return rows


def _num(value: float | None) -> str:
    """`None` это «измерить нечем» — пустая клетка, как в TSV бэктеста, а не ноль."""
    return "" if value is None else repr(float(value))


def rankings(folder: Path, shuffles: int = 50) -> dict[str, float]:
    """Сколько строк списка ловит верх того же размера, что ТОП конвейера, при разных
    правилах: ТОП как есть; случайный верх; «не noise» + ранжир одним столбцом.

    Признаки берутся из `backtest-classifier.tsv` под срезом, а у живого прогона — из
    самих карточек (`live_rows`): сравнение правил порядка на ОДНОМ пуле второго прогона
    не требует, и потому это единственный способ выбрать ключ порядка дешевле, чем
    прогоном шести доменов на каждое правило.

    ⚠️ Замер 2026-09-21 по трём срезам: ТОП конвейера (жанр `emerging` + ≥2 игрока) ловит
    7 / 9 / 14 при случайном верхе из всего пула 6.8 / 9.8 / 11.6 и из не-noise 8.9 / 11.9 /
    14.0 — то есть отбор по жанру и игрокам НЕ ЛУЧШЕ случайного: корзина «один игрок»
    попадает в список чаще всех (44–60%), `mature` — так же часто, как ТОП (~20%), делит
    только `noise` (6–8%). «Не noise + доля свежих работ ядра» даёт 12 / 16 / 14.
    """
    import random

    jp = folder / "judge-pool.tsv"
    tsv = folder / "backtest-classifier.tsv"
    if not jp.exists():
        return {}
    with jp.open(encoding="utf-8", newline="") as f:
        hit = {(r["домен"], r["кандидат"]): r["строка датасета"] for r in csv.DictReader(f, delimiter="\t")}  # noqa: E501
    if tsv.exists():
        with tsv.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        rankers = RANKERS
    else:
        rows, rankers = live_rows(folder), LIVE_RANKERS
    if not rows:
        return {}
    k: dict[str, int] = {}
    for r in rows:
        k[r["домен"]] = k.get(r["домен"], 0) + (r["где"] == "ТОП")

    def cover(key, keep=lambda r: True) -> int:
        """`key` — ключ сортировки, как в `sorted`: меньше значит выше."""
        found = set()
        for d, kk in k.items():
            here = sorted((r for r in rows if r["домен"] == d and keep(r)), key=key)
            found |= {hit[(d, r["направление"])] for r in here[:kk] if hit.get((d, r["направление"]))}  # noqa: E501
        return len(found)

    def col(name):
        return lambda r: float(r[name]) if r.get(name, "") != "" else -1.0

    no_noise = lambda r: r["gemma на срезе"] != "noise"  # noqa: E731
    rnd = random.Random(20260921)

    def by_col(name) -> float:
        """⚠️ Ничья разводится случайно и усредняется по прогонам: у правила с двумя
        значениями («жанр + игроки») и у целого числа игроков ничьих больше, чем мест, и без
        этого число мерило бы исходный порядок списка, то есть прежний ТОП.
        """
        score = col(name)
        draws = (cover(lambda r: (-score(r), rnd.random()), no_noise) for _ in range(shuffles))
        return sum(draws) / shuffles

    out = {
        "ТОП конвейера": float(cover(lambda r: 0 if r["где"] == "ТОП" else 1)),
        "случайный верх": sum(cover(lambda r: rnd.random()) for _ in range(shuffles)) / shuffles,
        "случайный верх из не noise": sum(
            cover(lambda r: rnd.random(), no_noise) for _ in range(shuffles)
        )
        / shuffles,
        **{f"не noise + {n}": by_col(n) for n in rankers if n in rows[0]},
    }
    print("  верх того же размера, что ТОП конвейера, ловит строк списка:")
    for name, v in out.items():
        print(f"    {name:36s} {v:.1f}")
    return out


if __name__ == "__main__":
    main()
