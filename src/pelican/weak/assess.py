"""Оценка кандидата моделью: жанр зрелости и стадия на шкале заказчика.

Вход — название технологии и несколько РЕАЛЬНЫХ работ, привязанных к нему (заголовок,
начало аннотации, дата). Выход — три поля:

- `kind` — жанр зрелости из `weak.kinds` (что идёт в выдачу, что исключается);
- `stage` — ступень рубрики заказчика (`weak.rubric.STAGES`): концепция/исследование,
  прототип/PoC, пилот, раннее внедрение. Это ПОЛЕ ДАТАСЕТА, и предсказывается оно на
  его же шкале — тогда метрика считается на языке заказчика (docs/weak-signals.md);
- `why` — одна фраза по-русски: на чём основан вывод. Требование ТЗ «показывать, по
  каким именно признакам наблюдение отнесено к слабому сигналу».

Свидетельства четырёх типов: PAPER (работа корпуса), NEWS (новость — ось денег), CODE
(репозиторий) и CORPUS (след ядра технологии в корпусе: объём и год появления,
`weak.core`). Зачем последний — в docs/weak-signals.md: без него прикладная статья про
LSTM выглядит новой, потому что по-новому названа.

⚠️ **Модели не показываются ни стадия методолога, ни его обоснование.** Только
название и найденные свидетельства — иначе замер стадии мерил бы переписывание подсказки.

⚠️ **Вывод обязан опираться на свидетельства, а не на знания модели.** ТЗ прямо запрещает
«формирование итоговой выдачи исключительно на основании знаний языковой модели без
подтверждённого поиска». Поэтому промпт требует ссылаться на номера работ, а
`parse_batch` отбрасывает `why` без единой ссылки [N] — такой вывод неотличим от
галлюцинации.

⚠️ Пакет — один кандидат на запрос. Кандидат несёт пять аннотаций, и в пакете из
нескольких модель начнёт путать, чьи работы чьи; раскладка здесь дороже скорости.

## Атомарные признаки (`JUDGMENTS`) — чтение свидетельств, а не вердикт

Тем же вызовом модель отдаёт девять ПРОВЕРЯЕМЫХ по свидетельствам чтений: сколько
компаний названо, есть ли раунд, есть ли продажи, назван ли лидер рынка и т.д. Это
признаки для обучаемой модели этапа 1 ТЗ (`scripts/model_zoo.py`,
`scripts/train_signal_model.py`): классификатор взвешивает их, а «по каким именно
признакам» отвечает разложением коэффициентов (`weak/model.py`).

Приём — **LLM как аннотатор признаков, решение — у интерпретируемой табличной модели**:
labeling functions у Snorkel (Ratner et al. 2017), FeatLLM (Han et al. 2024,
arXiv:2404.09491 — модель порождает правила-признаки, а не приговор по строке).
⚠️ Не наступить: `kind` в признаки модели НЕ идёт. Голос-вердикт как признак заставляет
любой классификатор выучить «сигнал, если хоть кто-то сказал сигнал», а важность
признаков говорит только «важен голос» (`reports/weak-model-zoo.md`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pelican.weak.kinds import EMERGING, KINDS
from pelican.weak.rubric import STAGES

#: Потолок числа свидетельств. Должен вмещать самый широкий состав, который собирает
#: `trends ask` (5 новостей + 2 работы + след корпуса): ⚠️ хвост сверх потолка молча
#: отрезается при сборке промпта, и первым отрезается след корпуса — он идёт последним.
#: ⚠️ Сам состав 5:2 — догадка (замерено плечо 3:2, docs/todo.md §64).
EVIDENCE = 8
EVIDENCE_CHARS = 320

_REF = re.compile(r"\[(\d+)\]")

SYSTEM_PROMPT = f"""You assess one TECHNOLOGY using ONLY the EVIDENCE listed under
it. Each item has a number, a date, a type (PAPER = research work, NEWS = industry
news, CODE = public repository, CORPUS = how often the technology appears across
millions of indexed papers, counted twice: for its underlying core and for this exact
wording) and its text.

Answer three fields.

kind — the FIRST of these that applies, checked in this order:
  "{KINDS[0]}"  a whole field or category, not a specific technology
                 ("artificial intelligence", "robotics", "fintech"). A CORPUS item
                 whose core is huge while the exact wording appears in no paper is
                 usually a market phrase from a press headline, not a technology.
  "{KINDS[1]}"  not a technology at all: a person, a company, a market event,
                 a policy debate, or the evidence is about something unrelated.
                 When THE USER ASKED ABOUT is given, a technology from a DIFFERENT
                 FIELD is also "{KINDS[1]}": solar home storage in a question about
                 AI security, electrical transformers in a question about fintech.
                 ⚠️ Neighbouring is not different: hardware, security, privacy or
                 networking work that the asked-about field actually uses stays a
                 technology and is judged on its own merits.
  "{KINDS[2]}"  a marketing label with no technical substance behind it.
  "{KINDS[3]}"  an industry standard, protocol specification or regulation itself.
  "{KINDS[4]}"  mass-adopted: an established market, clear leaders, the evidence
                 treats it as a routine tool rather than as something new. A CORPUS
                 item with thousands of papers over many years means the core
                 technology is established: if the TECHNOLOGY simply IS that core, or
                 is that core routinely applied to a standard task, it is mature. An
                 established core whose exact wording is rare may instead be that core
                 used in a new way - then judge by the NEWS and PAPERS, not by the core.
  "{KINDS[5]}"  a specific, still-early technology: presented as new, open,
                 being prototyped, piloted or first brought to market. An established
                 core used in a genuinely new way that did not exist before can be
                 emerging; a rare CORPUS count alone does not make something emerging.

stage — the MOST ADVANCED stage the evidence shows anyone has reached:
  "{STAGES[0]}"  concept or research only: theory, simulation, position papers
  "{STAGES[1]}"  prototype or proof of concept: a built system shown in a lab or demo
  "{STAGES[2]}"  pilot: tested with real customers, real data or in the field
  "{STAGES[3]}"  early adoption: first commercial deployments, products on sale,
                 paying customers
  Research papers usually describe an earlier stage than companies have reached;
  when NEWS shows a later stage than the PAPERS, the news decides.

why — ONE sentence in RUSSIAN explaining kind and stage, citing the supporting
  items by number in square brackets, e.g. "Работы [1] и [3] описывают прототипы,
  а новость [4] — первые коммерческие поставки". Cite only listed items. Do not use
  knowledge that is not in the evidence.

Then READ the evidence literally and fill these fields. Each is a fact about the
listed items only, never about what you know:
  players_named          how many DISTINCT companies or startups the NEWS items name
                         as doing this technology (0 if no NEWS or none named)
  funding_mentioned      any item mentions a funding round, raised amount, valuation
                         or acquisition
  commercial_deployment  any item mentions products on sale, paying customers or
                         commercial deployments
  pilot_mentioned        any item mentions a pilot, field trial or test with real
                         customers or real data
  market_leaders_named   any item names established market leaders or a formed
                         market with clear leaders for this technology
  routine_tool           the items treat the technology as a routine, standard tool
                         rather than as something new
  standard_or_regulation the TECHNOLOGY itself is a standard, protocol specification
                         or regulation
  field_not_technology   the TECHNOLOGY name is a whole field, market or category
                         rather than one specific technology
  novelty_claimed        any item presents it as new, first, novel or emerging"""


PAPER = "PAPER"
NEWS = "NEWS"
CODE = "CODE"
CORPUS = "CORPUS"


@dataclass(slots=True)
class Evidence:
    signal_id: int
    date: str
    text: str
    #: PAPER / NEWS / CODE — модели показывается, чтобы она различала стадию
    #: работы и стадию компании (docs/weak-signals.md, замер стадии).
    kind: str = PAPER
    url: str = ""
    publisher: str = ""
    #: Текст сверх заголовка — аннотация работы, сниппет живого источника (`weak.deepen`).
    #: ⚠️ Видит его только карточка: промпт оценки замерен на заголовках, и `build_prompt`
    #: здесь поле не читает.
    detail: str = ""


@dataclass(slots=True)
class Candidate:
    key: str
    name: str
    evidence: list[Evidence]
    #: Область, о которой спросил пользователь. ⚠️ Пусто в замере на датасете: там запроса
    #: нет, и промпт обязан остаться прежним, иначе замеренный F1 жанра перестаёт
    #: относиться к выпускаемому коду.
    area: str = ""


#: Атомарные признаки из оценки: имя → тип. Порядок закреплён — по нему пишется TSV
#: замера и читается `weak/model.json`.
JUDGMENTS: tuple[tuple[str, type], ...] = (
    ("players_named", int),
    ("funding_mentioned", bool),
    ("commercial_deployment", bool),
    ("pilot_mentioned", bool),
    ("market_leaders_named", bool),
    ("routine_tool", bool),
    ("standard_or_regulation", bool),
    ("field_not_technology", bool),
    ("novelty_claimed", bool),
)
JUDGMENT_NAMES: tuple[str, ...] = tuple(name for name, _ in JUDGMENTS)
#: Потолок числа компаний: больше в пяти новостях не бывает, а модель с числом без
#: потолка иногда отдаёт год. ⚠️ Догадка.
PLAYERS_MAX = 20


@dataclass(slots=True)
class Assessment:
    key: str
    kind: str
    stage: str
    why: str
    cited: list[int]
    #: Атомарные признаки (`JUDGMENTS`) как числа; пусто у оценок старого формата.
    judgments: dict[str, float] = field(default_factory=dict)

    @property
    def shown(self) -> bool:
        return self.kind == EMERGING


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "stage": {"type": "string", "enum": list(STAGES)},
            "why": {"type": "string"},
            **{
                name: {"type": "integer", "minimum": 0} if typ is int else {"type": "boolean"}
                for name, typ in JUDGMENTS
            },
        },
        "required": ["kind", "stage", "why", *JUDGMENT_NAMES],
        "additionalProperties": False,
    }


def build_prompt(candidate: Candidate) -> str:
    lines = [f"TECHNOLOGY: {candidate.name}"]
    if candidate.area:
        # ⚠️ Без этой строки тематичность не судима ВООБЩЕ: оценщик видит кандидата, но не
        # видит, о чём спросили, и «plug-in solar home systems» в запросе про безопасность
        # ИИ для него — обычная зарождающаяся технология. Разметка глазами: каждая пятая
        # карточка ТОП была про другую отрасль.
        lines.append(f"THE USER ASKED ABOUT: {candidate.area}")
    lines += ["", "EVIDENCE:"]
    for i, ev in enumerate(candidate.evidence[:EVIDENCE], 1):
        who = f", {ev.publisher}" if ev.publisher else ""
        lines.append(f"[{i}] ({ev.date}, {ev.kind}{who}) {ev.text[:EVIDENCE_CHARS]}")
    return "\n".join(lines)


def parse(payload: dict[str, Any], candidate: Candidate) -> Assessment:
    kind = str(payload.get("kind") or "")
    stage = str(payload.get("stage") or "")
    if kind not in KINDS:
        raise ValueError(f"жанр вне словаря: {kind!r}")
    if stage not in STAGES:
        raise ValueError(f"стадия вне шкалы: {stage!r}")
    why = " ".join(str(payload.get("why") or "").split())
    shown = len(candidate.evidence[:EVIDENCE])
    cited = sorted({int(n) for n in _REF.findall(why) if 1 <= int(n) <= shown})
    if not cited:
        # ⚠️ Вывод без единой ссылки на работу неотличим от знаний модели, а их
        # ТЗ как основание запрещает. Жанр и стадию оставляем, объяснение — нет.
        why = ""
    return Assessment(candidate.key, kind, stage, why, cited, judgments(payload))


def judgments(payload: dict[str, Any]) -> dict[str, float]:
    """Атомарные признаки из ответа как числа. Нет поля — нет ключа (не ноль)."""
    out: dict[str, float] = {}
    for name, typ in JUDGMENTS:
        if name not in payload or payload[name] is None:
            continue
        v = payload[name]
        if typ is bool:
            if isinstance(v, str):
                v = v.strip().lower() in ("true", "yes", "1")
            out[name] = 1.0 if v else 0.0
        else:
            try:
                out[name] = float(min(max(int(v), 0), PLAYERS_MAX))
            except (TypeError, ValueError):
                continue
    return out
