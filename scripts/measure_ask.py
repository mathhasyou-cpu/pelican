"""Замер выдачи `trends ask`: попадание в домены датасета и разброс между прогонами.

Два режима, и оба нужны по разным причинам.

**`--domains`** — прогон по одному запросу на каждый домен датасета и сверка ТОП со
строками ЭТОГО ЖЕ домена. Совпадение считается двумя способами:

- **по компании** — у строки датасета есть колонка «Компании», и у нашего кандидата
  компании взяты из заголовков. Пересечение имён — точное сравнение строк, а не
  косинус, и потому проверяемо. ⚠️ Это НИЖНЯЯ оценка: одно и то же направление может
  быть найдено через других игроков.
- **глазами** — колонка `match` в `reports/ask-match-<домен>.tsv` заполняется рукой.
  ⚠️ Косинусом здесь мерить нельзя: «AI cybersecurity» и «IAM для ИИ-агентов» близки
  по вектору и разными сигналами от этого быть не перестают.

⚠️ **Подбор и проверка разведены** (docs/todo.md §65): правки подбираются на доменах
`TUNE`, засчитываются на отложенных `HOLDOUT`. Строки датасета модели не показываются
нигде — они только мерило.

**`--repeat`** — нулевой контроль: один запрос несколько раз ПОДРЯД С ВЫКЛЮЧЕННЫМ
кэшем. Пересечение ТОП между прогонами — та планка, ниже которой разница между двумя
версиями кода ничего не значит. Пересечение считается дважды: дословно и по
направлению (`ask.same_direction` — тем же сравнением, что «держится N из K» на
странице), потому что точное сравнение строк устойчивость занижает.

**`--tz`** — демо-надёжность: запросы из примеров ТЗ дословно плюс один далёкий домен
(`TZ_QUERIES`), по `--repeat` прогонов каждый, без кэша. Мерится то, что на демо
решает: секунды на запрос (потолок заказчика — `DEMO_LIMIT_S`), число позиций (ТЗ
требует пятнадцать) и разброс между прогонами. Каждый прогон ложится в таблицу `reports` (и копией в `reports/ask/`)
как `tz-<запрос>-<n>` — туда же, куда пишут `trends ask` и веб, и оттуда `weak.history`
берёт историю для пометки устойчивости; сводка — `reports/ask-tz.tsv`.

`--no-submarkets` выключает подрынки первого круга (`ask.SUBMARKETS`) — для A/B в одном
окне кэша: A = `--domains --cached --no-submarkets --out reports/ask-A`,
B = `--domains --cached --out reports/ask-B`, затем `judge_match.py --dir` на обеих.

`--with-listed` ставит модель списка (`weak/listed.json`) первым ключом порядка ТОП,
посчитанную на всём пуле (`ask.LISTED_IN_ASK`), — чтобы замерить её вживую против того же
пула без неё (`label_listed.py --dir`). Вместе с ней возвращаются её стадии (проход по шардам, денежный след и
маски окон первому запросу дня: 210–390 с, медиана 240).

Побочно печатается доля срабатывания каждой проверки уверенности: **проверка,
проходящая больше чем у 90% кандидатов, — не проверка**, и её надо заменить.

⚠️ Скрипт держит замок `model`, как и `trends ask`: параллельно с докачкой векторов
или ночным `trends llm` LM Studio держит одну модель, и время запроса завышается.

    python scripts/measure_ask.py --domains
    python scripts/measure_ask.py --repeat 3 --query "слабые сигналы в кибербезопасности"
    python scripts/measure_ask.py --tz --repeat 3
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
# ⚠️ Под перенаправленным выводом консоль Windows даёт cp1251, и первый же знак
# «⚠️» в сводке валит прогон целиком: час работы убивала собственная диагностика.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pelican import jobs, locks  # noqa: E402
from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.weak import ask as ask_mod  # noqa: E402
from pelican.weak import asof, cache, history, page  # noqa: E402
from pelican.weak.ask import (  # noqa: E402
    MONEY_STREAM,
    NEAR_LABEL,
    AskResult,
    Signal,
    _stems,
    topic_words,
)
from pelican.weak.ask import run as run_ask  # noqa: E402
from pelican.weak.core import significant  # noqa: E402
from pelican.weak.names import discriminating, document_frequency  # noqa: E402
from pelican.weak.tech import query_words  # noqa: E402

DATASET = REPO / "scripts" / "weak_signals_100.tsv"
OUT = REPO / "reports"

#: Запросы по доменам датасета. Формулировка пользовательская, как на демо жюри.
QUERIES = {
    "Защита ИИ": "слабые сигналы в безопасности ИИ",
    "Финтех": "слабые сигналы в финтехе",
    "Роботы": "слабые сигналы в робототехнике",
    "Edge": "слабые сигналы в периферийных вычислениях edge AI",
    "Инфраструктура ИИ": "слабые сигналы в инфраструктуре ИИ",
    "Индустриальный ИИ": "слабые сигналы в промышленном ИИ",
}
#: На чём подбираем и на чём засчитываем (§65): отложенная половина обязательна.
TUNE = ("Защита ИИ", "Финтех", "Роботы")
HOLDOUT = ("Edge", "Инфраструктура ИИ", "Индустриальный ИИ")
#: Проверка, срабатывающая чаще этого, ничего не различает.
CHECK_CEILING = 0.9
#: Запросы демо: три примера из ТЗ (§2, §7.2) дословно и один далёкий от датасета домен.
TZ_QUERIES = (
    "технологии в ИИ",
    "перспективные решения в финтехе",
    "слабые сигналы в области кибербезопасности",
    "космические технологии и новые материалы",
)
#: Потолок заказчика на запрос в реальном времени, секунд (docs/todo.md §93).
DEMO_LIMIT_S = 1200



def _say(message: str) -> None:
    """Ход прогона виден сразу: без сброса буфера лог пуст до самого конца.

    Время в строке — чтобы по логу было видно, КАКАЯ стадия съела запрос: итоговые
    секунды одни на всё, а потолок демо держит именно самая долгая стадия.
    """
    print(f"  · {datetime.now():%H:%M:%S} {message}", flush=True)


def _rows() -> list[dict[str, str]]:
    with DATASET.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


_DF: Counter[str] | None = None


def _dataset_df() -> Counter[str]:
    """Документная частота имени по всем 100 строкам датасета.

    Приём: **различительность токена по документной частоте** — тот же, которым в
    `mine` отсеиваются неразличительные слова. ⚠️ Без него подсказка «общая компания»
    срабатывает на «google» и связывает федеративное обучение с оркестрацией агентов.
    """
    global _DF
    if _DF is None:
        _DF = document_frequency(r.get("companies", "") for r in _rows())
    return _DF


def _auto_match(sig: Signal, rows: list[dict[str, str]]) -> list[str]:
    """Строки датасета, у которых есть ОБЩАЯ НАЗВАННАЯ КОМПАНИЯ с нашим кандидатом.

    ⚠️ Сравниваются различительные слова имени, а не имя целиком: «Keycard» у нас и
    «Keycard, Cyata, Fabrix Security» у методолога — это одно и то же, а «Pulse
    Security» и «Fabrix Security» — нет.
    """
    df = _dataset_df()
    ours = discriminating(", ".join(sig.companies), df)
    hits = []
    for r in rows:
        common = ours & discriminating(r.get("companies", ""), df)
        if common:
            hits.append(f"{r['tech'][:60]} ({', '.join(sorted(common))})")
    return hits


def _check_rates(result: AskResult) -> list[tuple[str, float, int]]:
    seen = [*result.signals, *result.excluded, *result.thin]
    counts: Counter[str] = Counter()
    for s in seen:
        for c in s.checks:
            counts[c.name] += int(c.passed)
    order = [c.name for c in seen[0].checks] if seen else []
    return [(name, counts[name] / len(seen), counts[name]) for name in order]


def _flaws(result: AskResult) -> tuple[int, int]:
    """(пар названий-близнецов в ТОП, названий не по теме).

    ⚠️ Оба дефекта считаются, а не оцениваются на глаз: «стало меньше дублей» без числа
    — это мнение. Порог близнецов тот же, что в `ask.NEAR_LABEL`.
    """
    labels = [s.label for s in result.signals]
    twins = 0
    for i, a in enumerate(labels):
        wa = _stems(a)
        for b in labels[i + 1 :]:
            wb = _stems(b)
            if wa and wb and len(wa & wb) / len(wa | wb) >= NEAR_LABEL:
                twins += 1
    topic = topic_words(query_words(result.query_en, result.area_en, *result.facets_en))
    off = sum(1 for lab in labels if not (set(significant(lab)) & topic))
    return twins, off


def _report(result: AskResult, domain: str, rows: list[dict[str, str]]) -> int:
    path = OUT / f"ask-match-{domain.replace(' ', '-')}.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    # ⚠️ Кроме названий выписывается ВЕСЬ прогон: точность ТОП судится по карточке со
    # свидетельствами (`scripts/judge_precision.py`), а по одному названию ни рыночную
    # категорию, ни чужую отрасль не отличить.
    cards = OUT / f"ask-cards-{domain.replace(' ', '-')}.json"
    cards.write_text(page.to_json(result), encoding="utf-8")
    matched = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["n", "label", "поток", "компании", "по компании", "match", "уверенность"])
        for i, s in enumerate(result.signals, 1):
            auto = _auto_match(s, rows)
            matched += bool(auto)
            w.writerow(
                [
                    i,
                    s.label,
                    s.stream,
                    ", ".join(s.companies[:6]),
                    " | ".join(auto),
                    "",
                    f"{s.confidence:.2f}",
                ]
            )
        w.writerow([])
        w.writerow(["--- строки датасета этого домена, для сверки глазами ---"])
        for r in rows:
            w.writerow(["", r["tech"][:90], "", r.get("companies", "")[:90]])
    print(f"  выписано: {path} · карточки: {cards}", flush=True)
    return matched


def _summary(result: AskResult) -> None:
    conf = {round(s.confidence, 2) for s in result.signals}
    players = [len(s.companies) for s in result.signals]
    money = sum(1 for s in result.signals if s.stream == MONEY_STREAM)
    print(
        f"  ТОП {len(result.signals)} · денежных {money} · шум "
        f"{len(result.excluded)} · за ТОП {len(result.thin)} · "
        f"снято категорий {len(result.categories)} · "
        f"кандидатов {result.candidates} · {result.seconds:.0f} с"
    )
    print(
        f"  различных значений уверенности в ТОП: {len(conf)} · "
        f"игроков на сигнал: медиана {sorted(players)[len(players) // 2] if players else 0}"
    )
    print(
        f"  игроков названо группировщиком, но не найдено в заголовках: "
        f"{result.players_unnamed}"
    )
    print(f"  снято проверкой тематичности: {result.off_topic}")
    twins, off = _flaws(result)
    print(f"  названий-близнецов в ТОП: {twins} · не по теме: {off}", flush=True)
    for name, rate, n in _check_rates(result):
        mark = "  ⚠️ не различает" if rate > CHECK_CEILING else ""
        print(f"    проверка «{name}»: проходит {rate:.0%} ({n}){mark}", flush=True)


def _overlaps(tops: list[list[str]]) -> None:
    """Попарное пересечение ТОП: дословно и по направлению."""
    print("\n-- пересечение ТОП между прогонами --", flush=True)
    for a in range(len(tops)):
        for b in range(a + 1, len(tops)):
            common = set(tops[a]) & set(tops[b])
            union = set(tops[a]) | set(tops[b])
            near = history.overlap(tops[a], tops[b])
            print(
                f"  {a + 1} ∩ {b + 1}: дословно {len(common)} "
                f"(Жаккар {len(common) / max(len(union), 1):.2f}), по направлению {near} "
                f"из {len(tops[a])}: " + ", ".join(sorted(common))
            )


def _slug(query: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in query.lower()).strip("-")[:40]


TZ_COLUMNS = [
    "запрос",
    "прогон",
    "секунд",
    "ТОП",
    "денежных",
    "исключено",
    "без игроков",
    "кандидатов",
    "источников",
    "уверенных",
    "прошлых прогонов",
    "устойчивых",
    "подрынков",
]


def _tz(store: Store, repeat: int) -> None:
    ask_dir = OUT / "ask"
    ask_dir.mkdir(parents=True, exist_ok=True)
    summary = OUT / "ask-tz.tsv"
    rows_out: list[list[object]] = []
    for query in TZ_QUERIES:
        tops: list[list[str]] = []
        for i in range(repeat):
            print(f"\n== «{query}», прогон {i + 1} из {repeat}", flush=True)
            result = run_ask(store, query, on_progress=_say)
            _summary(result)
            # История и отчёт — в той же таблице `reports`, что у стенда: прогрев демо
            # сразу виден в истории интерфейса и даёт карточкам «держится N из K».
            with jobs.connect(settings.database_url) as conn:
                past = [r["result"] for r in jobs.reports(conn, limit=500)]
                history.annotate(result, history.prior_runs(past, query, result.started))
                name = f"tz-{_slug(query)}-{datetime.now():%Y%m%d-%H%M%S}"
                html, payload = page.render(result), page.to_json(result)
                jobs.save_report(conn, name, query, datetime.now().astimezone(), payload, html)
            (ask_dir / f"{name}.html").write_text(html, encoding="utf-8")
            (ask_dir / f"{name}.json").write_text(payload, encoding="utf-8")
            tops.append([s.label for s in result.signals])
            print("  ТОП: " + " · ".join(tops[-1]), flush=True)
            rows_out.append(
                [
                    query,
                    i + 1,
                    round(result.seconds),
                    len(result.signals),
                    sum(1 for s in result.signals if s.stream == MONEY_STREAM),
                    len(result.excluded),
                    len(result.thin),
                    result.candidates,
                    result.sources_processed,
                    result.confident,
                    result.history_runs,
                    result.stable,
                    len(result.submarkets_en),
                ]
            )
            # Сводка переписывается после каждого прогона: оборванный ночной замер
            # обязан оставить после себя числа, а не пустой файл.
            with summary.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter="\t")
                w.writerow(TZ_COLUMNS)
                w.writerows(rows_out)
        if len(tops) > 1:
            _overlaps(tops)
    secs = sorted(r[2] for r in rows_out)
    sizes = sorted(r[3] for r in rows_out)
    print(
        f"\n-- итог {len(rows_out)} прогонов: секунд медиана {secs[len(secs) // 2]}, "
        f"максимум {secs[-1]} (потолок {DEMO_LIMIT_S}); ТОП медиана {sizes[len(sizes) // 2]}, "
        f"минимум {sizes[0]} · сводка {summary}",
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domains", action="store_true", help="по одному запросу на домен датасета")
    ap.add_argument("--only", help="только этот домен")
    ap.add_argument("--repeat", type=int, default=0, help="нулевой контроль: N прогонов подряд")
    ap.add_argument("--query", default="слабые сигналы в кибербезопасности")
    ap.add_argument("--cached", action="store_true", help="разрешить кэш живого поиска")
    ap.add_argument(
        "--as-of",
        help="срез времени YYYY-MM-DD: прогон на состоянии мира той даты (ретро-бэктест)",
    )
    ap.add_argument("--out", help="папка выдачи; по умолчанию reports/")
    ap.add_argument("--tz", action="store_true", help="запросы ТЗ: время, позиции, разброс")
    ap.add_argument(
        "--no-submarkets", action="store_true", help="без подрынков в первом круге (A/B)"
    )
    ap.add_argument(
        "--with-listed",
        action="store_true",
        help="считать модель списка (в выдаче она выключена: `ask.LISTED_IN_ASK`)",
    )
    args = ap.parse_args()
    if args.no_submarkets:
        ask_mod.SUBMARKETS = False
    if args.with_listed:
        ask_mod.LISTED_IN_ASK = True
        print("модель списка — ключ порядка на всём пуле; прогон — ещё ~310 с на домен")
    if args.as_of:
        # ⚠️ Ставится ДО первого обращения к источникам: срез — состояние процесса, и
        # выставленный позже он оставил бы часть прогона в настоящем (`weak.asof`).
        asof.AS_OF = date.fromisoformat(args.as_of)
        print(f"СРЕЗ {asof.AS_OF}: новости {asof.news_window()}, корпус обрезан по эту дату")
    global OUT
    if args.out:
        OUT = REPO / args.out
        OUT.mkdir(parents=True, exist_ok=True)

    rows = _rows()
    try:
        with locks.hold("model"), Store(settings.storage_target) as store:
            _main(args, rows, store)
    except locks.Busy as exc:
        print(f"занято: {exc}")
        sys.exit(75)


def _main(args: argparse.Namespace, rows: list[dict[str, str]], store: Store) -> None:
    if args.tz:
        cache.ENABLED = args.cached
        _tz(store, max(args.repeat, 1))
        return
    if args.domains:
        cache.ENABLED = args.cached
        total: dict[str, int] = {}
        for domain, query in QUERIES.items():
            if args.only and domain != args.only:
                continue
            print(f"\n== {domain}: «{query}»", flush=True)
            result = run_ask(store, query, on_progress=_say)
            _summary(result)
            total[domain] = _report(result, domain, [r for r in rows if r["domain"] == domain])
            print(
                f"  совпало по компании: {total[domain]} из {len(result.signals)}", flush=True
            )
        print("\n-- итог (совпадения по компании) --", flush=True)
        for group, name in ((TUNE, "подбор"), (HOLDOUT, "отложенные")):
            got = {d: total[d] for d in group if d in total}
            print(f"  {name}: всего {sum(got.values())} — {got}", flush=True)

    if args.repeat:
        # Два РАЗНЫХ пола шума, и путать их нельзя:
        #   без `--cached` — полный разброс, вместе с выдачей Google News;
        #   с `--cached` — разброс одной только модели на тех же источниках, и
        #     именно он сравним с замером `--domains`, который идёт по кэшу.
        cache.ENABLED = args.cached
        tops: list[list[str]] = []
        for i in range(args.repeat):
            print(f"\n== прогон {i + 1} из {args.repeat}: «{args.query}»", flush=True)
            result = run_ask(store, args.query, on_progress=_say)
            _summary(result)
            tops.append([s.label for s in result.signals])
            print("  ТОП: " + " · ".join(tops[-1]), flush=True)
        _overlaps(tops)


if __name__ == "__main__":
    main()
