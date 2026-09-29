"""Ретроспективный бэктест: взлетело ли то, что детектор назвал два года назад.

## Зачем именно это

Все остальные замеры отвечают на вопрос «похоже ли на слабый сигнал сегодня». Ни один
не отвечает на вопрос жюри — «а оно правда смотрит вперёд?». Отвечает только прогон на
состоянии мира двухлетней давности и сверка его верха с тем, что случилось потом.

## Порядок

Сначала сам прогон под срезом — это `measure_ask.py`, никакого второго конвейера:

    python scripts/measure_ask.py --domains --cached
        --as-of 2024-09-15 --out reports/bt-2024-09-15

Потом этот скрипт считает, что стало с каждым направлением ПОСЛЕ среза, и сравнивает
верх выдачи с остальным пулом того же прогона.

И отдельно — главное число защиты: сколько строк списка методологов, составленного в
СЕНТЯБРЕ 2026, находит прогон на состоянии 2024 года:

    python scripts/judge_match.py --dir reports/bt-2024-09-15

## Чем меряется «взлетело»

- **Прирост работ в корпусе**: след ядра на срезе и на горизонте `HORIZON_YEARS` после
  него, оба — здесь же тем же `weak.core` на ТЕКУЩЕМ корпусе. ⚠️ Не наступить: след из
  карточки (`core_works`) для «на срезе» не годится — он посчитан на корпусе тех дней, и
  после докачки прирост читался бы как рост. Оба конца считаются одним набором срезов
  openalex — теми, чей край в БД не позже среза (`weak.core.SLICES`): CS и Engineering
  начинаются с 2023-01, и под срезом 2022 их включение выглядело бы ростом ×N.
- **Новости после среза**: явное окно `after:срез before:горизонт` (`when:2y` совпало
  бы с ним только у среза ровно двухлетней давности).

⚠️ **Сравнивать верх можно только с ОДНИМ И ТЕМ ЖЕ пулом**: контроль — это кандидаты
того же прогона, не попавшие в ТОП (снятые по жанру и оставшиеся с одним игроком). Два
верх-k из разных прогонов несравнимы.

⚠️ **Правило зачёта, записанное ДО прогона**: предсказательная сила показана, если
верх выдачи растёт заметно чаще контроля — по медиане прироста работ И по доле
направлений, о которых после среза вышли новости. Совпадение медиан — это «не
показано», и так и надо предъявлять.

⚠️ **Под срезом научная половина ТОНКАЯ, и это надо знать заранее.** На 2024-09-15 из
3.86 млн работ корпуса в поиске остаются около 0.4 млн: 3.09 млн вышли после среза, ещё
0.7 млн — строки без содержания. То есть бэктест проверяет прежде всего ДЕНЕЖНЫЙ поток,
который отыгрывается назад полностью, а научный — в меру того, что было опубликовано и
векторизовано к той дате.

⚠️ **Чего бэктест не доказывает**: срез беднее настоящего прошлого. Источники без
параметра даты (Хабр, КиберЛенинка) отдают сегодняшнюю страницу, и лишнее с неё
отбрасывается уже после выдачи (`weak.asof`). То есть это НИЖНЯЯ оценка того, что
детектор нашёл бы в 2024 году живьём.

    python scripts/backtest_signals.py --dir reports/bt-2024-09-15
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.weak import asof  # noqa: E402
from pelican.weak import core as core_mod  # noqa: E402
from pelican.weak.core import footprints  # noqa: E402
from pelican.weak.live import news_many  # noqa: E402
from pelican.weak.stats import span  # noqa: E402

#: Во сколько раз должен вырасти след, чтобы считать это ростом, а не шевелением.
#: ⚠️ Догадка: порога, замеренного на росте научных рядов, у нас нет ([todo](../docs/todo.md) §59).
GROWTH = 2.0
#: Горизонт исхода: столько лет после среза считается «потом». Один и тот же у всех
#: срезов, иначе срез 2022 мерил бы рост за четыре года, а 2024 — за два, и их модели
#: несравнимы (rolling-origin evaluation). Два года — как у первого бэктеста; в годах, а
#: не в днях, чтобы горизонт был календарной датой (730 дней через високосный год — 14-е).
HORIZON_YEARS = 2


def slices_at(store: Store, cut: date) -> frozenset[str]:
    """Срезы openalex, у которых край в БД не позже среза: только они есть на обоих концах.

    CS и Engineering собраны с 2023-01, и под срезом 2022 их нет вовсе — а через два года
    их 8 млн строк. Считать прирост с ними значит мерить включение источника, не рост.
    """
    rows = store.query(
        "SELECT field, min(observed_at)::date "
        "FROM works WHERE source = 'openalex' GROUP BY 1"
    )
    # Дата через `quack_query` может приехать строкой — сравнение по ISO-тексту.
    return frozenset(
        str(field) for field, edge in rows if field and str(edge)[:10] <= cut.isoformat()
    )


def runs(folder: Path) -> list[tuple[str, dict]]:
    out = []
    for path in sorted(folder.glob("ask-cards-*.json")):
        out.append(
            (
                path.stem.removeprefix("ask-cards-").replace("-", " "),
                json.loads(path.read_text(encoding="utf-8")),
            )
        )
    return out


def as_of_of(folder: Path, given: str | None) -> date:
    """Дата среза: из аргумента или из имени папки `backtest-YYYY-MM-DD`."""
    if given:
        return date.fromisoformat(given)
    tail = folder.name.rsplit("-", 3)[-3:]
    return date.fromisoformat("-".join(tail))


#: Сколько случайных разбиений считать полом. ⚠️ Двести — компромисс: 95-й перцентиль
#: на них устойчив во втором знаке, а прогон остаётся мгновенным.
SHUFFLES = 200


def _separation(
    top: list[float], control: list[float], folder: Path, extra: dict | None = None
) -> None:
    """Делит ли прирост следа верх выдачи и контроль — и делит ли лучше случайности.

    Считается вероятность, что случайное направление из ТОП выросло сильнее случайного
    из контроля: это статистика **Манна–Уитни**, она же AUC, и она не зависит от выбросов,
    в отличие от среднего. 0.50 — «ось не делит».

    ⚠️ **Пол случайности считается на ТЕХ ЖЕ числах**: тот же пул разбивается наугад в тех
    же пропорциях, и берётся 95-й перцентиль. Без этого 0.62 не с чем сравнить — на малых
    выборках случайное разбиение само по себе даёт 0.55–0.58.
    """
    if not top or not control:
        return

    def auc(a: list[float], b: list[float]) -> float:
        wins = sum(1 for x in a for y in b if x > y)
        ties = sum(1 for x in a for y in b if x == y)
        return (wins + 0.5 * ties) / (len(a) * len(b))

    got = auc(top, control)
    pool = [*top, *control]
    rnd = random.Random(20260916)
    floors = []
    for _ in range(SHUFFLES):
        rnd.shuffle(pool)
        floors.append(auc(pool[: len(top)], pool[len(top) :]))
    floors.sort()
    ceiling = floors[int(0.95 * len(floors))]
    print("")
    print(f"=== РАЗДЕЛЕНИЕ по приросту следа (AUC, n={len(top)}/{len(control)}) ===")
    print(f"  ТОП против контроля: {got:.2f}")
    middle = floors[len(floors) // 2]
    print(f"  пол случайности: медиана {middle:.2f}, 95-й перцентиль {ceiling:.2f}")
    print(
        "  вывод: "
        + ("разделение есть, но читать его надо вместе с полом" if got > ceiling
           else "⚠️ в пределах случайности — предсказательная сила НЕ показана")
    )
    # Сводка для отчёта сдачи: он собирается из чисел замера, а не из пересказа.
    (folder / "backtest.json").write_text(
        json.dumps(
            {
                "auc": round(got, 2),
                "пол": round(ceiling, 2),
                "топ": len(top),
                "контроль": len(control),
                **(extra or {}),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="что стало с направлениями после среза")
    ap.add_argument("--dir", required=True, help="папка прогона под срезом")
    ap.add_argument("--as-of", help="дата среза, если её нет в имени папки")
    ap.add_argument(
        "--horizon-years",
        type=int,
        default=HORIZON_YEARS,
        help="сколько лет после среза считается исходом (в будущем — до сегодня)",
    )
    args = ap.parse_args()

    folder = REPO / args.dir
    cut = as_of_of(folder, args.as_of)
    horizon = cut.replace(year=cut.year + args.horizon_years)
    if horizon >= date.today():
        horizon = None  # «после» — сегодняшний корпус
    found = runs(folder)
    if not found:
        print(f"нет файлов ask-cards-*.json в {folder}: сначала прогон measure_ask --as-of")
        return

    # Верх выдачи и контроль — из ОДНОГО пула: иначе сравнивать нечего.
    everyone: list[tuple[str, str, dict]] = []
    for domain, run in found:
        everyone += [("ТОП", domain, s) for s in run.get("signals", [])]
        everyone += [
            ("контроль", domain, s) for s in run.get("excluded", []) + run.get("thin", [])
        ]
    tops = sum(1 for where, _d, _s in everyone if where == "ТОП")
    print(f"срез {cut}: в ТОП {tops} направлений, в контроле {len(everyone) - tops}")

    cores = sorted({str(s.get("core") or s.get("label") or "") for _w, _d, s in everyone if s})
    with Store(settings.storage_target) as store:
        # Оба конца — ОДНИМ набором срезов корпуса и на ОДНОМ (текущем) корпусе: след из
        # карточки считался на корпусе тех дней и после докачки занижен (`weak.core.SLICES`).
        core_mod.SLICES = slices_at(store, cut)
        print(f"срезы openalex с краем ≤ {cut}: {', '.join(sorted(core_mod.SLICES)) or '—'}")
        print(f"след {len(cores)} ядер на срезе {cut} (это минуты)...", flush=True)
        asof.AS_OF = cut
        at_cut = footprints(store, cores)
        print(f"след тех же ядер на {horizon or 'сегодня'}...", flush=True)
        asof.AS_OF = horizon
        now_print = footprints(store, cores)
    asof.AS_OF = None

    labels = [str(s.get("label") or "") for _w, _d, s in everyone]
    # Новости строго в окне после среза: `when:2y` совпало бы с ним только у среза
    # двухлетней давности, а у более старого показало бы сегодняшние два года.
    window = f"after:{cut.isoformat()} before:{(horizon or date.today()).isoformat()}"
    print(f"новости {window} по {len(labels)} направлениям...", flush=True)
    fresh = news_many(labels, "en", window=window)

    rows = []
    for (where, domain, sig), news in zip(everyone, fresh, strict=True):
        core = str(sig.get("core") or sig.get("label") or "")
        before = int(at_cut[core].works) if core in at_cut else 0
        after = int(now_print[core].works) if core in now_print else before
        grew = after - before
        ratio = (after / before) if before else (GROWTH if after else 0.0)
        rows.append(
            {
                "домен": domain,
                "где": where,
                "направление": str(sig.get("label") or ""),
                "ядро": core,
                "работ на срезе": before,
                "работ на срезе (карточка)": int(sig.get("core_works") or 0),
                "работ после": after,
                "прирост": grew,
                "во сколько раз": round(ratio, 2),
                "новостей после среза": len(news),
            }
        )

    out = folder / "backtest.tsv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    print("")
    for name in ("ТОП", "контроль"):
        here = [r for r in rows if r["где"] == name]
        if not here:
            continue
        ratios = [r["во сколько раз"] for r in here]
        grown = sum(1 for r in here if r["во сколько раз"] >= GROWTH)
        with_news = sum(1 for r in here if r["новостей после среза"] > 0)
        print(f"=== {name} ({len(here)}) ===")
        print(f"  медиана прироста следа: ×{statistics.median(ratios):.2f}")
        print(
            f"  выросли в {GROWTH:g}+ раз: {grown} ({grown / len(here):.0%}) "
            f"{span(grown, len(here))}"
        )
        print(
            f"  с новостями после среза: {with_news} ({with_news / len(here):.0%}) "
            f"{span(with_news, len(here))}"
        )
    _separation(
        [float(r["во сколько раз"]) for r in rows if r["где"] == "ТОП"],
        [float(r["во сколько раз"]) for r in rows if r["где"] == "контроль"],
        folder,
        {
            "срез": cut.isoformat(),
            "горизонт": (horizon or date.today()).isoformat(),
            "срезы openalex": sorted(core_mod.SLICES or ()),
        },
    )
    print("")
    print(f"выписано: {out}")
    print("⚠️ главное число защиты считается отдельно — покрытие списка сентября 2026:")
    print(f"   python scripts/judge_match.py --dir {args.dir}")


if __name__ == "__main__":
    main()
