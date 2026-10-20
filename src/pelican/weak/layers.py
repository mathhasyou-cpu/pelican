"""Три слоя выдачи: уже тренд / слабый сигнал / зарождающееся.

Определение — от экспертов заказчика (консультация 16.10): «надо отделить зарождающиеся
технологии от слабых сигналов. Зарождающаяся может умереть. Слабый сигнал уже имеет
маленький тренд, патентный подтверждённый. Нужен слой между сильным сигналом, который уже
понятен, и совсем неподтверждённым». И о верхней границе: «Physical AI, embodied AI —
это уже всё понятно, они уже реализуются».

В литературе это же различение — «future sign» Hiltunen (2008): сигнал → проблема →
интерпретация, и слабый сигнал как первое ПОДТВЕРЖДЁННОЕ проявление изменения, а не любая
новинка; у Yoon (2012) — сигнал должен быть и в карте появления (KEM), и в карте
распространения (KIM), а не в одной из них.

- **уже тренд** — крупные игроки составляют не меньше половины названных компаний и
  продают (коммерческое внедрение), или свидетельства называют сложившихся лидеров рынка.
  ⚠️ Крупный игрок в ИССЛЕДОВАНИИ трендом не делает: «Attention is all you need» — Google
  Brain в 2017 году, и правило «есть Big Tech — значит тренд» убило бы ровно пример экспертов.
- **слабый сигнал** — не тренд и подтверждён хотя бы двумя НЕЗАВИСИМЫМИ путями: несколько
  работ корпуса (с препринтами) носят его название; патенты растут быстрее медианы пула;
  есть компания, пилот, деньги или стартап YC; есть исследовательский грант; есть
  клиническое испытание (`weak.extra`).
- **зарождающееся** — остальное: одна работа или одна группа, без патента и без
  компании. В ТОП не идёт, показывается блоком «наблюдать».

⚠️ Список крупных игроков, порог «двух путей», «половина компаний» и «рост патентов выше
медианы пула» — догадки, не
замер (docs/todo.md); правило зачёта — ретро-тест `scripts/retro_hits.py`
(pelican/docs/final-measure.md). Включается `WEAK_LAYERS` (`weak.flags`).
"""

from __future__ import annotations

from dataclasses import dataclass

TREND = "trend"
WEAK = "weak"
WATCH = "watch"

#: Крупные игроки: их продажа или вложение в направление значит, что оно уже понятно рынку.
#: ⚠️ Догадка: перечень по лидерам капитализации техсектора 2026, без замера.
BIG_PLAYERS = frozenset(
    name.lower()
    for name in (
        "OpenAI", "Anthropic", "Google", "Google Cloud", "Google DeepMind", "DeepMind",
        "Alphabet", "Microsoft", "Azure", "Amazon", "AWS", "Amazon Web Services", "Meta",
        "Apple", "NVIDIA", "Nvidia", "IBM", "Oracle", "Intel", "AMD", "Qualcomm", "Samsung",
        "Alibaba", "Tencent", "Baidu", "Huawei", "ByteDance", "Salesforce", "SAP", "Cisco",
        "Tesla", "Siemens", "Broadcom", "TSMC", "Visa", "Mastercard", "JPMorgan",
        "J.P. Morgan", "Goldman Sachs", "Pfizer", "Roche", "Novartis", "Johnson & Johnson",
        "Moderna", "BioNTech",
    )
)

#: Сколько независимых путей подтверждения нужно слабому сигналу. ⚠️ Догадка.
MIN_CONFIRMATIONS = 2
#: Работ корпуса с названием направления — «несколько работ». ⚠️ Догадка.
MIN_PAPERS = 2


@dataclass(frozen=True, slots=True)
class Layer:
    name: str
    reason: str
    confirmations: tuple[str, ...] = ()


def big_players(companies: list[str]) -> list[str]:
    return [c for c in companies if c.strip().lower() in BIG_PLAYERS]


def classify(
    companies: list[str],
    judgments: dict[str, float],
    works: int,
    patent_growth: float | None,
    extra: dict[str, int] | None = None,
    growth_floor: float = 1.0,
) -> Layer:
    """Слой направления по тому, что уже прочитано и посчитано; новых вызовов нет.

    `works` — след ЯРЛЫКА в корпусе (`Signal.label_works`), а не работы из выборки поиска:
    те — ~180 найденных, и у кандидатов денежного потока их ноль по построению.
    `patent_growth` — рост патентов «два года против двух до» (`patents.Activity.growth`);
    путь засчитывается, если рост выше `growth_floor` (медиана пула): объём патентов
    меряет общее ядро, а не технологию (2 577 за два года у «nested low-rank adaptation»).
    `extra` — находки дополнительных источников (`weak.extra`): препринты добавляются к
    работам; грант, клиническое испытание и стартап YC — свои пути подтверждения.
    """
    extra = extra or {}
    works += extra.get("preprint", 0)
    big = big_players(companies)
    if judgments.get("market_leaders_named"):
        return Layer(TREND, "свидетельства называют сложившихся лидеров рынка")
    # ⚠️ Крупный игрок делает трендом, только если он — не меньше половины названных
    # компаний и есть внедрение. Раунд — признак стартапа, то есть слабого сигнала: правило
    # «гигант назван + деньги» отправляло в тренд FHE-ускорители (Niobium, Zama и один
    # крупный в хвосте списка) и «quantum-driven optimization» (IonQ, Infleqtion, NVIDIA).
    if big and 2 * len(big) >= len(companies) and judgments.get("commercial_deployment"):
        return Layer(TREND, f"крупные игроки уже продают: {', '.join(big[:3])}")
    paths: list[str] = []
    if works >= MIN_PAPERS:
        paths.append(f"работ с этим названием в корпусе: {works}")
    if patent_growth is not None and patent_growth > growth_floor:
        paths.append(f"патенты растут: ×{patent_growth:.1f} за два года")
    market = [c for c in companies if c not in big]
    if market or judgments.get("pilot_mentioned") or judgments.get("funding_mentioned"):
        paths.append("компании: " + ", ".join(market[:3]) if market else "есть пилот или деньги")
    elif extra.get("yc"):
        paths.append(f"стартапов Y Combinator: {extra['yc']}")
    if extra.get("nih_reporter"):
        paths.append(f"исследовательских грантов: {extra['nih_reporter']}")
    if extra.get("clinicaltrials"):
        paths.append(f"клинических испытаний: {extra['clinicaltrials']}")
    if len(paths) >= MIN_CONFIRMATIONS:
        return Layer(WEAK, "подтверждено независимо: " + "; ".join(paths), tuple(paths))
    return Layer(
        WATCH,
        f"независимых подтверждений {len(paths)} из нужных {MIN_CONFIRMATIONS}: "
        + ("; ".join(paths) if paths else "ни работ, ни патентов, ни компаний"),
        tuple(paths),
    )
