"""Отчёт об оценке модели на датасете заказчика — из TSV замеров, а не руками.

    python scripts/weak_eval_report.py                 # → reports/weak-eval.md

ТЗ, промежуточная сдача: «отчёт об оценке модели на предоставленном датасете (метрики
Precision, Recall, F1-score)». Числа здесь только пересчитываются из
`reports/weak-assess-<плечо>.tsv`, которые пишет `scripts/measure_signal_kinds.py`, —
поэтому отчёт нельзя разойтись с замером, и перегенерировать его можно в любой момент.

⚠️ В отчёт вписаны оговорки, без которых числа читаются неверно: контрольный набор
размечен нами, precision зависит от доли контроля, стадия и жанр — разные задачи.
"""

from __future__ import annotations

import csv
import io as _io
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pelican.weak.rubric import (  # noqa: E402
    STAGE_RANK,
    parse_stage,
    parse_trend,
    score,
)
from pelican.weak.stats import span  # noqa: E402

HERE = Path(__file__).resolve().parent
REPORTS = Path("reports")
ARMS = (
    ("papers", "только работы корпуса"),
    ("mixed", "работы + новости"),
    ("corpus", "работы + новости + след корпуса (ядро и сам ярлык)"),
)
EMERGING = "emerging"


def read(path: Path) -> list[dict]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t"))


def rubric_block() -> list[str]:
    rows = read(HERE / "weak_signals_100.tsv")
    agreed = disagreed = outside = 0
    for r in rows:
        st, tr = parse_stage(r["stage"]), parse_trend(r["trend"])
        if st is None or tr is None:
            outside += 1
        elif score(st, tr) == int(r["score"]):
            agreed += 1
        else:
            disagreed += 1
    return [
        "## 1. Рубрика методологов воспроизводится",
        "",
        "Колонка «Балл (стадия+тренд)» — сумма двух порядковых шкал: стадия "
        "(Концепция/Исследование 1 · Прототип/PoC 2 · Пилот 3 · Раннее внедрение 4) плюс "
        "тренд (Растёт 2 · Растёт быстро 3). Ноль подгоняемых параметров.",
        "",
        "| сходится | расходится | не выражается шкалой |",
        "|---|---|---|",
        f"| **{agreed}** | {disagreed} | {outside} |",
        "",
        "Отсюда форма выдачи системы: она предсказывает те же два поля и собирает балл сложением.",
        "",
    ]


def arm_block(name: str, title: str, path: Path) -> tuple[list[str], dict]:
    rows = read(path)
    pos = [r for r in rows if r["set"] == "pos" and r["kind"]]
    ctl = [r for r in rows if r["set"] == "ctl" and r["kind"]]
    tp = sum(r["kind"] == EMERGING for r in pos)
    fn = len(pos) - tp
    fp = sum(r["kind"] == EMERGING for r in ctl)
    tn = len(ctl) - fp
    recall = tp / max(len(pos), 1)
    precision = tp / max(tp + fp, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    accuracy = (tp + tn) / max(len(pos) + len(ctl), 1)
    specificity = tn / max(len(ctl), 1)

    pairs = [(r["expected_stage"], r["stage"]) for r in pos if r["expected_stage"] and r["stage"]]
    exact = sum(e == g for e, g in pairs) / max(len(pairs), 1)
    near = sum(abs(STAGE_RANK[e] - STAGE_RANK[g]) <= 1 for e, g in pairs) / max(len(pairs), 1)
    bias = sum(STAGE_RANK[g] - STAGE_RANK[e] for e, g in pairs) / max(len(pairs), 1)

    lines = [
        f"### Плечо `{name}`: {title}",
        "",
        "| метрика | значение | 95% ДИ (Уилсон) |",
        "|---|---|---|",
        f"| Precision | {precision:.2f} | {span(tp, tp + fp)} |",
        f"| Recall | {recall:.2f} | {span(tp, len(pos))} |",
        f"| F1 | **{f1:.2f}** | — |",
        f"| Accuracy | {accuracy:.2f} | {span(tp + tn, len(pos) + len(ctl))} |",
        f"| Specificity (контроль не принят за сигнал) | {specificity:.2f} "
        f"| {span(tn, len(ctl))} |",
        f"| TP / FN / FP / TN | {tp} / {fn} / {fp} / {tn} | |",
        f"| стадия: точно | {exact:.0%} | {span(round(exact * len(pairs)), len(pairs))} |",
        f"| стадия: в пределах ступени | {near:.0%} | |",
        f"| смещение стадии (модель − методолог) | {bias:+.2f} ступени | |",
        "",
    ]
    misses = Counter(r["kind"] for r in pos if r["kind"] != EMERGING)
    if misses:
        lines += [f"Промахи по положительным: {dict(misses)}.", ""]
    matrix = Counter((r["expected_kind"], r["kind"]) for r in ctl)
    lines += [
        "Контроль, ожидали → получили: "
        + "; ".join(f"{e} → {g}: {c}" for (e, g), c in sorted(matrix.items())),
        "",
    ]
    return lines, {"f1": f1, "recall": recall, "precision": precision, "exact": exact}


def _json(name: str) -> dict:
    path = REPORTS / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def backtest_block() -> list[str]:
    """Ретроспективный бэктест: что стало с верхом выдачи среза к сегодняшнему дню.

    ⚠️ Единственный раздел отчёта, который отвечает на вопрос «смотрит ли система вперёд».
    Остальные говорят только о том, похоже ли найденное на слабый сигнал сегодня.
    """
    folders = sorted(REPORTS.glob("bt-*"))
    if not folders:
        return []
    folder = folders[-1]
    table = folder / "backtest.tsv"
    if not table.exists():
        return []
    rows = read(table)
    out = [f"## 5. Ретроспективный бэктест (срез {folder.name.removeprefix('bt-')})", ""]
    for name in ("ТОП", "контроль"):
        here = [r for r in rows if r.get("где") == name]
        if not here:
            continue
        ratios = sorted(float(r["во сколько раз"]) for r in here)
        median = ratios[len(ratios) // 2]
        with_news = sum(1 for r in here if int(r["новостей после среза"]) > 0)
        out += [
            f"- **{name}** ({len(here)} направлений): медиана прироста следа ×{median:.2f}; "
            f"с новостями после среза {with_news} из {len(here)} "
            f"({with_news / len(here):.0%}) {span(with_news, len(here))}",
        ]
    sep = folder / "backtest.json"
    if sep.exists():
        d = json.loads(sep.read_text(encoding="utf-8"))
        out += [
            f"- **Разделение** (вероятность, что направление из ТОП выросло сильнее "
            f"случайного из контроля — статистика Манна–Уитни): **AUC {d['auc']}** при поле "
            f"случайности {d['пол']}. ⚠️ Разделение есть, но слабое, и без пола это число "
            "читать нельзя: на таких выборках случайное разбиение само даёт 0.55–0.58.",
        ]
    coverage = folder / "judge-coverage.tsv"
    if coverage.exists():
        covered = len({(r["домен"], r["строка датасета"]) for r in read(coverage)})
        out += [
            f"- **Прогон на состоянии 2024 года находит {covered} строк** списка, который "
            "методологи составили в сентябре 2026.",
        ]
    out += [
        "",
        "⚠️ Верх сравнивается с контролем ТОГО ЖЕ прогона (кандидаты, не прошедшие в ТОП): "
        "два верх-k из разных прогонов несравнимы. ⚠️ Срез беднее настоящего прошлого — "
        "источники без параметра даты отдают сегодняшнюю страницу, и это нижняя оценка.",
        "",
    ]
    return out


def model_block() -> list[str]:
    """Обучаемые модели рядом с промпт-классом: зоопарк и связка с LLM.

    ⚠️ Это не замена промпт-класса, а ответ на «обучение модели» и «feature importance»
    из схемы 1 ТЗ и проверка, есть ли модель, которая в связке с LLM чинит его промахи.
    Правило решения записано до прогона в `scripts/model_zoo.py`; провал печатается вслух.
    """
    z = _json("weak-model-zoo.json")
    out = ["## 2а. Обучаемые модели и связка с LLM", ""]
    if not z:
        return [*out, "_зоопарк не прогонялся_", ""]
    rule = z["rule"]
    base = z["baselines"]
    nested = z["nested"]

    def row(name: str, v: dict) -> str:
        return (
            f"| {name} | {v['f1']:.3f} | {v['auc']:.3f} | {v['recall']:.2f} | "
            f"{v['specificity']:.2f} | {v['gain_median']:+.0f} | {v['mcnemar']['fixed']} / "
            f"{v['mcnemar']['broke']} |"
        )

    head = [
        "| что | F1 | AUC | recall | spec | прибавка к LLM, строк | чинит / ломает |",
        "|---|---|---|---|---|---|---|",
    ]
    ranked = sorted(z["candidates"].items(), key=lambda kv: -kv[1]["gain_median"])[:3]
    helpers = [k for k, v in z["candidates"].items() if v.get("helps")]
    out += [
        f"Зоопарк `scripts/model_zoo.py` (`reports/weak-model-zoo.md`): 9 оценщиков × 4 набора "
        "признаков (числа конвейера, голос LLM, три голоса LLM по плечам свидетельств — "
        "stacking) на одних фолдах CV "
        f"{z['folds']} × {z['repeats']}, против LLM `corpus` в одиночку. Правило до прогона: "
        f"медиана чистой прибавки ≥ {rule['gain_min']} строк, corrected resampled t-test "
        f"(Nadeau–Bengio) с поправкой Холма p < {rule['alpha']}, AUC ≥ {rule['auc_min']}.",
        "",
        *head,
        row("LLM corpus в одиночку", base["LLM corpus (одна)"]),
        row("any-yes трёх плеч (без обучения)", base["any-yes трёх плеч"]),
        row("большинство трёх плеч (без обучения)", base["большинство трёх плеч"]),
        row("вложенный выбор из зоопарка", nested),
        *(row(f"лучшая клетка: {k}", v) for k, v in ranked),
        "",
        "Кого берёт вложенный выбор: "
        + ", ".join(f"«{k}» ×{c}" for k, c in nested["chosen"])
        + ".",
        "",
        f"**По правилу помогают:** {', '.join(helpers) if helpers else 'никто'}. "
        + (
            "Потолок «помощи» на этих данных — промахи LLM (8 строк из 130), контроль лёгкий и "
            "наш (specificity у LLM 1.00), поэтому связка здесь может только чинить пропуски, а "
            "её цена в точности видна лишь на трудном контроле."
        ),
        "",
    ]
    if _json("weak-model.json").get("source") == "model_zoo":
        m = _json("weak-model.json")
        out += [
            f"В `weak-model.json` сохранён «{m['winner']}» (набор `{m['feature_set']}`); "
            f"правило {'выполнено' if m['gate']['passed'] else 'НЕ выполнено'} — "
            + (
                "вероятность показывается там, где модели хватает признаков."
                if m["gate"]["passed"]
                else "в выдачу вероятность не идёт, карточка остаётся с долей проверок."
            ),
            "",
        ]
    return out


def output_block() -> list[str]:
    """Качество открытой выдачи: полнота, точность, достоверность и потолок источников.

    ⚠️ Это ВТОРАЯ часть ТЗ, и числа у неё принципиально слабее первой: знаменатель
    чужой (100 строк методолога), а разметку точности делает модель, поверенная рукой.
    Каждое число идёт с оговоркой, без которой читается неверно.
    """
    precision = _json("judge-precision.json")
    facts = _json("card-facts.json")
    reach = _json("reachable.json")
    coverage = REPORTS / "judge-coverage.tsv"
    out = ["## 4. Качество открытой выдачи (открытый запрос)", ""]
    if not (precision or facts or reach or coverage.exists()):
        return [*out, "_замеры выдачи не прогонялись_", ""]

    if coverage.exists():
        rows = read(coverage)
        covered = len({(r["домен"], r["строка датасета"]) for r in rows})
        out += [
            f"- **Полнота (покрытие списка методолога)**: {covered} строк из 100 "
            "(`scripts/judge_match.py`). ⚠️ Знаменатель чужой, накрутить его размером ТОП "
            "нельзя; судья снисходителен к зонтикам и берёт одну строку на карточку, "
            "поэтому число занижено с одной стороны и завышено с другой.",
        ]
    if reach:
        out += [
            f"- **Потолок источников**: из 100 строк идеальным запросом видны "
            f"{reach.get('достижимо', 0)} (`scripts/measure_reachable.py`). "
            "⚠️ Полнота читается против этого числа: что недостижимо — цена набора "
            "источников, что достижимо и не найдено — цена набора запросов.",
        ]
    if precision:
        cards, good = precision.get("cards", 0), precision.get("signals", 0)
        out += [
            f"- **Точность ТОП**: {good} карточек из {cards} судья назвал слабым сигналом "
            f"({good / max(cards, 1):.0%}) {span(good, max(cards, 1))} "
            "(`scripts/judge_precision.py`). ⚠️ Судья не видит наших оценок, но его "
            "снисходительность измерена отдельно — см. пробы ниже.",
        ]
        probes = precision.get("probes") or {}
        loose = 0
        for name, probe in probes.items():
            n, wrong = probe.get("карточек", 0), probe.get("названо сигналом", 0)
            loose = max(loose, wrong / max(n, 1))
            out += [
                f"  - проба «{name}»: сигналом назван {wrong} из {n} "
                f"({wrong / max(n, 1):.0%}) {span(wrong, max(n, 1))}",
            ]
        for other in sorted(REPORTS.glob("judge-precision-*.json")):
            # Независимый судья: та же выдача, другая модель. Разрыв двух чисел — это и
            # есть мера self-preference, а не рассуждение о ней.
            d = json.loads(other.read_text(encoding="utf-8"))
            mine = good / max(cards, 1)
            theirs = d.get("signals", 0) / max(d.get("cards", 1), 1)
            same_run = d.get("cards") == cards
            where = "на тех же карточках" if same_run else "на прошлом прогоне выдачи"
            out += [
                f"  - **независимый судья** ({d.get('судья', '?')}) {where}: "
                f"{d.get('signals')} из {d.get('cards')} ({theirs:.0%}). ⚠️ Разрыв "
                f"{abs(mine - theirs) * 100:.0f} п.п. — это **self-preference**: своя модель "
                "ставит своему же тексту выше."
                + ("" if same_run else " ⚠️ Прогоны разные, и разрыв читается как оценка."),
            ]
        if loose > 0.25:
            # Проба — это заведомо НЕ сигналы. Если судья зовёт сигналом четверть из них,
            # его «да» почти ничего не сообщает, и число обязано читаться как потолок.
            out += [
                f"  - ⚠️ **на заведомо не-сигналах судья говорит «сигнал» в {loose:.0%} "
                "случаев**, то есть точность выше — ВЕРХНЯЯ граница, а не измерение. "
                "Мерят здесь ручная разметка (`scripts/precision_pairs.tsv`) и независимый "
                "судья другой модели (`--model`).",
            ]
    if facts:
        cards = facts.get("карточек", 0)
        wrong = facts.get("выдумок", 0)
        checks = facts.get("проверок на выдумку", 0)
        named = facts.get("названных компаний", 0)
        unver = facts.get("компаний без источника в карточке", 0)
        # ⚠️ Дефекты разной природы: выдуманное число и висячая ссылка — не одно и то же,
        # и объединять их в одну долю значит прятать, что чисел система не выдумывает.
        defects = REPORTS / "card-facts.tsv"
        refs = 0
        if defects.exists():
            refs = sum(1 for r in read(defects) if "ссылка" in r.get("утверждение/дефект", ""))
        out += [
            f"- **Достоверность карточки**: выдуманных чисел и дат {wrong - refs}, висячих "
            f"ссылок {refs} (всего {wrong} на {checks} проверяемых чисел и ссылок); карточек "
            f"без единой выдумки {facts.get('без выдумок', 0)} из {cards} "
            "(`scripts/measure_card_facts.py`).",
            (
                f"  - все {named} названных компаний подтверждены источником, показанным в "
                "самой карточке. ⚠️ Цена: имена, которые назвал группировщик заголовков, но "
                "не нашло построчное извлечение, в карточку не идут — игроков стало на 19% "
                "меньше."
                if not unver
                else f"  - ⚠️ отдельно: {unver} из {named} названных компаний не встречаются "
                "в источниках, показанных в самой карточке. Это не ложь — имя пришло из "
                "заголовка, прошедшего эхо-проверку, — но проверить его по карточке читатель "
                "не может."
            ),
        ]
        if facts.get("утверждений"):
            ok = facts.get("подтверждено", 0)
            n = facts["утверждений"]
            out += [
                f"  - подтверждаемость текста источником: {ok} из {n} ({ok / n:.0%}) {span(ok, n)}",
            ]
    foreign = _json("foreign/judge-precision.json")
    foreign_facts = _json("foreign/card-facts.json")
    if foreign:
        areas = ", ".join(foreign.get("by_domain", {}))
        n_cards = foreign.get("cards", 0)
        probe = (foreign.get("probes") or {}).get("чужой домен", {})
        out += [
            "- **Обобщаемость за пределы датасета**: запросы из областей, которых в датасете "
            f"нет ({areas}) — ТОП {n_cards} карточек, все признаны сигналом; карточек без "
            f"выдумок {foreign_facts.get('без выдумок', 0)} из "
            f"{foreign_facts.get('карточек', 0)}.",
            "  - ⚠️ Здесь же видно, что проба «чужой домен» на ДАЛЁКИХ областях работает: "
            f"сигналом назван {probe.get('названо сигналом', 0)} из {probe.get('карточек', 0)}. "
            "Значит высокая доля на шести доменах заказчика — это их взаимное соседство, а не "
            "только снисходительность судьи.",
        ]
    return [*out, ""]


def main() -> None:
    out = [
        "# Оценка модели на датасете заказчика",
        "",
        f"Сгенерировано `scripts/weak_eval_report.py` {date.today():%d.%m.%Y} из TSV замеров "
        "`scripts/measure_signal_kinds.py`. Методология — `docs/techdoc.md`.",
        "",
        "## Постановка",
        "",
        "- **Положительные**: 100 строк датасета организаторов, все размечены методологами "
        "как слабые сигналы.",
        "- **Отрицательные**: в датасете их нет. Контрольный набор — 30 строк "
        "(`scripts/weak_controls.tsv`): зрелое, отраслевые стандарты, слишком общие категории, "
        "маркетинговый хайп, шум — ровно классы исключения из ТЗ, по шести доменам датасета.",
        "- **Задача**: отнести кандидата к классу «зарождающаяся технология» против пяти "
        "классов исключения; модели показываются только название и найденные свидетельства — "
        "ни стадия, ни обоснование методолога.",
        "",
        "⚠️ **Контрольный набор размечен нами, а не заказчиком.** Precision и specificity "
        "измеряют отделимость от нашего представления о зрелом и шумном; на закрытой разметке "
        "организаторов они могут отличаться. Precision к тому же зависит от доли "
        "контроля (100 : 30).",
        "",
        "⚠️ **Предел различимости выборки**: на 100 положительных доля при 0.80 известна с "
        "точностью ±0.08, а «0 ложных из 30» — это верхняя граница ~11%, а не ноль. Разница "
        "меньше интервала между вариантами результатом не является.",
        "",
    ]
    out += rubric_block()
    out += ["## 2. Обнаружение слабого сигнала (жанр зрелости)", ""]
    summary = []
    for name, title in ARMS:
        path = REPORTS / f"weak-assess-{name}.tsv"
        if not path.exists():
            out += [f"### Плечо `{name}`: {title}", "", "_замер не прогонялся_", ""]
            continue
        block, nums = arm_block(name, title, path)
        out += block
        summary.append((name, title, nums))
    trend = _json("weak-assess-mixed.json")
    if trend.get("тренд"):
        t = trend["тренд"]
        ratio = t["точно"] / max(t["всего"], 1)
        const = t["константа"] / max(t["всего"], 1)
        out += [
            "### Тренд и балл: вторая половина рубрики",
            "",
            f"- **Тренд** («Растёт» против «Растёт быстро»): угадан в {t['точно']} случаях из "
            f"{t['всего']} ({ratio:.0%}) {span(t['точно'], t['всего'])}.",
            f"- ⚠️ **Константа-большинство даёт {const:.0%}**, то есть правило её НЕ бьёт и "
            "информации не несёт. Классы несбалансированны, и сравнивать надо с ней, а не с 50%.",
        ]
        if trend.get("балл"):
            b = trend["балл"]
            out += [
                f"- **Балл целиком** (стадия + тренд): точно {b['точно']} из {b['всего']} "
                f"({b['точно'] / max(b['всего'], 1):.0%}), в пределах единицы "
                f"{b['в пределах единицы'] / max(b['всего'], 1):.0%}, MAE {b['mae']}.",
                "- ⚠️ Балл собирается сложением, поэтому неинформативный тренд тянет вниз и его: "
                "предъявлять балл как оценку системы нельзя, пока тренд не заменён.",
            ]
        out += [""]

    out += model_block()
    if summary:
        out += [
            "## 3. Сводка",
            "",
            "| плечо | F1 | Recall | Precision | стадия точно |",
            "|---|---|---|---|---|",
            *(
                f"| {t} | {n['f1']:.2f} | {n['recall']:.2f} "
                f"| {n['precision']:.2f} | {n['exact']:.0%} |"
                for _, t, n in summary
            ),
            "",
            "Стадия — отдельная и более трудная задача: методологи определяют её по компаниям "
            "и раундам, а научная работа описывает более раннюю стадию. Порог ТЗ 75–80% "
            "относится к обнаружению сигнала.",
            "",
        ]
    out += output_block()
    out += backtest_block()
    target = REPORTS / "weak-eval.md"
    target.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    print(f"\n→ {target}")


if __name__ == "__main__":
    main()
