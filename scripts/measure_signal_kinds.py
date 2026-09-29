"""Замер 3: разделяет ли жанр зрелости, и совпадает ли стадия со шкалой заказчика.

    python scripts/measure_signal_kinds.py
    python scripts/measure_signal_kinds.py --limit 10 --controls 6   # отладка

## Два набора, и зачем второй

- **100 положительных** — датасет организаторов (`scripts/weak_signals_100.tsv`).
  Все размечены методологами как слабые сигналы.
- **30 контрольных** (`scripts/weak_controls.tsv`) — зрелое, стандарты, слишком общее,
  хайп и шум, по шесть доменов датасета.

⚠️ Без контроля замер ничего не значит: «100% emerging» на одних положительных
получит и классификатор, который отвечает `emerging` на всё.

⚠️ **Контрольный набор размечен НАМИ, а не заказчиком.** Он мерит отделимость от
нашего представления о зрелом и шумном, не от закрытой разметки организаторов, и так
это и надо предъявлять. Формулировки написаны по-русски в той же манере, что
положительные, — иначе классы разделились бы по языку и стилю, а не по сути.

## Что меряется

1. **Жанр:** доля `emerging` среди положительных (полнота) и среди контроля (ложные
   срабатывания), плюс матрица «ожидали → получили» по контролю.
2. **Стадия** против поля «Стадия развития» методолога (`weak.rubric.parse_stage`):
   точное совпадение и совпадение с точностью до ступени, матрица путаницы.
3. **Тренд** против поля «Тренд упоминаний» и **балл целиком** против колонки «Балл».
   Тренд в выдаче считает не модель, а правило по датам свидетельств
   (`ask.FAST_RECENT_SHARE` / `ask.FAST_MIN_NEWS`), и здесь оно повторяется тем же
   вызовом. ⚠️ Классы несбалансированны (60 «Растёт» против 36 «Растёт быстро»), и
   сравнивается правило с долей БОЛЬШИНСТВА, а не с 50%: константа даёт 63%, и правило,
   её не бьющее, неинформативно.
   ⚠️ Новостей для тренда берётся столько же, сколько в `trends ask` (`NEWS_PER_CARD`),
   а не `MIXED_NEWS`: на составе плеча порог «новостей ≥ 3» достижим ровно впритык, и
   замер мерил бы состав плеча, а не само правило.

⚠️ **Предел различимости считается ДО прогона.** Доля на 100 положительных при
0.80 — это ±0.078; на 30 контрольных при 0.10 ложных — ±0.11. Разницу между правками
промпта меньше этого предъявлять нельзя.

⚠️ Модели не показываются ни стадия методолога, ни его обоснование — только название
и привязанные работы (`weak.assess`).
"""

from __future__ import annotations

import argparse
import collections
import csv
import io as _io
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pelican.config import settings  # noqa: E402
from pelican.store import Store  # noqa: E402
from pelican.weak.ask import NEWS_PER_CARD, _age_share  # noqa: E402
from pelican.weak.assess import (  # noqa: E402
    JUDGMENT_NAMES,
    NEWS,
    Evidence,
)
from pelican.weak.core import cores  # noqa: E402
from pelican.weak.dataset import (  # noqa: E402
    ARMS,
    EMBED_FEATURES,
    FEATURE_NAMES,
    JUDGMENT_FEATURES,
    LLM_FEATURES,
    MIXED_PAPERS,
    Row,
    assess_many,
    candidate,
    embed_features,
    features,
    gather,
    llm_features,
)
from pelican.weak.kinds import EMERGING, KINDS  # noqa: E402
from pelican.weak.rubric import (  # noqa: E402
    FAST,
    GROWING,
    STAGE_RANK,
    STAGES,
    TRENDS,
    parse_stage,
    parse_trend,
    score,
)
from pelican.weak.stats import span  # noqa: E402
from pelican.weak.translate import translate  # noqa: E402

HERE = Path(__file__).resolve().parent
POSITIVES = HERE / "weak_signals_100.tsv"
CONTROLS = HERE / "weak_controls.tsv"
OUT = Path("reports/weak-assess.tsv")

# Состав плеч (`ARM_EVIDENCE`, `MIXED_NEWS`, `MIXED_PAPERS`) живёт в `weak.dataset`:
# он общий с обучением и предсказанием, и здесь только импортируется.


def read_tsv(path: Path) -> list[dict]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t"))


def write_features(drows: list[Row], assess_path: Path) -> Path:
    """Признаки строк в TSV для `scripts/model_zoo.py` / `train_signal_model.py`.

    Голоса LLM по ВСЕМ трём плечам (`llm_*`, `weak.dataset.llm_features`) подтягиваются из
    готовых TSV оценок того же контроля: обучение без них и с ними считается на одном
    файле, а оценка на модели ради признаков не гоняется заново (`--no-assess`).
    Отсутствующее плечо — пустая ячейка, не ноль.

    Атомарные чтения (`J`, `weak.dataset.JUDGMENT_FEATURES`) берутся из TSV оценки ЭТОГО
    плеча — того, что видит `trends ask`; соседство в векторах (`E`) — из строк, если
    считалось (`--embed-features`).
    """
    votes: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}
    judged: dict[tuple[str, str], dict[str, float]] = {}
    found_arms = []
    for arm in ARMS:
        arm_path = assess_path.with_name(
            assess_path.name.replace(f"-{args_arm(assess_path)}", f"-{arm}")
        )
        if not arm_path.exists():
            continue
        found_arms.append(arm)
        for r in read_tsv(arm_path):
            key = (r["set"], str(r["n"]))
            votes.setdefault(key, {})[arm] = (r["kind"], r["stage"])
            if arm_path == assess_path:
                judged[key] = {
                    k: float(r[k]) for k in JUDGMENT_NAMES if r.get(k, "") not in ("", None)
                }
    out = assess_path.with_name(assess_path.name.replace("weak-assess", "weak-features"))
    head = ["set", "n", "tech", "expected_kind", "expected_stage"]
    head += [*FEATURE_NAMES, *LLM_FEATURES, *JUDGMENT_FEATURES, *EMBED_FEATURES]
    today = date.today()
    missing = 0
    unjudged = 0

    def cell(v: float | None) -> str:
        return "" if v is None else f"{v:.4f}"

    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(head)
        for row in drows:
            feats = features(row, today)
            got = votes.get((row.set, row.n), {})
            if not got:
                missing += 1
            llm = llm_features(got)
            j = judged.get((row.set, row.n), {})
            if not j:
                unjudged += 1
            e = embed_features(row)
            w.writerow(
                [row.set, row.n, row.tech, row.expected_kind, row.expected_stage]
                + [cell(feats[k]) for k in FEATURE_NAMES]
                + [cell(llm[k]) for k in LLM_FEATURES]
                + [cell(j.get(k)) for k in JUDGMENT_FEATURES]
                + [cell(e[k]) for k in EMBED_FEATURES]
            )
    note = f"; без оценки модели {missing}" if missing else ""
    if unjudged:
        note += f"; без атомарных чтений {unjudged} (оценка старого формата — перегнать плечо)"
    with_e = sum(1 for r in drows if r.nbr is not None)
    note += f"; с соседством в векторах {with_e}"
    print(f"признаки: {out} ({len(drows)} строк; плечи с голосами: {found_arms}{note})", flush=True)
    return out


def args_arm(assess_path: Path) -> str:
    """Плечо из имени TSV оценки: `weak-assess-<плечо>[-<контроль>].tsv`."""
    stem = assess_path.stem.removeprefix("weak-assess-")
    return stem.split("-")[0]


#: Правило тренда «свежесть свидетельств», ИЗ ПРОДУКТА УБРАННОЕ.
#:
#: ⚠️ Значения живут здесь, а не в `weak.ask`: правило проиграло константе-большинству
#: (§81), и вместе с осью тренда ушло из выдачи — импорт из `ask` ломал этот скрипт
#: целиком. Оставлено здесь нарочно: это базовая линия, которую обязана побить любая
#: новая ось тренда, и без неё «патентная ось даёт 60%» не с чем сравнить.
FAST_RECENT_SHARE = 0.6
FAST_MIN_NEWS = 3

#: Сетка порогов оси «скорость ядра»: доля работ ядра за последние 12 месяцев.
SPEED_GRID = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50)

#: Сетка порогов патентной оси: во сколько раз выросла патентная активность за два года
#: против предыдущих двух.
PATENT_GRID = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


def _patent_axis(rows: list, core_names: list[str]) -> None:
    """Тренд по ПАТЕНТНОЙ активности: третья проверенная ось.

    Две предыдущие проиграли константе-большинству (§81): свежесть свидетельств не делит
    по построению — мы и ищем свежее, — а скорость ядра говорит о ядре, а не о
    направлении. Эта ось считается по самому направлению и в чужих данных: публикации за
    два года против предыдущих двух (`weak.patents`).

    Приём назван и не выдуман: патентная активность по годам как фаза жизненного цикла —
    Haupt, Kloyer, Lange, *Research Policy* (2007); это же компонента **growth** у
    Porter и др., *TFSC* (2019). Заказчик описывает зрелость ровно так (цикл Гартнера,
    «количество патентов по годам»).

    ⚠️ Порог подбирается на одной половине строк, а засчитывается на другой (чёт/нечет по
    номеру), и сравнение идёт с долей БОЛЬШИНСТВА на зачётной половине, а не с 50%.
    ⚠️ Пустая база (ноль в предыдущем окне) — это не бесконечный рост, но и не «нет
    роста»: такие строки считаются отдельно и в подбор порога не идут.
    """
    from pelican.weak import patents

    if not patents.enabled():
        print("")
        print("⚠️ патентная ось: ключей EPO OPS нет — замер не проводился")
        return

    pairs = [
        (r, core.strip())
        for (which, r), core in zip(rows, core_names, strict=True)
        if which == "pos" and parse_trend(r.get("trend", "")) and core.strip()
    ]
    print("")
    print(f"патентная активность по {len(pairs)} ядрам (по два запроса на ядро)...", flush=True)
    acts = patents.activity(sorted({c for _r, c in pairs}), with_docs=False)

    data, no_base, unmeasured = [], 0, 0
    for r, core in pairs:
        act = acts.get(core)
        if act is None or not act.measured:
            unmeasured += 1
            continue
        if not act.before:
            no_base += 1
            continue
        number = int(r["n"]) if str(r["n"]).isdigit() else len(data)
        data.append((number, parse_trend(r["trend"]), act.recent / act.before))

    print("")
    print(f"=== ТРЕНД по ПАТЕНТНОЙ АКТИВНОСТИ (годных строк {len(data)}) ===")
    print(f"без базы в предыдущем окне: {no_base} · измерить нечем: {unmeasured}")
    if len(data) < 20:
        # ⚠️ Предел различимости считается ДО выводов: на такой выборке любые два
        # правила неотличимы ни друг от друга, ни от монеты.
        print("⚠️ строк меньше двадцати — замер не считается")
        return

    tune = [d for d in data if d[0] % 2 == 1]
    hold = [d for d in data if d[0] % 2 == 0]

    def hits(sample: list, threshold: float) -> int:
        return sum(
            1
            for _n, expected, ratio in sample
            if (FAST if ratio >= threshold else GROWING) == expected
        )

    best = max(PATENT_GRID, key=lambda t: hits(tune, t))
    got = hits(hold, best)
    majority = max(sum(1 for _n, e, _s in hold if e == t) for t in TRENDS)
    print(f"подбор на {len(tune)}, зачёт на {len(hold)}; порог роста: ×{best:.2f}")
    print(f"точно на зачётной половине: {got / len(hold):.0%} {span(got, len(hold))}")
    print(f"⚠️ константа-большинство там же: {majority / len(hold):.0%}")
    if got <= majority:
        print("⚠️ ось не бьёт константу — в выдачу она НЕ идёт (та же судьба, что у двух прошлых)")
    else:
        print(f"ось бьёт константу: порог ×{best:.2f} можно ставить в выдачу")


def _speed_axis(rows: list, core_prints: list) -> None:
    """Тренд по СКОРОСТИ ядра вместо свежести свидетельств.

    Нынешнее правило (`ask.FAST_RECENT_SHARE`) меряет свежесть найденного, а свежо у нас
    всё: мы и ищем свежее, поэтому ось не делит (59% при константе 64%). Здесь ось другая
    — какая доля работ ядра вышла за последние 12 месяцев (`core.Footprint`): у зрелого
    ядра она мала, у зарождающегося велика.

    ⚠️ **Порог подбирается на одной половине строк, а засчитывается на другой** (чёт/нечет
    по номеру): выбрать лучший порог и на нём же отчитаться — подгонка, а не замер.
    ⚠️ Сравнение — с долей БОЛЬШИНСТВА на зачётной половине, а не с 50%.
    """
    data = []
    for (which, r), fp in zip(rows, core_prints, strict=True):
        expected = parse_trend(r.get("trend", "")) if which == "pos" else None
        if not expected or not fp.works:
            continue
        number = int(r["n"]) if str(r["n"]).isdigit() else len(data)
        data.append((number, expected, fp.last_year_works / fp.works))
    if len(data) < 20:
        print("")
        print(f"⚠️ ось скорости: строк со следом всего {len(data)} — замер не считается")
        return

    tune = [d for d in data if d[0] % 2 == 1]
    hold = [d for d in data if d[0] % 2 == 0]

    def hits(sample: list, threshold: float) -> int:
        return sum(
            1
            for _n, expected, share in sample
            if (FAST if share >= threshold else GROWING) == expected
        )

    best = max(SPEED_GRID, key=lambda t: hits(tune, t))
    got = hits(hold, best)
    majority = max(sum(1 for _n, e, _s in hold if e == t) for t in TRENDS)
    print("")
    print(f"=== ТРЕНД по СКОРОСТИ ЯДРА (подбор на {len(tune)}, зачёт на {len(hold)}) ===")
    print(f"порог доли работ за год: {best:.2f}")
    print(f"точно на зачётной половине: {got / len(hold):.0%} {span(got, len(hold))}")
    print(f"⚠️ константа-большинство там же: {majority / len(hold):.0%}")
    if got <= majority:
        print("⚠️ ось не бьёт константу — тренд и балл из выдачи убираются (план, п. 3)")
    else:
        print(f"ось бьёт константу: ставим порог {best:.2f} в ask.FAST_LAST_YEAR_SHARE")


def main() -> None:
    ap = argparse.ArgumentParser(description="замер жанра зрелости и стадии")
    ap.add_argument("--limit", type=int, default=0, help="первые N положительных")
    ap.add_argument("--controls", type=int, default=0, help="первые N контрольных")
    ap.add_argument(
        "--controls-file",
        default=str(CONTROLS),
        help="файл контроля; трудный набор — scripts/weak_controls_hard.tsv",
    )
    ap.add_argument(
        "--evidence",
        choices=("papers", "mixed", "corpus"),
        default="papers",
        help="papers — только работы; mixed — работы и новости; corpus — mixed + след корпуса",
    )
    ap.add_argument(
        "--features-out",
        action="store_true",
        help="записать признаки строк в reports/weak-features-<плечо>.tsv (weak.dataset)",
    )
    ap.add_argument(
        "--no-assess",
        action="store_true",
        help="с --features-out: только признаки, жанр модели взять из готового TSV оценки",
    )
    ap.add_argument(
        "--embed-features",
        action="store_true",
        help="с --features-out: считать и соседство в векторах корпуса (E, weak.neighbours) "
        "— проход по всем шардам; векторы названий уходят в reports/weak-label-vectors.npz",
    )
    ap.add_argument(
        "--patents-only",
        action="store_true",
        help="только патентная ось: перевод, ядра, OPS — без привязки, новостей и оценки",
    )
    args = ap.parse_args()
    out_path = OUT.with_name(f"weak-assess-{args.evidence}.tsv")
    if Path(args.controls_file) != CONTROLS:
        # ⚠️ Трудный контроль пишется в СВОЙ файл и объявляется отдельным числом:
        # усреднять его с лёгким нельзя, иначе честная цифра растворяется.
        out_path = OUT.with_name(f"weak-assess-{args.evidence}-{Path(args.controls_file).stem}.tsv")

    pos = read_tsv(POSITIVES)
    controls_path = Path(args.controls_file)
    ctl = read_tsv(controls_path)
    if args.limit:
        pos = pos[: args.limit]
    if args.controls:
        ctl = ctl[: args.controls]
    rows = [("pos", r) for r in pos] + [("ctl", r) for r in ctl]

    if args.patents_only:
        # ⚠️ Ось не зависит от оценки: ей нужны ядра и OPS. Гонять ради неё час
        # оценки на модели незачем — и TSV здесь не пишется, чтобы частичный прогон
        # не выдал себя за полный (§73 уже наступали).
        names = [r["tech"] for _, r in rows]
        print(f"перевод {len(names)} названий и ядра для патентной оси...", flush=True)
        core_names = cores([tr.en for tr in translate(names)])
        _patent_axis(rows, list(core_names))
        return

    store = Store(settings.storage_target)
    t0 = time.time()
    print(
        f"привязка {len(rows)} названий ({len(pos)} положительных, "
        f"{len(ctl)} контрольных из {controls_path.name})..."
    )
    # Сбор свидетельств — общий с обучением и предсказанием (`weak.dataset`): число
    # замера обязано относиться к тому же составу, что идёт в `predict_dataset.py`.
    drows = [
        Row(
            set=which,
            n=str(r["n"]),
            tech=r["tech"],
            expected_kind=EMERGING if which == "pos" else r["expected"],
            expected_stage=(parse_stage(r["stage"]) or "") if which == "pos" else "",
        )
        for which, r in rows
    ]
    coverage = gather(
        store,
        drows,
        args.evidence,
        say=lambda m: print(f"  {m}", flush=True),
        neighbours=args.embed_features,
    )
    if coverage is not None:
        print(
            f"  покрытие векторами: последнее окно {coverage.last:.0%}, предыдущее "
            f"{coverage.prev:.0%}, всё до {coverage.asof} {coverage.before:.0%} — признаки E "
            f"считаются по тем векторам, что есть",
            flush=True,
        )
    papers = [d.papers for d in drows]
    # Новости для ТРЕНДА — до `NEWS_PER_CARD`, как в `trends ask`. ⚠️ Не `MIXED_NEWS`:
    # плечо держит ровно три новости, а порог правила — «новостей ≥ 3», и на составе
    # плеча он был бы достижим ровно в одном случае из возможных, то есть мерил бы
    # плечо, а не правило.
    trend_news: list[list[Evidence]] = [
        [
            Evidence(-1, d.published, d.title, NEWS, d.url, d.publisher)
            for d in row.news[:NEWS_PER_CARD]
        ]
        for row in drows
    ]
    candidates = [candidate(row, args.evidence) for row in drows]
    core_prints: list = []
    core_names_for_axis: list[str] = []
    if args.evidence == "corpus":
        # Следы нужны не только промпту: по ним считается ось тренда «скорость ядра».
        core_prints = [row.core_print for row in drows]
        core_names_for_axis = [row.core for row in drows]
        print(f"  след корпуса показан всем {len(candidates)} кандидатам", flush=True)

    if args.features_out and args.embed_features:
        # Векторы названий — для контрольной линии «embedding probe» в зоопарке.
        import numpy as np

        from pelican.weak.ground import embed_texts

        # Имя — по TSV оценки: у трудного контроля свой файл, иначе он затирал бы основной.
        vec_path = out_path.with_name(out_path.name.replace("weak-assess", "weak-label-vectors"))
        vec_path = vec_path.with_suffix(".npz")
        np.savez(
            vec_path,
            keys=np.array([f"{d.set}:{d.n}" for d in drows]),
            vectors=embed_texts([d.tech for d in drows]),
        )
        print(f"  векторы названий: {vec_path}", flush=True)
    if args.features_out and args.no_assess:
        # Признаки без оценки: голоса и атомарные чтения — из готовых TSV.
        write_features(drows, out_path)
        return
    print(f"  готово за {time.time() - t0:.0f} с; оценка через {settings.llm_model}...", flush=True)

    t0 = time.time()
    got = assess_many(candidates, say=lambda m: print(f"  {m}", flush=True))
    print(f"  готово за {time.time() - t0:.0f} с")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(
            ["set", "n", "tech", "expected_kind", "kind", "expected_stage", "stage", "why"]
            + list(JUDGMENT_NAMES)
        )
        for (which, r), a in zip(rows, got, strict=True):
            expected_stage = parse_stage(r["stage"]) if which == "pos" else ""
            expected_kind = EMERGING if which == "pos" else r["expected"]
            judged = a.judgments if a else {}
            w.writerow(
                [
                    which,
                    r["n"],
                    r["tech"],
                    expected_kind,
                    a.kind if a else "",
                    expected_stage or "",
                    a.stage if a else "",
                    a.why if a else "",
                ]
                + ["" if judged.get(k) is None else f"{judged[k]:g}" for k in JUDGMENT_NAMES]
            )
    print(f"TSV: {out_path} (плечо {args.evidence})")
    if args.features_out:
        # После оценки: атомарные чтения только что легли в TSV этого плеча.
        write_features(drows, out_path)

    # ---------------------------------------------------------------- жанр
    pos_got = [a for (w, _), a in zip(rows, got, strict=True) if w == "pos" and a]
    ctl_pairs = [(r, a) for (w, r), a in zip(rows, got, strict=True) if w == "ctl" and a]
    failed = sum(a is None for a in got)

    recall = sum(a.kind == EMERGING for a in pos_got) / max(len(pos_got), 1)
    fp = sum(a.kind == EMERGING for _, a in ctl_pairs) / max(len(ctl_pairs), 1)
    print(f"\n=== ЖАНР (отказов модели {failed}) ===")
    n_pos, n_ctl = len(pos_got), len(ctl_pairs)
    tp_pos = sum(a.kind == EMERGING for a in pos_got)
    fp_ctl = sum(a.kind == EMERGING for _, a in ctl_pairs)
    print(f"положительные → emerging: {recall:.0%} {span(tp_pos, n_pos)}  (n={n_pos})")
    print(f"контроль → emerging (ложные): {fp:.0%} {span(fp_ctl, n_ctl)}  (n={n_ctl})")
    print("положительные по жанрам:", collections.Counter(a.kind for a in pos_got).most_common())

    matrix = collections.Counter((r["expected"], a.kind) for r, a in ctl_pairs)
    print("\nконтроль, ожидали → получили:")
    for exp in KINDS:
        line = {k: matrix[(exp, k)] for k in KINDS if matrix[(exp, k)]}
        if line:
            print(f"  {exp:10s} → {line}")

    if pos_got or ctl_pairs:
        tp = sum(a.kind == EMERGING for a in pos_got)
        fpn = sum(a.kind == EMERGING for _, a in ctl_pairs)
        fn = len(pos_got) - tp
        precision = tp / max(tp + fpn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        print(
            f"\nPrecision {precision:.2f} · Recall {recall:.2f} · F1 {f1:.2f} "
            f"(TP {tp}, FP {fpn}, FN {fn}) — ⚠️ precision зависит от ДОЛИ контроля (100:30),"
            " а не только от модели"
        )

    # ---------------------------------------------------------------- стадия
    pairs = [
        (parse_stage(r["stage"]), a.stage)
        for (w, r), a in zip(rows, got, strict=True)
        if w == "pos" and a and parse_stage(r["stage"])
    ]
    if pairs:
        exact = sum(e == g for e, g in pairs) / len(pairs)
        near = sum(abs(STAGE_RANK[e] - STAGE_RANK[g]) <= 1 for e, g in pairs) / len(pairs)
        bias = sum(STAGE_RANK[g] - STAGE_RANK[e] for e, g in pairs) / len(pairs)
        print(f"\n=== СТАДИЯ против методолога (n={len(pairs)}) ===")
        hits = sum(e == g for e, g in pairs)
        print(f"точно: {exact:.0%} {span(hits, len(pairs))} · в пределах ступени: {near:.0%}")
        print(f"смещение (модель − методолог, в ступенях): {bias:+.2f}")
        conf = collections.Counter(pairs)
        head = "методолог \\ модель"
        print(f"  {head:24s}" + "".join(f"{s[:10]:>12s}" for s in STAGES))
        for e in STAGES:
            print(f"  {e:24s}" + "".join(f"{conf[(e, g)]:>12d}" for g in STAGES))

    # ------------------------------------------------------- тренд и балл целиком
    # Тренд в выдаче считает не модель, а правило по датам свидетельств
    # (`ask.FAST_RECENT_SHARE` / `ask.FAST_MIN_NEWS`), поэтому здесь оно повторяется
    # тем же вызовом, а не переписывается.
    today = date.today()
    trend_pairs = []
    score_pairs = []
    for (which, r), a, fresh, work in zip(rows, got, trend_news, papers, strict=True):
        if which != "pos" or not a:
            continue
        expected = parse_trend(r.get("trend", ""))
        if not expected:
            continue
        dated = [e.date for e in fresh] + [e.date for e in work[:MIXED_PAPERS]]
        recent = _age_share(dated, today)
        market = len(fresh)
        trend = FAST if recent >= FAST_RECENT_SHARE and market >= FAST_MIN_NEWS else GROWING
        trend_pairs.append((expected, trend))
        stage = parse_stage(r["stage"])
        if stage and a.stage:
            try:
                score_pairs.append((int(r["score"]), score(a.stage, trend)))
            except (ValueError, KeyError):
                continue

    if trend_pairs:
        hits = sum(e == g for e, g in trend_pairs)
        n = len(trend_pairs)
        majority = max(sum(1 for e, _ in trend_pairs if e == t) for t in TRENDS)
        print("")
        print(f"=== ТРЕНД против методолога (n={n}) ===")
        print(f"точно: {hits / n:.0%} {span(hits, n)}")
        # ⚠️ Классы несбалансированны, и сравнивать надо с долей БОЛЬШИНСТВА, а не с 50%:
        # константа «Растёт» на этом датасете даёт 63%.
        print(f"⚠️ константа-большинство: {majority / n:.0%} — правило обязано бить её")
        if hits <= majority:
            print("⚠️ правило не бьёт большинство: тренд неинформативен, и в балл он не идёт")
        conf_t = collections.Counter(trend_pairs)
        head = "методолог | правило"
        print(f"  {head:24s}" + "".join(f"{t[:14]:>16s}" for t in TRENDS))
        for e in TRENDS:
            print(f"  {e:24s}" + "".join(f"{conf_t[(e, g)]:>16d}" for g in TRENDS))
        if args.evidence == "papers":
            print("⚠️ плечо papers: новостей нет вовсе, правило вырождено в «Растёт» — не мера")

    if core_prints:
        _speed_axis(rows, core_prints)
        _patent_axis(rows, core_names_for_axis)

    if score_pairs:
        n = len(score_pairs)
        exact = sum(e == g for e, g in score_pairs)
        near = sum(abs(e - g) <= 1 for e, g in score_pairs)
        mae = sum(abs(e - g) for e, g in score_pairs) / n
        print("")
        print(f"=== БАЛЛ (стадия + тренд) против колонки методолога (n={n}) ===")
        print(f"точно: {exact / n:.0%} {span(exact, n)} · ±1: {near / n:.0%} · MAE {mae:.2f}")

    # Сводка для отчёта сдачи: он собирается из чисел замера, а не из пересказа.
    # ⚠️ Тренд и балл в TSV не попадают (там только жанр и стадия), поэтому без этого
    # файла отчёт про них молчит — а молчание читается как «не мерили».
    if trend_pairs:
        hits = sum(e == g for e, g in trend_pairs)
        majority = max(sum(1 for e, _ in trend_pairs if e == t) for t in TRENDS)
        summary = {
            "плечо": args.evidence,
            "тренд": {"точно": hits, "всего": len(trend_pairs), "константа": majority},
        }
        if score_pairs:
            summary["балл"] = {
                "точно": sum(e == g for e, g in score_pairs),
                "в пределах единицы": sum(abs(e - g) <= 1 for e, g in score_pairs),
                "всего": len(score_pairs),
                "mae": round(sum(abs(e - g) for e, g in score_pairs) / len(score_pairs), 2),
            }
        out_json = out_path.with_suffix(".json")
        out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"сводка тренда и балла: {out_json}")


if __name__ == "__main__":
    main()
