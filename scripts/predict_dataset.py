"""Датасет организаторов на входе → CSV предсказаний на выходе (часть 1 ТЗ).

    python scripts/predict_dataset.py scripts/weak_signals_100.tsv
    python scripts/predict_dataset.py data/closed.csv --out reports/predictions-closed.csv
    python scripts/predict_dataset.py data/closed.json --limit 10      # отладка

Тот же конвейер, что замерялся (`weak/dataset.py`): привязка названия к корпусу →
перевод → новости → след ядра и ярлыка → оценка моделью по свидетельствам
(`weak/assess.py`) по ТРЁМ составам (плечи `papers` / `mixed` / `corpus`) → сведение
голосов `--vote`. По умолчанию `any`: сигнал, если хоть одно плечо увидело сигнал —
self-consistency (Wang et al. 2022), вариант any-yes. Замер (`reports/weak-model-zoo.md`):
одно плечо `corpus` пропускает 8 строк из 100, any-yes — 2, ложных на лёгком контроле у
обоих 0. Ни один обученный классификатор поверх голосов этого не превзошёл — они выучивают
то же правило. ⚠️ Цена any-yes в точности на трудных случаях не измерена (§90); `--vote
corpus` возвращает одно плечо, чей recall 0.92 и есть число `weak-eval.md`.

## Формат входа — угадывается по заголовку и печатается

Формат закрытого датасета неизвестен. Берётся TSV/CSV (разделитель по расширению и
сниффером) или JSON (список объектов). Колонка названия ищется среди `NAME_COLUMNS`,
домен — среди `DOMAIN_COLUMNS`; что взято, печатается первой строкой, чтобы ошибка
угадывания была видна до прогона, а не в CSV.

## Что в выходе и чего там нет

`is_weak_signal` — жанр модели равен «зарождающаяся технология»; `kind`/`stage` — на
языке словаря `weak.kinds` / `weak.rubric` и по-русски; `confidence` — доля проверок,
считаемых без компаний (научная опора, разные типы источников, разные издатели), та же
семантика, что у карточки `trends ask`; `probability` — обученная модель
(`weak/model.py`), пусто, пока правило её обучения не выполнено; `why` — объяснение
модели со ссылками на номера свидетельств; `sources` — сами свидетельства с датой,
издателем и доверенностью.

⚠️ Балла (стадия + тренд) в выходе НЕТ: тренд не бьёт константу ни одной из трёх
проверенных осей (docs/todo.md §81, §83), и сумма из стадии и шума выдавала бы шум за оценку.
"""

from __future__ import annotations

import argparse
import csv
import io as _io
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.weak import model as model_mod  # noqa: E402
from pelican.weak.dataset import (  # noqa: E402
    ARMS,
    EMBED_FEATURES,
    Row,
    assess_many,
    candidate,
    embed_features,
    features,
    gather,
    judgment_features,
    llm_features,
)
from pelican.weak.kinds import EMERGING, label_of  # noqa: E402
from pelican.weak.rubric import STAGE_LABELS, parse_stage  # noqa: E402
from pelican.weak.stats import span  # noqa: E402
from pelican.weak.trust import LEVEL_LABELS, SCIENCE  # noqa: E402

ARM = "corpus"
NAME_COLUMNS = ("tech", "technology", "name", "title", "Технология", "Название", "название")
DOMAIN_COLUMNS = ("domain", "Домен", "Отрасль", "Направление", "область")
STAGE_COLUMNS = ("stage", "Стадия", "Стадия развития")
ID_COLUMNS = ("n", "id", "№", "index")
#: Сколько работ, называющих ядро в тексте, считается научной опорой (как в `trends ask`).
NAMED_WORKS = 2


def read_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = next((v for v in payload.values() if isinstance(v, list)), [])
        return [dict(r) for r in payload]
    if path.suffix.lower() == ".tsv":
        delimiter = "\t"
    else:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


def pick(columns: list[str], wanted: tuple[str, ...]) -> str | None:
    lowered = {c.lower().strip(): c for c in columns}
    for w in wanted:
        if w.lower() in lowered:
            return lowered[w.lower()]
    return None


def checks_for(row: Row) -> list[tuple[str, bool, str]]:
    """Проверки уверенности, считаемые без списка компаний (семантика — `weak.ask`)."""
    needle = row.core.lower()
    named = [p for p in row.papers if needle and needle in p.text.lower()]
    kinds = {d.trust.kind for d in row.news}
    if row.papers:
        kinds.add(SCIENCE)
    publishers = {d.publisher or d.domain for d in row.news if d.publisher or d.domain}
    return [
        (
            "научная опора",
            len(named) >= NAMED_WORKS,
            f"работ, называющих «{row.core}»: {len(named)}",
        ),
        ("разные типы источников", len(kinds) >= 2, "типы: " + (", ".join(sorted(kinds)) or "—")),
        ("разные издатели", len(publishers) >= 2, f"разных издателей: {len(publishers)}"),
    ]


def sources_line(row: Row) -> str:
    parts = []
    for d in row.news:
        t = d.trust
        parts.append(
            f"{d.title} — {d.publisher or d.domain}, {d.published or 'дата н/д'}, "
            f"{t.kind}, доверенность {LEVEL_LABELS[t.level]}, {d.url}"
        )
    for p in row.papers:
        parts.append(f"{p.text[:120]} — работа корпуса, {p.date}, {SCIENCE}, доверенность высокая")
    return " || ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="предсказания по датасету технологий")
    ap.add_argument("path", help="TSV / CSV / JSON с колонкой названия технологии")
    ap.add_argument("--out", help="куда писать CSV (по умолчанию reports/predictions-<имя>.csv)")
    ap.add_argument("--limit", type=int, default=0, help="первые N строк — отладка")
    ap.add_argument(
        "--vote",
        choices=("any", "corpus", "majority"),
        default="any",
        help="как сводить три оценки по плечам: any — сигнал, если хоть одно плечо "
        "сказало сигнал (замер: +6 из 8 пропусков, ложных 0); corpus — одно плечо; "
        "majority — двое из трёх",
    )
    args = ap.parse_args()

    src = Path(args.path)
    raw = read_rows(src)
    if not raw:
        print("во входе нет строк")
        raise SystemExit(1)
    columns = list(raw[0])
    name_col = pick(columns, NAME_COLUMNS)
    if name_col is None:
        print(f"не нашёл колонку названия среди {columns}; ожидал одну из {NAME_COLUMNS}")
        raise SystemExit(1)
    domain_col = pick(columns, DOMAIN_COLUMNS)
    stage_col = pick(columns, STAGE_COLUMNS)
    id_col = pick(columns, ID_COLUMNS)
    print(
        f"вход {src.name}: {len(raw)} строк; название ← «{name_col}», "
        f"домен ← {('«' + domain_col + '»') if domain_col else 'нет'}, "
        f"id ← {('«' + id_col + '»') if id_col else 'номер строки'}"
    )
    if args.limit:
        raw = raw[: args.limit]
    # Строка без названия предсказания не получит и в CSV не идёт: считается вслух.
    blank = sum(1 for r in raw if not str(r.get(name_col, "")).strip())
    if blank:
        print(f"  строк без названия пропущено: {blank}")
    raw = [r for r in raw if str(r.get(name_col, "")).strip()]

    rows = [
        Row(
            set="in",
            n=str(r.get(id_col) or i) if id_col else str(i),
            tech=str(r[name_col]).strip(),
        )
        for i, r in enumerate(raw, 1)
    ]

    def say(msg: str) -> None:
        print(f"  {msg}", flush=True)

    params = model_mod.load()
    # Соседство в векторах (E) — ещё один проход по шардам, и только если оно в модели.
    need_e = params is not None and any(f in params["features"] for f in EMBED_FEATURES)
    with Store(settings.storage_target) as store:
        print(f"{settings.llm_model} @ {settings.llm_base_url}")
        gather(store, rows, ARM, say=say, neighbours=need_e)
    # Победитель зоопарка может стоять на голосах трёх плеч (stacking по
    # self-consistency): тогда оценок три на строку, из ОДНОГО сбора. Иначе — одна.
    extra_arms = [
        arm
        for arm in ARMS
        if arm != ARM
        and (args.vote != "corpus" or (params is not None and f"llm_{arm}" in params["features"]))
    ]
    cands = [candidate(r, ARM) for r in rows]
    say(f"оценка {len(cands)} кандидатов моделью (плечо {ARM})...")
    got = assess_many(cands, say=say)
    votes: list[dict[str, tuple[str, str]]] = [{ARM: (a.kind, a.stage)} if a else {} for a in got]
    whys: dict[tuple[str, str], str] = {
        (r.n, ARM): a.why for r, a in zip(rows, got, strict=True) if a
    }
    for arm in extra_arms:
        say(f"оценка по плечу {arm} — для голосования «{args.vote}»...")
        more = assess_many([candidate(r, arm) for r in rows], say=say)
        for r, v, a in zip(rows, votes, more, strict=True):
            if a:
                v[arm] = (a.kind, a.stage)
                whys[(r.n, arm)] = a.why
    today = date.today()
    out_path = Path(args.out) if args.out else Path("reports") / f"predictions-{src.stem}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    head = [
        "n", "tech", "domain", "is_weak_signal", "kind", "kind_ru", "stage", "stage_ru",
        "confidence", "checks", "probability", "predictors", "votes", "why", "sources",
    ]  # fmt: skip
    n_signal = n_assessed = 0
    stage_hits = stage_total = 0
    kinds: dict[str, int] = {}
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for r, raw_row, a, v in zip(rows, raw, got, votes, strict=True):
            checks = checks_for(r)
            passed = sum(ok for _, ok, _ in checks)
            conf = passed / len(checks)
            prob = pred_words = ""
            if params is not None:
                # Признаки те же, что при обучении: числа, голоса, атомарные чтения из
                # ЭТОЙ оценки (плечо `corpus`) и соседство в векторах.
                feats = {
                    **features(r, today),
                    **llm_features(v),
                    **judgment_features(a),
                    **embed_features(r),
                }
                pred = model_mod.probability(feats, params)
                prob = f"{pred.probability:.3f}"
                pred_words = "; ".join(pred.predictors)
            kind = a.kind if a else ""
            stage = a.stage if a else ""
            # Сведение голосов (self-consistency, Wang et al. 2022): any-yes чинит 6 из 8
            # пропусков одного плеча без единого ложного на лёгком контроле
            # (reports/weak-model-zoo.md). ⚠️ Цена в точности на трудных случаях не
            # измерена (docs/todo.md §90) — потому режим переключаемый.
            yes = [arm for arm, (k, _st) in v.items() if k == EMERGING]
            no = [arm for arm, (k, _st) in v.items() if k and k != EMERGING]
            why = a.why if a else ""
            if v and args.vote != "corpus":
                final = bool(yes) if args.vote == "any" else len(yes) >= 2
                if final and kind != EMERGING:
                    # Объяснение и стадия — от плеча, которое сигнал и увидело.
                    kind, stage = EMERGING, v[yes[0]][1]
                    why = whys.get((r.n, yes[0]), "")
                elif not final and kind == EMERGING:
                    kind, stage = v[no[0]]
                    why = whys.get((r.n, no[0]), "")
            if a:
                n_assessed += 1
                kinds[kind] = kinds.get(kind, 0) + 1
                n_signal += kind == EMERGING
                expected = parse_stage(str(raw_row.get(stage_col, ""))) if stage_col else ""
                if expected:
                    stage_total += 1
                    stage_hits += expected == stage
            w.writerow(
                [
                    r.n,
                    r.tech,
                    str(raw_row.get(domain_col, "")) if domain_col else "",
                    "" if not a else int(kind == EMERGING),
                    kind,
                    label_of(kind) if kind else "оценка не получена",
                    stage,
                    STAGE_LABELS.get(stage, stage),
                    f"{conf:.2f}",
                    "; ".join(f"{name}: {'да' if ok else 'нет'} ({d})" for name, ok, d in checks),
                    prob,
                    pred_words,
                    "; ".join(f"{arm}: {label_of(k)}" for arm, (k, _st) in v.items()),
                    why,
                    sources_line(r),
                ]
            )

    print(f"\nCSV: {out_path}")
    print(
        f"оценено {n_assessed} из {len(rows)}; слабый сигнал: {n_signal} "
        f"({n_signal / max(n_assessed, 1):.0%}) {span(n_signal, max(n_assessed, 1))}"
    )
    print(
        "по жанрам:", ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
    )
    if stage_total:
        print(
            f"стадия совпала с колонкой «{stage_col}»: {stage_hits} из {stage_total} "
            f"({stage_hits / stage_total:.0%})"
        )
    if params is None:
        print(
            "вероятность модели не печатается: weak/model.json нет или правило обучения провалено"
        )


if __name__ == "__main__":
    main()
