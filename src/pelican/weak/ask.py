"""`trends ask`: открытый запрос по-русски → ТОП-15 слабых сигналов с карточками.

Весь путь собран из деталей, у каждой из которых свой замер (docs/weak-signals.md):

1. **фасеты** — модель раскрывает запрос в несколько конкретных направлений
   (утвердительно: bi-encoder кладёт `not X` рядом с `X`, docs/search.md) и даёт
   английскую формулировку для новостного поиска;
2. **привязка** фасетов к локальному корпусу — сырой русский текст, без перевода
   (`weak.ground`, hit@8 = 28/30);
3. **приём** в каждой найденной работе — стадия `tech` (выход 58%, сдвигов раскладки 0);
4. **сведение** вариантов написания — UPGMA внутри запроса (`cluster.build_groups`),
   а между потоками — по ЯДРУ технологии, строкой: оно воспроизводимо, вектор — нет;
5. **денежный поток** кандидатов — направление, общее для НЕСКОЛЬКИХ компаний, названное
   по пачке заголовков о раундах и пилотах (`weak.money`), плюс новости по каждому
   кандидату (`weak.live`);
6. **жанр и стадия** — модель по смешанным свидетельствам (`weak.assess`; жанр:
   93% полноты, 0 ложных из 30 контрольных);
7. **углубление** показанных — аннотации работ и фактовые заголовки (`weak.deepen`);
8. **карточка** по-русски — только по свидетельствам, с датами и числами (`weak.card`).

## Уверенность — вероятность модели роста; проверки — качество свидетельств

ТЗ просит «скоринг — уверенность модели» и счёт сигналов «с уверенностью выше 75%».
Уверенность — **вероятность модели роста** `weak/growth.json` (`score`; текст для
пользователя — `page.HINTS["confidence"]`). Без модели (`score` вернул `False`)
уверенность — доля пройденных проверок.

Проверки — «качество свидетельств» (`page.HINTS["evidence"]`), каждая показывается в
карточке:

| проверка | что значит |
|---|---|
| жанр `emerging` | модель по свидетельствам отнесла к зарождающимся |
| несколько игроков | в заголовках названы ≥ 2 разные компании |
| независимые издатели | новости пришли с ≥ 2 разных доменов издателей (правило ТЗ) |
| научная опора | ≥ 2 работ, в тексте которых стоит ядро технологии |
| объяснение со ссылками | вывод модели ссылается на номера свидетельств |

⚠️ **Проверка, проходящая по построению, — не проверка.** Прежние «≥2 работы корпуса» и
«есть источник выше пониженной доверенности» срабатывали почти у всех: работы мы сами
кладём по две, а неизвестный домен по правилу ТЗ считается средним. В одном прогоне 11
кандидатов из 15 получали ровно 1.00, и порядок внутри ТОП переставал что-либо значить.
Правило: на пуле каждая проверка обязана давать и «да», и «нет» (печатает
`scripts/measure_ask.py`); срабатывающая больше чем у 90% — заменяется.

## Порядок ТОП — по уверенности

В ТОП идёт только `emerging` (`Signal.shown`), прошедший проверку соответствия запросу
(`_on_topic`). Порядок — уверенность модели роста, при равенстве и без модели — `top_key`:
«за направлением кто-то стоит» → **модель
списка заказчика** (`weak/listed.json`, метка «вошёл в список методолога 2026» на трёх
срезах бэктеста; docs/weak-audit.md) → запасной ключ **доля работ ядра за последний год**
→ число независимых игроков → видимость ниже медианы пула (перцентиль у BERTrend) →
уверенность, свежесть. Не вошедшие по месту уходят в отдельный список: отсев считается
вслух.

⚠️ **Тренда и балла в выдаче нет — по замеру** (docs/todo.md §81): обе проверенные оси
хуже константы, а рост публикаций — не то, что заказчик зовёт трендом.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from pelican import events
from pelican.config import settings
from pelican.embed import THRESHOLD, build_groups
from pelican.llm import LLMClient, LLMError, LLMTruncated
from pelican.store import Store
from pelican.weak import asof, flags, onpoint, rounds, topic, twins
from pelican.weak import burst as burst_mod
from pelican.weak import card as card_mod
from pelican.weak import deepen as deepen_mod
from pelican.weak import extra as extra_mod
from pelican.weak import layers as layers_mod
from pelican.weak import model as model_mod
from pelican.weak import patents as patents_mod
from pelican.weak import technique as technique_mod
from pelican.weak.assess import CODE, CORPUS, NEWS, PAPER, Candidate, Evidence
from pelican.weak.assess import EVIDENCE as ASSESS_EVIDENCE
from pelican.weak.assess import SYSTEM_PROMPT as ASSESS_PROMPT
from pelican.weak.assess import build_prompt as assess_prompt
from pelican.weak.assess import parse as assess_parse
from pelican.weak.assess import schema as assess_schema
from pelican.weak.core import (
    Footprint,
    cores,
    corpus_line,
    footprints,
    industry_share,
    names_core,
    significant,
)
from pelican.weak.dataset import (
    ARM_EVIDENCE,
    EMBED_FEATURES,
    FEATURE_LABELS,
    JUDGMENT_FEATURES,
    Row,
    _share,
    age_share,
    embed_features,
    evidence_for,
    features,
    llm_features,
)
from pelican.weak.ground import ground
from pelican.weak.group import vectors_for
from pelican.weak.kinds import EMERGING, MATURE, NOISE, TOO_BROAD
from pelican.weak.kinds import label_of as kind_label
from pelican.weak.live import LiveDoc, news_many_measured
from pelican.weak.live import search as live_search
from pelican.weak.model import PLAYERS_CAP, SHARE_PRIOR_WEIGHT
from pelican.weak.money import candidates as money_candidates
from pelican.weak.money import companies_of
from pelican.weak.money import queries as money_queries
from pelican.weak.neighbours import neighbourhoods
from pelican.weak.rubric import evidence_stage
from pelican.weak.tech import MARKET_WORDS, is_category, query_words, run_tech
from pelican.weak.tech import _too_broad as too_broad
from pelican.weak.trust import (
    ASSOCIATION,
    GOV,
    HIGH,
    LEVEL_LABELS,
    MEDIA,
    PREPRINT,
    SCIENCE,
    UNIVERSITY,
    classify,
    corroborated,
)

TOP = 15
#: Поднимается, когда меняется СОДЕРЖИМОЕ выдачи (не вёрстка — её интерфейс строит
#: из JSON сам): отчёты прошлых версий кэш повторов больше не отдаёт (`jobs.cached_report`).
#: 1 — тематичность, стадия, гипотеза, доверенность, русские названия исключённых.
#: 2 — исправления по ревью: состав свидетельств оценки, разметка найденного без горизонта,
#: след корпуса, денежный поток.
REPORT_VERSION = 2
#: Спрашивать ли первым кругом денежного потока подрынки области (см. `expand`).
#: Выключатель нужен замеру A/B (`scripts/measure_ask.py --no-submarkets`).
SUBMARKETS = True
#: Модель списка заказчика (`weak/listed.json`) — первым ключом порядка после нулевого,
#: посчитанная на ВСЁМ пуле до сортировки. Выключена: вживую не выигрывает у `top_key` без
#: неё на том же пуле (22 строки списка против 23, docs/weak-audit.md), а стоит 210–390 с на запрос
#: (медиана 240; проход по шардам, денежный след, первый запрос дня платит ещё и маски окон)
#: при потолке заказчика в 1200 с. Включается замером:
#: `WEAK_LISTED_IN_ASK=1` (`weak/flags.py`) или `scripts/measure_ask.py --with-listed`.
#: Имя модуля остаётся: его подменяют замер и тесты.
LISTED_IN_ASK = flags.LISTED_IN_ASK
#: Бюджет времени запроса, с. Потолок заказчика — 1200 с; запас — на сохранение отчёта и
#: опрос статуса. ⚠️ Догадка, не замер (docs/todo.md).
BUDGET_S = float(os.getenv("WEAK_BUDGET_S", "1100"))
#: Сколько оставить карточкам: 208–258 с на 15 сигналов (docs/weak-demo.md). Патенты и
#: углубление идут, только пока после них остаётся столько; карточки — продукт и идут всегда.
CARDS_RESERVE_S = 260.0
#: Меньше этого остатка до резерва карточек углубление не начинается: аннотации arXiv и
#: OpenAlex на 15 сигналов — десятки секунд. ⚠️ Догадка.
DEEPEN_MIN_S = 60.0

#: Метка патентного свидетельства в карточке. ⚠️ В промпт зрелости она НЕ попадает:
#: патенты добираются после оценки, и состав свидетельств `assess` остаётся замеренным.
PATENT_EV = "PATENT"
#: Работ на фасет. ⚠️ Догадка: 6 фасетов × 30 = 180 работ.
PER_FACET = 30
#: Кандидатов, которых доводим до оценки. Больше ТОП-15, потому что часть уйдёт в отсев.
#: ⚠️ Догадка, не замер (docs/todo.md).
CANDIDATES = 45
#: Сколько ярлыков сводить к ядру. ⚠️ Догадка: ~1 с модели на 12 ярлыков.
CORE_POOL = 80
#: Сколько из них отдаётся денежному потоку. ⚠️ Догадка: денежных кандидатов берём
#: больше научных, потому что игроки — обязательное условие ТОП, а научный поток их
#: почти не даёт (прогон по безопасности ИИ: 10 денежных против 35 научных на входе,
#: но в ТОП вышли 4 денежных и 5 научных, и у научных «игроки» оказались журналами).
MONEY_POOL = 35
#: Сколько компаний считается «несколькими игроками» в проверке карточки. ⚠️ Условием
#: ТОП это НЕ является (замер `scripts/label_listed.py` по трём срезам бэктеста: корзина
#: «один игрок» попадает в список заказчика 2026 чаще всех — 44–60% против ~20% у ТОП;
#: у заказчика медиана 4 компании на строку, но это компании 2026-го, на срезе их одна).
MIN_PLAYERS = 2
SCIENCE_STREAM = "наука"
MONEY_STREAM = "деньги"
#: 5 новостей + 2 работы + след корпуса = `assess.EVIDENCE`. ⚠️ Состав 5:2 —
#: ДОГАДКА (замерено было 3:2, §64): новостей больше, потому что стадию и игроков
#: видно по ним, а не по статьям. Сумма обязана не превышать `EVIDENCE`, иначе хвост
#: молча отрезается при сборке промпта.
NEWS_PER_CARD = 5
PAPERS_PER_CARD = 2
#: Типы источников, которые ТЗ считает самостоятельным подтверждением. ⚠️ Вендорский
#: пресс-релиз, блог и агрегатор сюда не входят намеренно: по ТЗ они не могут быть
#: единственным основанием.
INDEPENDENT_KINDS = frozenset({SCIENCE, PREPRINT, GOV, UNIVERSITY, ASSOCIATION, MEDIA})

#: Сколько заголовков доносится в карточку сверх `NEWS_PER_CARD` ради того, чтобы у
#: названных компаний был источник. ⚠️ Догадка, зажатая с двух сторон: меньше — часть
#: игроков остаётся непроверяемой, больше — карточка тонет в ссылках, а модель платит
#: за каждое свидетельство. Замер доли непроверяемых компаний — `measure_card_facts.py`.
EXTRA_FOR_COMPANIES = 3
#: Сколько работ привязывается к денежному кандидату для проверки научной опоры.
#: ⚠️ Догадка, не замер (docs/todo.md).
PAPERS_FOR_CHECK = 4

#: ⚠️ ДОГАДКИ (docs/todo.md §63): тренд.

ProgressHook = Callable[[str], None] | None
#: Хук структурных событий прогона: (вид, данные). Контракт и этапы — `pelican.events`;
#: их проигрывает экран ожидания веб-интерфейса. Строки `say()` остаются.
EventHook = Callable[[str, dict], None] | None

EXPAND_PROMPT = """The user asks, in Russian, for emerging technologies in some area.
First decide what the SUBJECT is: the thing the user actually named, in their own
words, not a broader field that contains it.
Stay inside the subject. Technologies come from the subject itself: its materials,
techniques, tools, equipment, processes and methods of study. Never add a
general-purpose technology the user did not name - AI, machine learning, neural
networks, blockchain, VR/AR, metaverse, digital twins, IoT, drones, apps - unless the
subject itself is software, computing or electronics. In beekeeping, "varroa-resistant
bee breeding" is right and "AI hive monitoring" is wrong; in artificial intelligence,
"multimodal LLMs" is right.
Return:
  facets    - 6 search phrases, each naming a family of NEW technical approaches of
              the subject that appeared in the last two or three years, as a Russian
              noun phrase of 4 to 12 words, stated affirmatively (never "not ...").
              Name the technology itself, the way a research paper title would, not
              a generic task: in materials science "самовосстанавливающиеся полимеры
              для гибкой электроники", never "методы анализа свойств материалов"; in
              ceramics "низкотемпературные глазури на основе стеклобоя".
  facets_en - the same six facets in English, each 2 to 5 words, named the way an
              industry news headline would name the technology ("AI agent identity",
              "neuromorphic edge chips", "varroa-resistant bee breeding",
              "recycled glass glazes").
  query_en  - the user's request as a short English search query of 3 to 6 words.
  area_en   - ONLY the subject in English, 1 to 5 words, keeping every name the user
              gave and never widening it to a parent field, and dropping every word
              like "emerging", "new", "weak signals", "trends", "technologies" or
              "solutions" ("cybersecurity", "industrial robotics", "fintech",
              "beekeeping", "studio ceramics").
  submarkets_en - 4 to 6 SUB-MARKETS of the subject an industry analyst would list
              besides its technological core, each 2 to 4 English words that name the
              sub-market inside THIS subject: components and materials, equipment and
              tooling, services, certification and compliance, insurance and financing,
              data and testing ("robot insurance", "satellite components", "fintech
              compliance certification", "battery testing services", "kiln
              equipment"). The same rule: no technology the user did not name."""


def _expand_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "facets": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 6},
            "facets_en": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            "query_en": {"type": "string"},
            "area_en": {"type": "string"},
            "submarkets_en": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        },
        "required": ["facets", "facets_en", "query_en", "area_en", "submarkets_en"],
        "additionalProperties": False,
    }


@dataclass(slots=True)
class Source:
    n: int
    title: str
    url: str
    date: str
    kind: str  # тип источника по ТЗ (weak.trust)
    evidence: str  # PAPER / NEWS / CODE
    lang: str
    trust: str  # high / medium / low
    trust_label: str
    publisher: str = ""
    summary_ru: str = ""
    #: Домен ИЗДАТЕЛЯ, по которому считана доверенность. ⚠️ Не домен `url`: у
    #: Google News ссылка — переадресация `news.google.com` (`weak.live`).
    domain: str = ""
    #: Резюме на русском написано моделью (ТЗ: машинный перевод или генеративное резюме
    #: отмечается возле источника). Поле, а не вычисление: оно обязано доехать до JSON
    #: и до интерфейса. Истинно у ЛЮБОГО резюме от модели, включая русские источники.
    machine_summary: bool = False
    #: Текст сверх заголовка, который видела карточка (аннотация, сниппет — `weak.deepen`).
    #: Хранится, чтобы число карточки можно было сверить по отчёту, а не по памяти модели.
    excerpt: str = ""


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str
    #: Условие ПОПАДАНИЯ в ТОП. ⚠️ В уверенность такая проверка не идёт: внутри ТОП она
    #: истинна по построению, и складывать её в балл — это держать константу под видом
    #: признака. Из-за двух таких проверок уверенность принимала ровно три значения
    #: (0.6 / 0.8 / 1.0) и кандидатов почти не различала (docs/todo.md §72).
    entry: bool = False
    #: Идёт ли в долю проверок. «Несколько игроков» — подпись: условием ТОП не является
    #: (`MIN_PLAYERS`), но и в долю не идёт — по тому же замеру §72.
    scored: bool = True

    @property
    def counted(self) -> bool:
        """В долю проверок: не условие входа и не подпись."""
        return self.scored and not self.entry


@dataclass(slots=True)
class Signal:
    label: str
    title: str
    kind: str
    kind_label: str
    stage: str
    confidence: float
    checks: list[Check]
    why: str
    description: str = ""
    advantage: str = ""
    case: str = ""
    #: Гипотеза для эксперта («если…, то…», `card.Card.hypothesis`); пусто — не прошла чистку.
    hypothesis: str = ""
    #: Хронология карточки: `{"date": "2026-03", "text": "… [n]"}` (`weak.card`).
    facts: list[dict[str, str]] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    papers: int = 0
    news: int = 0
    recent_share: float = 0.0
    #: Ядро технологии и его след в корпусе (`weak.core`): объём и год появления.
    core: str = ""
    core_works: int = 0
    core_since: int | None = None
    #: След САМОГО ярлыка: сколько работ называют его этими же словами.
    label_works: int = 0
    #: Видимость ниже медианы пула — низкий DoV у Yoon (2012), перцентиль у BERTrend.
    low_visibility: bool = False
    #: Доля пройденных проверок свидетельств сверх условий отбора — «качество свидетельств»
    #: карточки. `confidence` равна ей только когда модели скоринга нет.
    evidence_share: float = 0.0
    #: Вероятность модели этапа 1 (`weak/model.json`) и её вклады. В выдаче не считается:
    #: будущий рост она не предсказывает (docs/weak-audit.md); поле — для `predict_dataset`.
    probability: float | None = None
    predictors: list[str] = field(default_factory=list)
    #: Атомарные чтения свидетельств из той же оценки (`assess.JUDGMENTS`) — признаки `J`
    #: обученной модели; приезжают с оценкой, лишних вызовов не стоят.
    judgments: dict[str, float] = field(default_factory=dict)
    #: Модель списка заказчика (`weak/listed.json`): шанс, что направление войдёт в список
    #: методолога, и три её главных вклада — ПЕРВЫЙ ключ порядка ТОП (после нулевого).
    #: `None` — модели нет или не посчиталось; тогда порядок — по `core_recent_share`.
    listed: float | None = None
    listed_predictors: list[str] = field(default_factory=list)
    #: Компании, названные в заголовках. В отбор ТОП не входят (см. `MIN_PLAYERS`).
    companies: list[str] = field(default_factory=list)
    #: Доля работ ядра за последний год (`dataset.core_last_year_share`) — запасной ключ
    #: порядка ТОП (когда нет `listed`); `None` — измерить нечем.
    core_recent_share: float | None = None
    #: Откуда кандидат: научный поток (корпус) или денежный (новости о раундах).
    stream: str = ""
    #: Патентная активность по ядру: «публикаций за два года — N, за предыдущие два — M».
    #: Пусто, когда источник выключен или измерить нечем (docs/patents.md).
    patents: str = ""
    #: В скольких из `AskResult.history_runs` прошлых прогонов ЭТОГО ЖЕ запроса есть то
    #: же направление (`weak.history`). ⚠️ В уверенность и в порядок ТОП не входит: это
    #: согласие выдачи с самой собой, а не качество сигнала.
    held: int = 0
    #: Оценка тематичности 0–3 (`weak.topic`, шкала UMBRELA) и её объяснение. В ТОП идут 2 и
    #: 3 (`_on_topic`); между ними оценка — подпись, в порядок и уверенность не входит.
    #: `None` — не оценивалось (старые отчёты, сбой проверки).
    relevance: int | None = None
    relevance_why: str = ""
    #: Дословная опора объяснения из источника (`card.Card.why_quote`); пусто — не сверена.
    why_quote: str = ""
    #: Новостной поиск по кандидату не ответил и после повтора (`live.news_many_measured`):
    #: «новостей нет» у такого кандидата — не измерение (три исхода, как `check_state` у
    #: copycat).
    news_unmeasured: bool = False
    #: Патентных публикаций по ядру за два года (`patents.Activity.recent`); `None` — не
    #: измерено. Подтверждение для слоя «слабый сигнал» (`weak.layers`).
    patents_recent: int | None = None
    #: Рост патентов «два года против двух до» (`patents.Activity.growth`); `None` — не мерили.
    patents_growth: float | None = None
    #: Слой выдачи (`weak.layers`: trend / weak / watch) и его причина; пусто — слои выключены.
    layer: str = ""
    layer_reason: str = ""
    #: Категория направления и приём сверх неё (`weak.technique`); пусто — не оценивалось.
    category: str = ""
    technique: str = ""
    #: Находки дополнительных источников (`weak.extra`): источник → число документов.
    extra: dict[str, int] = field(default_factory=dict)
    #: Направление запроса — индекс в `AskResult.facets` (`assign_facets`): сектор радара
    #: веб-интерфейса. В отбор, порядок и уверенность не входит; `None` — не привязано.
    facet: int | None = None

    @property
    def shown(self) -> bool:
        """Фильтр мейнстрима схемы 1 ТЗ: в ТОП идёт только `emerging`. Зрелое, стандарт,
        хайп, шум и слишком общая категория уходят в исключённые с причиной — ТЗ прямо
        запрещает включать в выдачу зрелые тренды и сформированные рынки.
        ⚠️ Не наступить: по покрытию списка заказчика `mature` попадает в список так же
        часто, как ТОП (~20%, `scripts/label_listed.py`), и отбор «всё, кроме шума» выигрывал
        этот замер — но пускал в ТОП по 2–6 зрелых на запрос при 15–23 не вошедших
        `emerging`. Список заказчика — не цель выдачи; требование ТЗ — цель."""
        return self.kind == EMERGING

    @property
    def corroborated(self) -> bool:
        """Правило ТЗ: соцсети, блоги, пресс-релизы — не единственное основание. Из выдачи
        такой сигнал не снимается (ТЗ допускает и отметку), а помечается «пониженная
        доверенность» (`page`). Без источников — не помечается: судить не о чем."""
        return not self.sources or corroborated([s.trust for s in self.sources])


@dataclass(slots=True)
class AskResult:
    query: str
    query_en: str
    facets: list[str]
    started: str
    seconds: float = 0.0
    model: str = ""
    embedder: str = ""
    #: Все модели, участвовавшие в ответе: роль, модель, точка доступа. ТЗ требует
    #: раскрывать выбор модели для конкретного ответа и логировать его. Выбор здесь
    #: фиксирован конфигурацией — автоматического выбора модели нет.
    models: list[dict[str, str]] = field(default_factory=list)
    works_found: int = 0
    works_named: int = 0
    candidates: int = 0
    sources_processed: int = 0
    live_by_source: dict[str, int] = field(default_factory=dict)
    live_errors: dict[str, str] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    excluded: list[Signal] = field(default_factory=list)
    #: Жанр зарождающийся, но за кандидатом один игрок или ни одного. Считается вслух.
    thin: list[Signal] = field(default_factory=list)
    #: «Зарождающееся — наблюдать»: не тренд, но и не подтверждено двумя путями
    #: (`weak.layers`). Пусто, когда слои выключены.
    watch: list[Signal] = field(default_factory=list)
    context: list[Source] = field(default_factory=list)
    headlines_seen: int = 0
    #: Английские слова запроса: по ним считается тематичность направления. ⚠️ Русские
    #: `facets` для этого не годятся — названия направлений английские, и пересечение
    #: слов у них пустое всегда.
    area_en: str = ""
    facets_en: list[str] = field(default_factory=list)
    #: Направления из всплеска в корпусе (`weak.burst`); пусто, когда флаг выключен.
    bursts: list[str] = field(default_factory=list)
    #: Подрынки области, которыми расширен первый круг денежного потока (`expand`).
    #: ⚠️ В `query_words` они не входят намеренно: слово подрынка («insurance»,
    #: «components») в названии направления — не признак категории.
    submarkets_en: list[str] = field(default_factory=list)
    #: Компании, названные группировщиком, но не найденные ни в одном заголовке
    #: поодиночке. ⚠️ Считается вслух: это расхождение ДВУХ моделей на одном и том же
    #: тексте, и молча выбрасывать такое имя нельзя — надо знать, сколько их.
    players_unnamed: int = 0
    #: Кандидаты, на которых оценка зрелости не удалась (таймаут, обрезанный или
    #: невалидный ответ). ⚠️ Считаются вслух: иначе кандидат исчезает из выдачи без следа.
    unassessed: list[str] = field(default_factory=list)
    #: Разметка найденных работ приёмами (`tech.TechStats`): ждало разметки, упавших
    #: пакетов, переставленных по эху элементов, модель недоступна.
    tech_stats: dict[str, int | bool] = field(default_factory=dict)
    #: Признанные зарождающимися, но снятые проверкой тематичности (`weak.topic`).
    off_topic: int = 0
    #: Названия, снятые как рыночная категория: в них нет ни одного слова сверх
    #: самого запроса. Считаются вслух — это часть логики исключений по ТЗ.
    categories: list[str] = field(default_factory=list)
    #: Сколько прошлых прогонов того же запроса учтено в `Signal.held` (0 — история
    #: пуста) и сколько сигналов ТОП держатся хотя бы в половине из них (`weak.history`).
    history_runs: int = 0
    stable: int = 0
    #: Версия содержимого отчёта (`REPORT_VERSION` на момент прогона; 0 — собран до
    #: версионирования). Кэш повторов (`jobs.cached_report`) отдаёт только текущую версию:
    #: иначе повтор запроса показывает выдачу, собранную до исправлений.
    version: int = 0

    @property
    def confident(self) -> int:
        """Сигналов с уверенностью модели скоринга выше 75% (ТЗ, «будет плюсом»).

        Уверенность — калиброванная вероятность модели роста (`score`); без модели —
        доля проверок свидетельств.
        """
        return sum(1 for s in self.signals if s.confidence > 0.75)


def _client() -> LLMClient:
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
        reasoning_effort=settings.llm_reasoning_effort,
    )


async def _one(system: str, user: str, schema: dict, name: str, max_tokens: int) -> dict:
    async with httpx.AsyncClient() as http:
        return await _client().json_completion(
            http, system=system, user=user, schema=schema, name=name, max_tokens=max_tokens
        )


def _evidence(
    shown: list[LiveDoc],
    backing: list[LiveDoc],
    works: list[tuple[str, str, str, str, int]],
    core_fp: Footprint,
    label_fp: Footprint,
    today: date,
) -> tuple[list[Evidence], list[Source], int]:
    """Свидетельства и источники карточки в одном порядке + сколько первых видит оценщик.

    `works` — (дата, текст, ссылка, источник, id) отобранных работ.

    Номера источников в карточке совпадают с номерами свидетельств, поэтому порядок общий.
    ⚠️ Не наступить: работы и след корпуса обязаны стоять ДО добранных заголовков
    (`backing`). Промпт оценки показывает только первые `assess.EVIDENCE`, и добор,
    поставленный раньше, вытеснял за потолок след и обе работы — у кандидатов с игроками
    зрелость оценивалась по одним новостям (PEL-E-01).
    """
    ev: list[Evidence] = []
    sources: list[Source] = []

    def add_live(d: LiveDoc) -> None:
        # ⚠️ Google News отдаёт и статьи журналов (Nature, IEEE): рыночным
        # подтверждением они не являются, и модель должна видеть их как PAPER.
        kind = PAPER if d.trust.kind in (SCIENCE, PREPRINT) else NEWS
        ev.append(Evidence(-1, d.published, d.title, kind, d.url, d.publisher, d.snippet))
        sources.append(_live_source(len(sources) + 1, d, kind))

    for d in shown:
        add_live(d)
    for day, text, url, src, sid in works:
        ev.append(Evidence(sid, day, text, PAPER, url, src))
        sources.append(_paper_source(len(sources) + 1, day, text, url, src))
    ev.append(Evidence(-1, today.isoformat(), corpus_line(core_fp, label_fp), CORPUS))
    sources.append(_corpus_source(len(sources) + 1, core_fp, label_fp, today.isoformat()))
    assessed = len(ev)
    for d in backing:
        add_live(d)
    return ev, sources, assessed


#: Потолок ответа оценки: класс, стадия, русское `why` и 9 суждений.
ASSESS_TOKENS = 400


def _assess(candidate: Candidate):
    """Оценка зрелости одного кандидата; обрезанный потолком ответ — один повтор с вдвое
    большим потолком. Русское `why` стоит 2–3 токена на слово и упирается в 400."""
    prompt = assess_prompt(candidate)
    try:
        payload = asyncio.run(_one(ASSESS_PROMPT, prompt, assess_schema(), "assessment", ASSESS_TOKENS))
    except LLMTruncated:
        payload = asyncio.run(
            _one(ASSESS_PROMPT, prompt, assess_schema(), "assessment", 2 * ASSESS_TOKENS)
        )
    return assess_parse(payload, candidate)


@dataclass(slots=True)
class Expansion:
    """Запрос, раскрытый моделью: фасеты, английский запрос, область, подрынки."""

    facets: list[str]
    query_en: str
    area_en: str
    facets_en: list[str]
    submarkets_en: list[str]


def _phrases(got: dict, key: str) -> list[str]:
    return [" ".join(f.split()) for f in got.get(key, []) if str(f).strip()]


#: Дата среза в промпте направлений (`flags.DATED_FACETS`, только под `weak.asof`). Подсказка
#: даты утечку будущего не убирает (Sarkar & Vafa 2024, lookahead bias) — поэтому за ней
#: стоит фильтр по корпусу на дату (`dated_facets`).
DATED_EXPAND = (
    "\n\nTODAY IS {cut}. The user is asking on {cut}: 'the last two or three years' means "
    "the years before {cut}. Name only approaches that already had published papers before "
    "{cut}; never name anything that appeared after {cut}."
)
#: ⚠️ Догадка, не замер: меньше 3 работ по набору слов на дату — направление названо из будущего.
LEAK_MIN = 3


def dated_facets(store: Store, facets_en: list[str], bursts: list[str], n: int = 6) -> list[str]:
    """Направления на дату среза: без работ в корпусе на дату — вон, место добирает всплеск.

    Направление, по которому на дату в корпусе меньше `LEAK_MIN` работ, аналитик в тот день
    назвать не мог — это знание модели о будущем. Пробник на диффузии (+12, +18): работ о
    цели среди найденного 0 и 5 → 11 и 14; всплеск без модели — 0.
    """
    fps = footprints(store, facets_en, loose=True)
    kept = [f for f in facets_en if f in fps and fps[f].works >= LEAK_MIN]
    return kept + [b for b in bursts if b not in kept][: max(n - len(kept), 0)]


def expand(query: str) -> Expansion:
    """Раскрыть запрос в направления, область и подрынки одним вызовом модели.

    ⚠️ Область — отдельным полем, а не английским запросом: в шаблоны денежного
    потока подставлялось «weak signals cybersecurity technologies startup raises», и
    Google News возвращал статьи про «слабые сигналы», а не раунды (19 заголовков
    вместо 39 у голого «cybersecurity»).

    Подрынки — **расширение запроса по фасетам предметной области** (faceted query
    expansion), а не по найденному: второй круг денежного потока (`money.follow_up`) —
    это псевдорелевантная обратная связь, и она по построению углубляет то, что нашёл
    первый круг, а в сторону не смотрит (docs/todo.md §76). Компоненты, услуги,
    сертификация и страхование первому кругу иначе не достаются вовсе.
    ⚠️ Число подрынков (4–6) и перечень их типов в промпте — догадка (§91).

    ⚠️ **Дрейф запроса** (query drift, Mitra, Singhal & Buckley, SIGIR 1998): расширение
    уводит поиск от темы. У расширения моделью источник дрейфа — её собственные знания
    (Jagerman et al. 2023, arXiv:2305.03653). Поэтому промпт держит предмет словами
    пользователя и запрещает технологии общего назначения (ИИ, блокчейн, VR), которых
    в запросе нет. Примеры в промпте смешанные, IT и не IT: когда все примеры были из IT,
    «лавка чучел» раскрывалась в «generative avatar AI», «комары» — в «AI population
    monitoring», а «art brut и prison art» — в блокчейн и цифровых двойников, область
    же обобщалась до «contemporary art». Замер — на 10 запросах, глазами.
    """
    system = EXPAND_PROMPT
    if flags.DATED_FACETS and asof.active():
        system += DATED_EXPAND.format(cut=asof.today().isoformat())
    got = asyncio.run(_one(system, query, _expand_schema(), "facets", 900))
    facets = _phrases(got, "facets")
    query_en = " ".join(str(got.get("query_en") or "").split()) or query
    area = " ".join(str(got.get("area_en") or "").split()) or query_en
    return Expansion(
        facets or [query],
        query_en,
        area,
        _phrases(got, "facets_en"),
        _phrases(got, "submarkets_en") if SUBMARKETS else [],
    )


def _papers(store: Store, ids: list[int]) -> dict[int, tuple[str, str, str, str]]:
    """id → (дата, текст, ссылка, источник)."""
    listed = ",".join(str(int(i)) for i in ids)
    if not listed:
        return {}
    rows = store.conn.execute(
        "SELECT id, CAST(observed_at AS DATE), term_raw, url, source "
        f"FROM works WHERE id IN ({listed})"
    ).fetchall()
    return {int(r[0]): (str(r[1]), r[2], r[3] or "", r[4]) for r in rows}


def _mentions(store: Store, ids: list[int]) -> dict[int, str]:
    listed = ",".join(str(int(i)) for i in ids)
    if not listed:
        return {}
    rows = store.conn.execute(
        f"SELECT signal_id, tech FROM tech_mentions WHERE model = ? AND tech <> '' "
        f"AND signal_id IN ({listed})",
        [settings.llm_model],
    ).fetchall()
    # Категория, записанная до нормализованной проверки (`tech._too_broad`), отсекается и
    # при чтении: разметка навсегда, и иначе старые строки обходили бы правило.
    return {int(r[0]): r[1].strip().lower() for r in rows if not too_broad(r[1])}


# Доля свежих дат — одна реализация на выдачу и на замер датасета.
_age_share = age_share


def _live_source(n: int, d: LiveDoc, evidence: str) -> Source:
    t = d.trust
    return Source(
        n,
        d.title,
        d.url,
        d.published,
        t.kind,
        evidence,
        d.lang,
        t.level,
        t.label,
        d.publisher,
        domain=d.domain,
    )


def _named_by(
    docs: list[LiveDoc], firms: dict[str, tuple[str, ...]], label_words: set[str]
) -> dict[str, LiveDoc]:
    """Компания → заголовок, который её назвал. Порядок сохраняется: первым назвавший.

    ⚠️ Компанией не считается слово из самого ярлыка: у «ensemble of specialized large
    language models» игроком оказывался «Ensemble».
    """
    out: dict[str, LiveDoc] = {}
    for d in docs:
        for name in firms.get(d.title, ()):
            if name.lower() not in label_words:
                out.setdefault(name, d)
    return out


def _onpoint(label: str, area: str, named: dict[str, LiveDoc]) -> dict[str, LiveDoc]:
    """Оставить компании, чей заголовок — про эту технологию (`weak.onpoint`).

    ⚠️ Сбой проверки ничего не снимает: «не про технологию» без ответа модели не доказано.
    """
    titles = list(dict.fromkeys(d.title for d in named.values()))[: onpoint.MAX_HEADLINES]
    try:
        about = onpoint.parse(
            asyncio.run(
                _one(
                    onpoint.SYSTEM_PROMPT,
                    onpoint.build_prompt(label, area, titles),
                    onpoint.schema(),
                    "onpoint",
                    120,
                )
            ),
            len(titles),
        )
    except (LLMError, ValueError, httpx.HTTPError):
        return named
    kept = {titles[i - 1] for i in about}
    # Заголовки сверх потолка проверки не судятся — и не снимаются.
    unjudged = {d.title for d in named.values()} - set(titles)
    return {x: d for x, d in named.items() if d.title in kept or d.title in unjudged}


def _backing(shown: list[LiveDoc], rest: list[LiveDoc], named: dict[str, LiveDoc]) -> list[LiveDoc]:
    """Заголовки, которые надо доносить в карточку ради источников у компаний.

    Жадное покрытие множества: на каждом шаге берётся заголовок, называющий больше всего
    ещё не подтверждённых компаний. ⚠️ Замерено, что без этого треть названных игроков
    (127 из 427) в источниках карточки не встречается — и читатель их не проверит.
    """
    covered = {name for name, d in named.items() if d.title in {x.title for x in shown}}
    missing = {name for name in named if name not in covered}
    out: list[LiveDoc] = []
    pool = [d for d in rest if d.title not in {x.title for x in shown}]
    while missing and len(out) < EXTRA_FOR_COMPANIES:

        def gain_of(d: LiveDoc, left: set[str] = missing) -> int:
            return sum(1 for n in left if named[n].title == d.title)

        best = max(pool, key=gain_of, default=None)
        if best is None or not gain_of(best):
            break
        gain = {n for n in missing if named[n].title == best.title}
        out.append(best)
        missing -= gain
        pool = [d for d in pool if d.title != best.title]
    return out


def _corpus_source(n: int, core: Footprint, label: Footprint, day: str) -> Source:
    """След корпуса как ИСТОЧНИК, а не только как свидетельство.

    ⚠️ Без него карточка ссылается на свидетельство, которого читателю не показывают:
    номера источников и свидетельств совпадают по построению, а след шёл только в
    свидетельства — и ссылка `[8]` упиралась в пустоту (docs/todo.md §80).

    ⚠️ Ссылки у этого источника нет и быть не может: это наша собственная база. Вёрстка
    источник без ссылки умеет (`weak/page.py`), а доверенность у него научная — за ним
    стоят arXiv и OpenAlex, а не чьё-то мнение.
    """
    since = f" с {core.first_year}" if core.first_year else ""
    title = (
        f"Локальный научный корпус (arXiv + OpenAlex): ядро «{core.core}» — "
        f"{core.works} работ{since}, из них {core.last_year_works} за последний год; "
        f"само направление — {label.works}"
    )
    return Source(
        n,
        title,
        "",
        day,
        SCIENCE,
        CORPUS,
        "ru",
        HIGH,
        LEVEL_LABELS[HIGH],
        "arXiv + OpenAlex",
    )


def _paper_source(n: int, day: str, text: str, url: str, src: str) -> Source:
    home = "arxiv.org" if src == "arxiv" else "openalex.org"
    t = classify(url or f"https://{home}")
    title = re.split(r"(?<=[.?!])\s", text, maxsplit=1)[0]
    return Source(n, title, url, day, t.kind, PAPER, "en", t.level, t.label, src, domain=home)


@dataclass(slots=True)
class _Cand:
    """Кандидат по пути к оценке: ярлык, его варианты, ядро, заголовки и игроки."""

    label: str
    members: list[str]
    stream: str
    core: str = ""
    headlines: list[LiveDoc] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    #: Ядра сведённых вариантов: запасные, если у `core` нет следа в корпусе.
    cores: list[str] = field(default_factory=list)


#: Насколько два названия должны совпадать словами, чтобы считаться одним направлением.
#: ⚠️ Догадка: 0.5 сводит «homomorphic encryption hardware and protocols» с «fully
#: homomorphic encryption hardware acceleration» и не трогает соседние, но разные приёмы.
NEAR_LABEL = 0.5
#: Слова, которые есть в любом запросе про ИИ и потому темы не задают.
_NOT_A_TOPIC = frozenset({"ai", "artificial", "intelligence", "ии", "слабые", "сигналы"})


def topic_words(words: set[str]) -> set[str]:
    """Содержательные слова запроса: без рыночных и без вездесущего «ai».

    Ими `scripts/measure_ask.py` считает, сколько названий в ТОП ушло от темы. ⚠️ Как
    ключ ПОРЯДКА тематичность замерена и отвергнута: она не уменьшила офтопик заметно,
    а совпадений стало на одно меньше (docs/todo.md §71).
    """
    return {w for w in words if w not in MARKET_WORDS and w not in _NOT_A_TOPIC}


def _specificity(label: str, words: set[str]) -> int:
    """Сколько в названии слов СВЕРХ самого запроса и рыночного словаря, не больше трёх.

    ⚠️ Потолок обязателен: без него ключ премирует длинное название, а не точное.
    """
    rest = {w for w in significant(label) if w not in words and w not in MARKET_WORDS}
    return min(len(rest), 3)


def _stems(label: str) -> set[str]:
    """Значимые слова названия для сравнения близнецов.

    ⚠️ Основы слов (отсечение суффиксов) сюда НЕ подставлять: замерено — целевой дубль они
    сводят, но состав кандидатов на границе отсечения сдвигается и одно совпадение с
    датасетом теряется (docs/todo.md §75).
    """
    return set(significant(label))


def same_direction(a: str, b: str) -> bool:
    """Одно ли направление названо двумя строками: дословно или по значимым словам.

    Тот же приём, что у `_merge_near` (Жаккар значимых слов ≥ `NEAR_LABEL`): точное
    сравнение строк занижает совпадение — «security and governance for agentic ai» и
    «security infrastructure for agentic ai» названы иначе, а направление одно
    (docs/weak-measurements.md). Им же считается устойчивость между прогонами
    (`weak.history`) и пересечение ТОП в `scripts/measure_ask.py`.
    """
    if a.strip().lower() == b.strip().lower():
        return True
    mine, theirs = _stems(a), _stems(b)
    if not mine or not theirs:
        return False
    return len(mine & theirs) / len(mine | theirs) >= NEAR_LABEL


def _merge_near(cands: list[_Cand], words: set[str]) -> list[_Cand]:
    """Свести названия-близнецы, оставив БОЛЕЕ ТОЧНОЕ из них.

    ⚠️ Выживает самое специфичное, а не самое популярное: прежнее сведение оставляло то,
    за которым больше компаний, и частное направление («neuromorphic computing for edge
    ai») исчезало внутри общего — вместе с совпадением по датасету.
    """
    out: list[_Cand] = []
    for c in sorted(cands, key=lambda c: -_specificity(c.label, words)):
        mine = _stems(c.label)
        twin = None
        for other in out:
            theirs = _stems(other.label)
            if not mine or not theirs:
                continue
            if len(mine & theirs) / len(mine | theirs) >= NEAR_LABEL:
                twin = other
                break
        if twin is None:
            out.append(c)
            continue
        seen = {x.lower() for x in twin.companies}
        twin.companies += [x for x in c.companies if x.lower() not in seen]
        twin.headlines += c.headlines
        twin.members += [m for m in c.members if m not in twin.members]
        twin.cores = [k for k in dict.fromkeys([*twin.cores, c.core, *c.cores]) if k and k != twin.core]
    return out


def _merge_group(group: list[_Cand]) -> _Cand:
    """Один кандидат из группы вариантов одного направления: всё вещество — в него.

    Голова — ПЕРВЫЙ в группе: группы собираются в порядке `_merge_near`, то есть самое
    специфичное название идёт первым. ⚠️ Не наступить: пересортировка по числу компаний
    возвращала ровно ту эвристику, которую `_merge_near` отвергла замером, — общее имя,
    под которое подходит больше игроков, растворяло в себе частное (PEL-C-04).
    """
    head = group[0]
    return _Cand(
        label=head.label,
        members=list(dict.fromkeys(m for c in group for m in c.members)),
        stream=MONEY_STREAM if any(c.stream == MONEY_STREAM for c in group) else SCIENCE_STREAM,
        core=head.core,
        headlines=list({d.title: d for c in group for d in c.headlines}.values()),
        companies=list(dict.fromkeys(x for c in group for x in c.companies)),
        cores=[
            k
            for k in dict.fromkeys(k for c in group for k in [c.core, *c.cores])
            if k and k != head.core
        ],
    )


def _traced_core(c: _Cand, works: Callable[[str], int]) -> str:
    """Ядро для счёта: своё, а при нулевом следе — ядро варианта с самым большим следом.

    ⚠️ Ядро сведённой группы — ядро головы, и его формулировка случайна: у группы KYC/KYB
    выжило «automated KYC/KYB» без единой работы, и модель роста дала пол 0.11, хотя у
    варианта было ядро со следом (docs/todo.md §101). Замена идёт только при НУЛЕВОМ следе:
    правило «брать самое частое ядро» тянуло бы общее «machine learning».
    """
    if works(c.core) or not c.cores:
        return c.core
    best = max(c.cores, key=works)
    return best if works(best) else c.core


def _merge_twins(cands: list[_Cand]) -> list[_Cand]:
    """Свести близнецов, которых не видят слова: решает модель (`weak.twins`).

    Идёт после `_merge_near` и в её порядке — головой группы становится самое
    специфичное название. Общее ядро ставит пару перед моделью, но само не сводит:
    ⚠️ строковое сведение по ядру склеивало разные направления, потому что ядро зависит
    от соседей по пачке `core.cores` (docs/todo.md §101).
    """
    if len(cands) < 2:
        return cands
    labels = [c.label for c in cands]
    heads = twins.groups(labels, vectors_for(labels), [c.core for c in cands])
    by_head: dict[int, list[_Cand]] = {}
    for c, h in zip(cands, heads, strict=True):
        by_head.setdefault(h, []).append(c)
    return [g[0] if len(g) == 1 else _merge_group(g) for g in by_head.values()]


def _score_by_model(
    store: Store,
    signals: list[Signal],
    news_by_label: dict[str, list[LiveDoc]],
    trace: Callable[[str], Footprint],
    label_trace: Callable[[str], Footprint],
    today: date,
    say: Callable[[str], None],
) -> None:
    """Вероятность по признакам и три главных вклада — от модели `weak/model.json`.

    Это пунктирная стрелка схемы 1 ТЗ: модель, обученная на датасете (этап 1), питает
    скоринг открытого запроса (этап 2). Признаки — те же, что при обучении
    (`weak.dataset`): числа `N` по привязке, новостям и следу; атомарные чтения `J` из уже
    сделанной оценки (`Signal.judgments`, лишних вызовов модели нет); соседство в
    векторах `E` — только если оно есть в модели (ещё один проход по шардам).
    ⚠️ Расхождение с обучением одно и названо: датасет привязывался сырым русским
    названием, здесь ярлык английский (он и есть родной язык корпуса).
    """
    # ⚠️ Модели роста здесь нет: её признаки (`query_share*`, `backed`, `players_cap`)
    # считаются внутри запроса в `score`, а словарь ниже их не содержит — импутация медианой
    # давала всем одну вероятность (PEL-F-02). Рост считает `score`, и только он.
    params = model_mod.load()
    listed = model_mod.load(model_mod.LISTED_PARAMS) if LISTED_IN_ASK else None
    if (params is None and listed is None) or not signals:
        return
    # ⚠️ Победителю зоопарка могут быть нужны голоса LLM по плечам `papers` и `mixed`, а у
    # `ask` есть только одна оценка. Три оценки ради вероятности — +30 вызовов к демо
    # (docs/todo.md §93), поэтому такая модель в карточке не применяется вовсе.
    if params is not None and any(f in params["features"] for f in ("llm_papers", "llm_mixed")):
        say("вероятность по признакам: модели нужны голоса трёх плеч — в карточке не считается")
        params = None
    say(f"вероятность по признакам: привязка {len(signals)} названий")
    hits = ground(store, [s.label for s in signals], k=ARM_EVIDENCE)
    ev = evidence_for(store, sorted({h.signal_id for found in hits for h in found}))
    nbrs: list = [None] * len(signals)
    wanted = [f for p in (params, listed) if p is not None for f in p["features"]]
    if any(f in wanted for f in EMBED_FEATURES):
        say("соседство в векторах для пула (проход по шардам)...")
        nbrs, _cov, _ = neighbourhoods(store, [s.label for s in signals], today, say=say)
    year_before: dict[str, Footprint] = {}
    if listed is not None:
        # Momentum ядра: работ за последний год к работам за год до того — второй след
        # под срезом «год назад» (кэш `weak.core` ключуется срезом). Ярлыки не считаются:
        # их след дорог (docs/todo.md §93), и `label_momentum` идёт медианой обучения.
        held = asof.AS_OF
        asof.AS_OF = today - timedelta(days=365)
        try:
            year_before = footprints(store, sorted({s.core for s in signals if s.core}))
        finally:
            asof.AS_OF = held
    # Денежный след ДО сегодняшнего дня — тем же инструментом и с той же арифметикой, что
    # признаки `$` в обучении (`weak.rounds.features`, docs/weak-audit.md): иначе модель
    # видит в бою не то, на чём училась. Второе окно («год до того») — ещё столько же
    # запросов, берётся только если модели нужны `money_prev_n` / `money_momentum`.
    dollars: list[dict[str, float | None]] = [{} for _ in signals]
    industry: dict[str, float | None] = {}
    if listed is not None and any(f in listed["features"] for f in rounds.MONEY_FEATURES):
        with_prev = any(f in listed["features"] for f in ("money_prev_n", "money_momentum"))
        try:
            dollars = rounds.features([s.label for s in signals], today, say, with_prev=with_prev)
        except rounds.MoneyUnchecked as exc:
            # Не измерено — признаки `$` пустые («измерить нечем»), а не нулевые.
            say(f"денежный след не измерен: {exc}")
    if listed is not None and "industry_share" in listed["features"]:
        industry = industry_share(store, sorted({s.core for s in signals if s.core}))
    for sig, found, nbr, money_feats in zip(signals, hits, nbrs, dollars, strict=True):
        kept = [h for h in found if h.signal_id in ev]
        row = Row(
            "top",
            sig.label,
            sig.label,
            hits=kept,
            papers=[ev[h.signal_id] for h in kept],
            news=news_by_label.get(sig.label, []),
            core_print=trace(sig.core),
            label_print=label_trace(sig.label),
            nbr=nbr,
        )
        feats = {
            **features(row, today),
            **llm_features({"corpus": (sig.kind, sig.stage)}),
            **{k: sig.judgments.get(k) for k in JUDGMENT_FEATURES},
            **embed_features(row),
        }
        if params is not None:
            pred = model_mod.probability(feats, params)
            sig.probability = pred.probability
            sig.predictors = pred.predictors
        if listed is not None:
            now_fp, before_fp = row.core_print, year_before.get(sig.core)
            momentum = None
            if before_fp is not None and (now_fp.last_year_works or before_fp.last_year_works):
                momentum = (now_fp.last_year_works + 1) / (before_fp.last_year_works + 1)
            feats.update(emergence_features(sig, momentum, today))
            feats.update({**money_feats, "industry_share": industry.get(sig.core)})
            lp = model_mod.probability(feats, listed)
            sig.listed = lp.probability
            sig.listed_predictors = lp.predictors


def emergence_features(
    sig: Signal, core_momentum: float | None, today: date
) -> dict[str, float | None]:
    """Признаки эмерджентности сверх наборов `weak.dataset` — те, что модели роста и списка
    получают от самой выдачи (`scripts/backtest_classifier.py` считает их теми же именами).

    ⚠️ Имена — единственное, чем обучение и выдача связаны: разошлось имя — `probability`
    подставит медиану импутации и МОЛЧА посчитает по пустому признаку. Отсюда отдельная
    функция и тест на её ключи против установленного артефакта.
    """
    return {
        "players": float(len(sig.companies)),
        "money_stream": 1.0 if sig.stream == MONEY_STREAM else 0.0,
        "low_visibility": 1.0 if sig.low_visibility else 0.0,
        "core_age_at_cut": (
            float(max(today.year - sig.core_since, 0)) if sig.core_since else None
        ),
        "core_momentum": core_momentum,
        # След ЯРЛЫКА за год до того не считается: он дорог (docs/todo.md §93), и модель
        # получает его медианой обучения.
        "label_momentum": None,
    }


def _on_topic(
    ranked: list[tuple[Signal, list[Evidence]]],
    area: str,
    result: AskResult,
    target: int | None = None,
) -> list[tuple[Signal, list[Evidence]]]:
    """Снять кандидатов, чьи свидетельства не о предмете запроса (`weak.topic`).

    Отдельным вызовом, а не строкой в промпте оценки: там модель соблюдала её через раз.
    ⚠️ Идёт ПО ПОРЯДКУ ТОП и останавливается, когда набрано `TOP` тематичных: проверять
    хвост, который в ТОП всё равно не войдёт, — это ~2 с модели на кандидата, а запрос и так
    у потолка 1200 с (docs/todo.md §100). Непроверенный хвост уходит в «за пределами ТОП».
    ⚠️ Сбой проверки кандидата не снимает: без ответа модели «не по теме» не доказано.
    """
    # `TOP` — в момент вызова, не при определении: со слоями цель шире (`LAYER_POOL`).
    target = TOP if target is None else target
    kept: list[tuple[Signal, list[Evidence]]] = []
    for i, (sig, ev) in enumerate(ranked):
        if len(kept) >= target:
            return kept + ranked[i:]
        try:
            grade, why = topic.parse(
                asyncio.run(
                    _one(
                        topic.SYSTEM_PROMPT,
                        topic.build_prompt(area, sig.label, ev),
                        topic.schema(),
                        "topic",
                        200,
                    )
                )
            )
        except (LLMError, ValueError, httpx.HTTPError):
            grade, why = topic.RELEVANT_MIN, ""
        else:
            # Оценка остаётся подписью в выдаче: «прямо о предмете» (3) против «смежное» (2).
            sig.relevance, sig.relevance_why = grade, card_mod.drop_foreign(why)
        if grade >= topic.RELEVANT_MIN:
            kept.append((sig, ev))
            continue
        sig.kind, sig.kind_label = NOISE, kind_label(NOISE)
        sig.why = card_mod.drop_foreign(why) or f"Свидетельства не о предмете запроса «{area}»."
        sig.checks[0] = Check("жанр: зарождающаяся", False, "другая отрасль", entry=True)
        result.excluded.append(sig)
        result.off_topic += 1
    return kept


#: Сколько тематичных кандидатов набирать под слои: часть уйдёт в «наблюдать», и ТОП-15
#: должен набраться из остальных. ⚠️ Догадка; цена — патенты на весь пул (~3.5 мин на 15).
#: Весь пул кандидатов: при 30 после проверки тематичности у «космоса» оставалось 19, и
#: ТОП-15 не набирался.
LAYER_POOL = CANDIDATES
#: Сколько «зарождающихся» показывать блоком «наблюдать». ⚠️ Догадка.
WATCH_MAX = 10


def _drop_trends(
    shown: list[tuple[Signal, list[Evidence]]], result: AskResult
) -> list[tuple[Signal, list[Evidence]]]:
    """«Уже тренд» — в исключённые с причиной, до тематичности и патентов: для этого слоя
    хватает прочитанного (крупные игроки с продажами, лидеры рынка) — `weak.layers`."""
    kept = []
    for sig, ev in shown:
        lay = layers_mod.classify(sig.companies, sig.judgments, sig.label_works or 0, None)
        if lay.name != layers_mod.TREND:
            kept.append((sig, ev))
            continue
        sig.layer, sig.layer_reason = lay.name, lay.reason
        sig.kind, sig.kind_label = MATURE, "уже тренд"
        sig.why = f"Уже тренд: {lay.reason}."
        result.excluded.append(sig)
    return kept


def _techniques(
    shown: list[tuple[Signal, list[Evidence]]], result: AskResult
) -> list[tuple[Signal, list[Evidence]]]:
    """Приём за направлением (`weak.technique`); не назван в свидетельствах — «слишком
    общая категория» с причиной. ⚠️ Сбой вызова кандидата не снимает."""
    kept = []
    for sig, ev in shown:
        try:
            category, found = technique_mod.parse(
                asyncio.run(
                    _one(
                        technique_mod.SYSTEM_PROMPT,
                        technique_mod.build_prompt(sig.label, ev),
                        technique_mod.schema(),
                        "technique",
                        160,
                    )
                ),
                ev,
            )
        except (LLMError, ValueError, httpx.HTTPError):
            kept.append((sig, ev))
            continue
        sig.category, sig.technique = category, found
        if found:
            kept.append((sig, ev))
            continue
        sig.kind, sig.kind_label = TOO_BROAD, kind_label(TOO_BROAD)
        sig.why = (
            f"Слишком общая категория: «{category or sig.label}» — в свидетельствах не назван "
            "приём, которого нет у обычных работ этой категории."
        )
        result.excluded.append(sig)
    return kept


def excluded_why(kind: str, judgments: dict[str, float]) -> str:
    """Причина исключения, когда объяснение модели снято (нет ссылки `[n]` — `assess.parse`).

    BR-03: каждый исключённый — с причиной. Слова модели без ссылки показывать нельзя, а
    признаки оценки (`assess.JUDGMENTS`) — её же ответ по тем же свидетельствам, только
    атомарный; их и называем. У ТОП (`EMERGING`) пустое объяснение остаётся пустым — там оно
    проверка уверенности «объяснение со ссылками», а не подпись.
    """
    if kind == EMERGING:
        return ""
    seen = [
        FEATURE_LABELS[k] + (f": {v:.0f}" if k == "players_named" else "")
        for k, v in judgments.items()
        if v and k in FEATURE_LABELS and k != "novelty_claimed"
    ]
    tail = "Объяснение оценщика без ссылки на свидетельство не показано."
    return f"По признакам оценки: {'; '.join(seen)}. {tail}" if seen else tail


def _split_layers(
    shown: list[tuple[Signal, list[Evidence]]],
    result: AskResult,
    say: Callable[[str], None],
    deadline: float | None = None,
) -> list[tuple[Signal, list[Evidence]]]:
    """Слабый сигнал — в ТОП, зарождающееся — в «наблюдать» (`weak.layers`).

    Патенты нужны слою как подтверждение, поэтому считаются на весь пул `LAYER_POOL`, а не
    на 15 показанных. Порядок внутри слоя — прежний (скоринг). `deadline` — тот же срок,
    что у патентов обычного пути (BR-33): кандидат, до которого не дошли, слоится без них.
    """
    pool, rest = shown[:LAYER_POOL], shown[LAYER_POOL:]
    attach_patents(pool, result.live_errors, say, deadline=deadline)
    if flags.EXTRA_SOURCES:
        attach_extra(pool, result.live_errors, say)
    weak, watch = [], []
    # Рост патентов сравнивается с медианой пула: растёт весь патентный поток, и «рост
    # вообще» подтверждал бы почти всех (на запросах ТЗ медиана пула — ×1.3…×2.0).
    growths = [s.patents_growth for s, _ in pool if s.patents_growth is not None]
    floor = _median_float(growths) if growths else 1.0
    for sig, ev in pool:
        lay = layers_mod.classify(
            sig.companies, sig.judgments, sig.label_works or 0, sig.patents_growth, sig.extra,
            growth_floor=floor,
        )
        sig.layer, sig.layer_reason = lay.name, lay.reason
        (weak if lay.name == layers_mod.WEAK else watch).append((sig, ev))
    # Добор до 15: ТЗ требует пятнадцать позиций, а слабых бывает меньше (у «космоса» 14).
    # Недостающие места — лучшим из «наблюдать», со слоем и причиной на карточке: позиция
    # есть, но за слабый сигнал она себя не выдаёт (docs/todo.md §117).
    need = max(TOP - len(weak), 0)
    for sig, _ in watch[:need]:
        sig.layer_reason = "добор до пятнадцати позиций: " + sig.layer_reason
    fill, watch = watch[:need], watch[need:]
    result.watch = [s for s, _ in watch[:WATCH_MAX]]
    # Сверх «наблюдать» — в «за пределами ТОП», а не в никуда: отсев считается вслух.
    return weak + fill + watch[WATCH_MAX:] + rest


#: Тип свидетельства для документов `weak.extra` в карточке.
EXTRA_EV = {
    extra_mod.NIH: "GRANT",
    extra_mod.PREPRINT: PAPER,
    extra_mod.TRIALS: "TRIAL",
    extra_mod.YC: NEWS,
}


def attach_extra(
    shown: list[tuple[Signal, list[Evidence]]],
    live_errors: dict[str, str],
    say: Callable[[str], None] = lambda _m: None,
) -> None:
    """Гранты, препринты, клинические испытания и стартапы YC — в карточку и в счёт
    подтверждений слоя (`weak.extra`). Как патенты: ПОСЛЕ оценки зрелости, в балл не идёт.

    ⚠️ Свидетельство и источник — в ногу: номера в карточке общие.
    """
    if not shown:
        return
    say(f"гранты, препринты, испытания, стартапы: {len(shown)}")
    got = extra_mod.gather([(s.core or s.label).strip() for s, _ in shown])
    silent: dict[str, int] = {}
    for (sig, ev), found in zip(shown, got, strict=True):
        sig.extra = {src: len(found.found(src)) for src in extra_mod.SOURCES}
        for src in found.unmeasured():
            silent[src] = silent.get(src, 0) + 1
        for src in extra_mod.SOURCES:
            for d in found.found(src):
                kind = EXTRA_EV[src]
                ev.append(Evidence(-1, d.published, d.title, kind, d.url, d.publisher, d.snippet))
                sig.sources.append(_live_source(len(sig.sources) + 1, d, kind))
    # ⚠️ Отказ виден, как у патентов: без строки «не ответил» ноль грантов неотличим от сбоя.
    for src, n in silent.items():
        live_errors[src] = f"не ответил по {n} из {len(shown)} кандидатов"


def attach_patents(
    shown: list[tuple[Signal, list[Evidence]]],
    live_errors: dict[str, str],
    say: Callable[[str], None] = lambda _m: None,
    deadline: float | None = None,
) -> None:
    """Патентная строка и свежие патенты — в карточку и в свидетельства, в ногу.

    `deadline` — `time.monotonic()`, после которого новых запросов к OPS нет; кандидаты,
    до которых не дошли, остаются без патентной строки, а причина — в `live_errors`.
    """
    if not (patents_mod.enabled() and shown):
        return
    say(f"патентная активность: {len(shown)}")
    phrases = sorted({(s.core or s.label).strip() for s, _ in shown})
    try:
        acts = patents_mod.activity(phrases, deadline=deadline)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        # Последняя страховка: патенты идут на 12–16-й минуте, и строка карточки не стоит
        # потерянного прогона. Обычные отказы `patents` закрывает сам (`closed()`).
        live_errors["patents"] = f"не ответил: {type(exc).__name__}"
        return
    for sig, ev in shown:
        act = acts.get((sig.core or sig.label).strip())
        if act is None:
            continue
        sig.patents = act.line()
        sig.patents_recent = act.recent if act.measured else None
        sig.patents_growth = act.growth
        for d in act.docs:
            # Свидетельство и источник — в ногу: номера в карточке общие, и сдвиг
            # здесь разъехался бы по всем ссылкам.
            ev.append(Evidence(-1, d.published, d.title, PATENT_EV, d.url, d.publisher))
            sig.sources.append(_live_source(len(sig.sources) + 1, d, PATENT_EV))
    # ⚠️ Отказ патентного источника обязан быть ВИДЕН: он приезжает в тот же список
    # отказов источников, что и остальные. Молчаливая деградация здесь хуже
    # отсутствующего источника — карточка без патентной строки неотличима от
    # карточки, у которой патентов нет.
    if patents_mod.closed():
        live_errors["patents"] = patents_mod.closed()


def score(pool: list[Signal], say: Callable[[str], None] = lambda _m: None) -> bool:
    """Скоринг схемы 1 ТЗ: уверенность модели и ключевые предикторы у каждого кандидата пула.

    Модель — `weak/growth.json`, обученная на трёх срезах бэктеста предсказывать рост
    научного следа ядра сверх области за два года (`scripts/train_growth_model.py
    --in-query`); признаки — `model.QUERY_FEATURES`, посчитанные внутри ЭТОГО запроса той же
    функцией, что при обучении. Проверка будущим (обучение на двух срезах, проверка на
    третьем): AUC 0.83 / 0.90 / 0.83 (± 0.06–0.09) при случайном 0.60–0.61 (docs/weak-audit.md).
    Пишет `confidence` (вероятность, калибровка Платта) и `predictors` (вклады со знаком).
    Все четыре признака уже посчитаны к этому месту: прохода по корпусу скоринг не стоит.

    `False` — модели нет, её правило решения провалено или она ждёт других признаков:
    тогда `confidence` остаётся долей проверок, а порядок — `top_key`.
    """
    params = model_mod.load(model_mod.GROWTH_PARAMS)
    if params is None or not pool or not set(params["features"]) <= set(model_mod.QUERY_FEATURES):
        return False
    say(f"скоринг моделью роста: {len(pool)} кандидатов")
    feats = model_mod.query_features(
        [
            {
                "core_last_year_share": s.core_recent_share,
                "core_works": float(s.core_works),
                "players": float(len(s.companies)),
                "money_stream": 1.0 if s.stream == MONEY_STREAM else 0.0,
                **{src: s.judgments.get(src) for src in model_mod.COMMERCE_SOURCE.values()},
            }
            for s in pool
        ]
    )
    for s, f in zip(pool, feats, strict=True):
        pred = model_mod.probability(f, params)
        s.confidence = pred.probability
        s.predictors = pred.predictors
    return True


def top_key(s: Signal, prior: float) -> tuple:
    """Ключ порядка ТОП одной выдачи, когда модели скоринга нет (и тай-брейк при ней).

    Вынесен из `run()`, чтобы замер и тесты звали ту же функцию, а не повторяли её у себя:
    ключ уже трижды менялся, и переписанный в тесте он проверял прошлую версию.

    **Нулевой ключ** — «за направлением кто-то стоит»: денежный поток или хотя бы один
    игрок. На пулах бэктеста он ничего не стоит (покрытие то же), но научный поток без
    игроков — это конфигурация одной статьи («goal auditing system», 1 работа, доля 1.0),
    и в живом ТОП-15 такие занимали треть мест; список заказчика среди них — 4 из 27.

    **Первый ключ** — доля работ ядра за последний год, сжатая к медиане пула: единственный
    признак, устойчивый между эпохами (AUC 0.71 / 0.80 / 0.80 по избыточному росту). Игроки,
    видимость и уверенность — только при равенстве.

    Вероятность модели списка (`Signal.listed`) идёт ключом только при `LISTED_IN_ASK`
    (замер); выключенная, она — подпись в карточке и порядка не трогает.
    """
    listed = s.listed if LISTED_IN_ASK and s.listed is not None else -1.0
    return (
        not (s.stream == MONEY_STREAM or s.companies),
        -listed,
        -rank_share(s, prior),
        -min(len(s.companies), PLAYERS_CAP),
        not s.low_visibility,
        -s.confidence,
        -s.recent_share,
    )


def rank_share(s: Signal, prior: float) -> float:
    """Доля свежих работ ядра, сжатая к априорной доле пула на `SHARE_PRIOR_WEIGHT` работ;
    −1 — измерить нечем (в хвост)."""
    if s.core_recent_share is None:
        return -1.0
    m = SHARE_PRIOR_WEIGHT
    return (s.core_recent_share * s.core_works + m * prior) / (s.core_works + m)


def _median_float(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return float(ordered[mid]) if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def answer_models() -> list[dict[str, str]]:
    """Какие модели участвуют в ответе и в какой роли — для страницы, JSON и лога.

    Выбор фиксирован конфигурацией (`LLM_MODEL`, `EMBEDDING_MODEL`) и установленными
    весами обученных моделей; автоматического выбора модели на ответ нет.
    """
    out = [
        {
            "role": "генерация (локально): направления запроса, приёмы в работах, ядра, "
            "направления из новостей, компании, оценка зрелости, тематичность, слияние дублей, "
            "карточки, гипотезы, резюме источников, русские названия исключённых",
            "model": settings.llm_model,
            "endpoint": settings.llm_base_url,
        },
        {
            "role": "эмбеддинги (локально): поиск по научному корпусу, сведение названий",
            "model": settings.embedding_model,
            "endpoint": settings.llm_base_url,
        },
    ]
    trained = [
        (
            (
                "скоринг (уверенность модели и порядок ТОП): обученная модель роста научного "
                "следа ядра, признаки внутри запроса"
            ),
            model_mod.GROWTH_PARAMS,
        ),
    ]
    if LISTED_IN_ASK:
        trained.append(("обученная модель списка заказчика", model_mod.LISTED_PARAMS))
    for role, path in trained:
        if model_mod.load(path) is not None:
            out.append({"role": role, "model": f"логистическая регрессия ({path.name})",
                        "endpoint": "в процессе приложения"})
    return out


#: Ответ модели на карточку: четыре раздела, гипотеза, хронология и резюме источников.
#: ⚠️ Догадка с запасом (docs/todo.md §99): обрезка ответа не мерилась; +200 под гипотезу,
#: +300 под «почему слабый сигнал» с цитатой.
CARD_TOKENS = 2100


def deepen_top(
    shown: list[tuple[Signal, list[Evidence]]],
    errors: dict[str, str],
    say: Callable[[str], None] = lambda _m: None,
) -> None:
    """Досбор текстов для карточек показанных (`weak.deepen`). Изменяет пары на месте.

    ⚠️ Свидетельство и источник добавляются В НОГУ: номера в карточке общие.
    """
    say(f"углубление: аннотации и фактовые новости по {len(shown)}")
    urls = [e.url for _, ev in shown for e in ev if e.kind == PAPER and e.url]
    got = deepen_mod.abstracts(urls, errors)
    for _, ev in shown:
        for e in ev:
            if e.url in got and not e.detail:
                e.detail = got[e.url][: deepen_mod.ABSTRACT_CHARS]
    seen = [{s.title for s in sig.sources} for sig, _ in shown]
    fresh = deepen_mod.fact_news([(sig.core or sig.label).strip() for sig, _ in shown], seen)
    for (sig, ev), docs in zip(shown, fresh, strict=True):
        for d in docs:
            ev.append(Evidence(-1, d.published, d.title, NEWS, d.url, d.publisher, d.snippet))
            sig.sources.append(_live_source(len(sig.sources) + 1, d, NEWS))


def write_cards(
    shown: list[tuple[Signal, list[Evidence]]], say: Callable[[str], None] = lambda _m: None
) -> None:
    say(f"пишу карточки: {len(shown)}")
    for sig, ev in shown:
        for s, e in zip(sig.sources, ev, strict=False):
            s.excerpt = e.detail
        try:
            payload = asyncio.run(
                _one(
                    card_mod.SYSTEM_PROMPT,
                    card_mod.build_prompt(sig.title, ev),
                    card_mod.schema(),
                    "card",
                    CARD_TOKENS,
                )
            )
            c = card_mod.parse(payload, sig.title, ev)
        except (LLMError, ValueError, httpx.HTTPError):
            continue
        sig.title, sig.description, sig.advantage, sig.case = (
            c.title,
            c.description,
            c.advantage,
            c.case,
        )
        sig.facts = [{"date": d, "text": t} for d, t in c.facts]
        sig.hypothesis = c.hypothesis
        # Объяснение карточки — «почему слабый сигнал», по полным текстам свидетельств; у
        # оценки зрелости оно про жанр и стадию и пустеет без ссылки. Пусто у карточки —
        # остаётся объяснение оценки.
        if c.why:
            sig.why, sig.why_quote = c.why, c.why_quote
        for s in sig.sources:
            s.summary_ru = c.summaries.get(s.n, "")
            s.machine_summary = bool(s.summary_ru)


RU_TITLES_PROMPT = """Translate each English technology name into Russian, 3 to 10 words.
Keep established English terms (LLM, MCP, TEE, RAG) as they are. No explanation, no quotes.
Answer for every item, in the given order."""

#: Названий за один вызов: исключённых и не вошедших обычно 15–30 — один-два вызова.
RU_TITLES_BATCH = 25


def _ru_titles_schema(n: int) -> dict:
    items = {"type": "array", "items": {"type": "string"}, "minItems": n, "maxItems": n}
    return {
        "type": "object",
        "properties": {"items": items},
        "required": ["items"],
        "additionalProperties": False,
    }


#: Номер пункта, который модель повторяет из позиционного промпта («14. Ridge Security…»).
_ECHOED_NUMBER = re.compile(r"^\d+[.)]\s+")


def _positional_text(raw: object) -> str:
    """Строка позиционного ответа: пробелы схлопнуты, повторённый номер пункта срезан."""
    return _ECHOED_NUMBER.sub("", " ".join(str(raw).split()))


def russian_titles(sigs: list[Signal], say: Callable[[str], None] = lambda _m: None) -> None:
    """Русские названия исключённых и не вошедших в ТОП (ТЗ: вся выдача на русском).

    Карточку им не пишут — это дорого, а название нужно, чтобы список исключений читался.
    Сюда же идут карточки ТОП, чей заголовок остался латиницей. Раскладка позиционная (как
    `weak.translate`). Ответ без кириллицы («перевод», повторивший английский) или с чужим
    письмом не берётся; непереведённые спрашиваются ещё раз одним вызовом, после него
    остаётся английский ярлык: он хотя бы верен.
    """
    pending = list(sigs)
    if not pending:
        return
    say(f"перевожу названия исключённых: {len(pending)}")
    for _attempt in range(2):
        failed: list[Signal] = []
        for start in range(0, len(pending), RU_TITLES_BATCH):
            part = pending[start : start + RU_TITLES_BATCH]
            user = "\n".join(f"{i}. {s.label}" for i, s in enumerate(part, 1))
            try:
                got = asyncio.run(
                    _one(RU_TITLES_PROMPT, user, _ru_titles_schema(len(part)), "ru_titles", 60 * len(part))
                )
            except (LLMError, ValueError, httpx.HTTPError):
                failed += part
                continue
            items = got.get("items") or []
            for i, sig in enumerate(part):
                ru = _positional_text(items[i]) if i < len(items) else ""
                if ru and card_mod.has_cyrillic(ru) and not card_mod.foreign_script(ru):
                    sig.title = ru
                else:
                    failed.append(sig)
        if not failed:
            return
        pending = failed


RU_SUMMARY_PROMPT = """For each numbered source write ONE Russian sentence (10 to 30 words)
saying what the source reports, using ONLY its title and excerpt. Keep names, numbers and
established English terms as they are. No explanation, no quotes.
Answer for every item, in the given order."""

#: Источников за один вызов. ⚠️ Догадка, не замер (docs/todo.md §120): ответ — ~40 токенов
#: на источник, 20 укладываются в потолок одного вызова с запасом.
RU_SUMMARY_BATCH = 20
#: Сколько символов выдержки видит модель на источник: на одну фразу хватает начала.
RU_SUMMARY_CHARS = 400


def russian_summaries(
    sigs: list[Signal], errors: dict[str, str], say: Callable[[str], None], deadline: float
) -> None:
    """Русское резюме каждому зарубежному источнику, у которого его не написала карточка.

    ТЗ: «для зарубежных материалов сервис должен предоставлять русскоязычное резюме…
    при использовании генеративного резюме необходимо отметить это возле источника».
    Карточка пишет резюме только процитированным источникам (`card.SYSTEM_PROMPT`), а
    таблица источников показывает все — остальным оно добирается здесь, пакетом, с той же
    пометкой `machine_summary`. Раскладка позиционная (как `russian_titles`); сбой вызова или
    чужое письмо — резюме нет, и страница так и пишет. Дедлайн запроса — граница: не
    успели — пометка в `errors`, а не превышение потолка заказчика.
    """
    todo = [s for sig in sigs for s in sig.sources if s.lang != "ru" and not s.summary_ru]
    if not todo:
        return
    say(f"русские резюме зарубежных источников: {len(todo)}")
    for start in range(0, len(todo), RU_SUMMARY_BATCH):
        if time.monotonic() >= deadline:
            errors["summaries"] = (
                f"русское резюме не дописано у {len(todo) - start} зарубежных источников: "
                "исчерпан бюджет времени запроса"
            )
            return
        part = todo[start : start + RU_SUMMARY_BATCH]
        user = "\n".join(
            f"{i}. {s.title}" + (f" — {s.excerpt[:RU_SUMMARY_CHARS]}" if s.excerpt else "")
            for i, s in enumerate(part, 1)
        )
        try:
            got = asyncio.run(
                _one(RU_SUMMARY_PROMPT, user, _ru_titles_schema(len(part)), "ru_summaries", 80 * len(part))
            )
        except (LLMError, ValueError, httpx.HTTPError):
            continue
        for src, ru in zip(part, got.get("items") or [], strict=False):
            ru = _positional_text(ru)
            if ru and not card_mod.foreign_script(ru):
                src.summary_ru, src.machine_summary = ru, True


def assign_facets(
    cands: list[_Cand], facets: list[str], hits: list[list], by_tech: dict[str, list[int]]
) -> dict[str, int]:
    """Ярлык кандидата → направление запроса (индекс в `facets`): сектор радара.

    Научному кандидату — фасет, чья привязка (`ground`) дала больше всего работ его
    вариантам: так он и был найден. Денежному и научному без таких работ — ближайший фасет
    по косинусу векторов того же эмбеддера (`vectors_for`; ярлык английский, фасет русский —
    эмбеддер многоязычный). Порога нет, это argmax: сектор у радара есть всегда.
    ⚠️ Качество привязки не замерено (docs/todo.md §119). На отбор, порядок и уверенность
    направление не влияет. Сбой эмбеддера — кандидат без направления, а не угаданный сектор.
    """
    by_facet = [{h.signal_id for h in found} for found in hits]
    out: dict[str, int] = {}
    rest: list[_Cand] = []
    for c in cands:
        works = {sid for t in c.members for sid in by_tech.get(t, [])}
        counts = [len(works & f) for f in by_facet]
        if counts and max(counts) > 0:
            out[c.label] = counts.index(max(counts))
        else:
            rest.append(c)
    if not rest or not facets:
        return out
    try:
        vectors = vectors_for(list(facets) + [c.label for c in rest])
    except (httpx.HTTPError, ValueError, KeyError):
        return out
    near = (vectors[len(facets) :] @ vectors[: len(facets)].T).argmax(axis=1)
    out.update({c.label: int(k) for c, k in zip(rest, near, strict=True)})
    return out


def _event_signal(s: Signal) -> dict[str, Any]:
    """Вердикт по кандидату для экрана ожидания: то, что видно точкой и строкой ленты."""
    return {
        "label": s.label,
        "title": s.title,
        "kind": s.kind,
        "kind_label": s.kind_label,
        "stage": s.stage,
        "why": s.why,
        "companies": s.companies[:4],
        "stream": s.stream,
        "facet": s.facet,
        "sources": len(s.sources),
    }


def run(
    store: Store, query: str, on_progress: ProgressHook = None, on_event: EventHook = None
) -> AskResult:
    say = on_progress or (lambda _msg: None)
    emit = events.checked(on_event) if on_event else (lambda _kind, _data: None)

    def stage(sid: str) -> None:
        emit("stage", events.stage(sid))

    t0 = datetime.now(UTC)
    # Бюджет времени — от старта прогона: ни одна стадия сама не знает, сколько уже прошло.
    deadline = time.monotonic() + BUDGET_S
    # Под срезом «сегодня» — это дата среза: иначе свежесть считалась бы от нашего дня.
    today = asof.today()

    stage("expand")
    say("раскрываю запрос в направления")
    ex = expand(query)
    facets, query_en, area_en, facets_en = ex.facets, ex.query_en, ex.area_en, ex.facets_en
    bursts: list[str] = []
    if flags.DATED_FACETS and asof.active():
        # Поиск — английскими фразами: русские пары отсеянных направлений не выровнять со
        # всплеском, а пробник мерил именно английские (`dated_facets`).
        say("сверяю направления с корпусом на дату среза")
        facets_en = dated_facets(
            store, facets_en, [b.phrase for b in burst_mod.facets(store, [query_en, area_en], today)]
        )
        facets = facets_en or facets
    if flags.BURST:
        # Направления модели называют уже известное ей; всплеск в корпусе — то, чего она не
        # видела (`weak.burst`, docs/todo.md §112). Область — словами запроса, не фасетами.
        say("ищу всплески в корпусе")
        bursts = [b.phrase for b in burst_mod.facets(store, [query_en, area_en], today)]
        if flags.BURST == "only" and bursts:
            facets, facets_en = bursts, bursts
        else:
            facets = facets + bursts
    result = AskResult(
        query,
        query_en,
        facets,
        t0.isoformat(timespec="seconds"),
        model=settings.llm_model,
        embedder=settings.embedding_model,
        models=answer_models(),
        version=REPORT_VERSION,
    )

    result.bursts = bursts
    emit(
        "facets",
        {
            "facets_ru": list(facets),
            "facets_en": list(facets_en),
            "submarkets": list(ex.submarkets_en),
            "query_en": query_en,
        },
    )
    stage("science")
    say(f"ищу по локальному корпусу: {len(facets)} направлений")
    hits = ground(store, facets, k=PER_FACET)
    ids = sorted({h.signal_id for found in hits for h in found})
    result.works_found = len(ids)

    say(f"называю технологию в {len(ids)} работах")
    _named, tech_stats = run_tech(store, ids=ids)
    # ⚠️ Отказы разметки и сдвиги раскладки — в отчёт: работа без приёма из-за упавшего
    # пакета иначе неотличима от работы, где приёма нет.
    result.tech_stats = {
        "pending": tech_stats.pending,
        "failed_batches": tech_stats.failed_batches,
        "echo_swap": tech_stats.echo_swap,
        "unreachable": tech_stats.unreachable,
    }
    mention = _mentions(store, ids)
    result.works_named = len(mention)
    emit("counts", {"works": result.works_found, "works_named": result.works_named})
    papers = _papers(store, ids)

    # сведение вариантов написания — только внутри этого запроса
    by_tech: dict[str, list[int]] = {}
    for sid, tech in mention.items():
        by_tech.setdefault(tech, []).append(sid)
    texts = sorted(by_tech)
    cands: list[_Cand] = []
    if texts:
        vectors = vectors_for(texts)
        groups = build_groups(texts, vectors, {t: (len(by_tech[t]), 0) for t in texts}, THRESHOLD)
        grouped = {t for g in groups for t in (*g.members, *g.border)}
        # Одиночки тоже кандидаты: слабый сигнал по построению редок, и требовать
        # повтора в выборке из ста работ значило бы выбросить именно его.
        singles = [t for t in texts if t not in grouped]
        pool = [(g.label, [*g.members, *g.border]) for g in groups] + [(t, [t]) for t in singles]
        # Ядра считаются не для всех: ~1 с модели на 12 ярлыков, а после сотни работ
        # ярлыков бывает под сотню. Потолок — по числу работ, и это единственное место,
        # где частота ярлыка вообще участвует в отборе.
        pool.sort(key=lambda item: -sum(len(by_tech[t]) for t in item[1]))
        cands = [
            _Cand(label, members, SCIENCE_STREAM)
            for label, members in pool[: CORE_POOL - MONEY_POOL]
        ]

    words = query_words(query_en, area_en, *facets_en)
    stage("money")
    say("денежный поток: направления, за которыми стоят несколько компаний")
    money, seen_headlines, money_cover = money_candidates(
        money_queries(area_en, facets_en, ex.submarkets_en), say, area=area_en
    )
    money = [m for m in money if m.label not in by_tech]
    # Очередь в оценку: сперва coherence (несколько игроков), потом СПЕЦИФИЧНОСТЬ имени.
    # ⚠️ Премия за число компаний ограничена сверху, иначе она премирует расплывчатость:
    # под общее название («enterprise ai platforms») подходит больше компаний, чем под
    # частное («сканеры MCP-серверов»), и при большом пуле общее вытесняет частное.
    # Тот же потолок стоит в порядке ТОП — он и там заведён по этой причине.
    money.sort(
        key=lambda m: (
            -min(len({c.lower() for c in m.companies}), PLAYERS_CAP),
            -_specificity(m.label, words),
            -len(m.headlines),
        )
    )
    say(f"денежный поток: {len(money)} направлений из {seen_headlines} заголовков")
    emit("counts", {"headlines": seen_headlines, "directions": len(money)})
    money = money[:MONEY_POOL]
    if money:
        # Работы корпуса к названию из заголовка — той же привязкой, что у фасетов.
        # Работ берётся больше, чем пойдёт в свидетельства: по ним считается
        # научная опора, и потолок в две работы сделал бы проверку невыполнимой.
        found = ground(store, [m.label for m in money], k=PAPERS_FOR_CHECK)
        extra = sorted({h.signal_id for f in found for h in f} - set(papers))
        papers.update(_papers(store, extra))
        for m, f in zip(money, found, strict=True):
            by_tech[m.label] = [h.signal_id for h in f]
    cands += [
        _Cand(
            m.label,
            [m.label],
            MONEY_STREAM,
            headlines=list(m.headlines),
            companies=list(m.companies),
        )
        for m in money
    ]
    if not cands:
        if note := money_cover.note():
            result.live_errors["google_news_money"] = note
        result.seconds = (datetime.now(UTC) - t0).total_seconds()
        return result

    stage("merge")
    say(f"свожу {len(cands)} кандидатов к ядру технологии")
    for c, core in zip(cands, cores([c.label for c in cands]), strict=True):
        c.core = core
    cands = _merge_near(cands, words)
    say(f"сверяю близнецов моделью: {len(cands)} названий")
    cands = _merge_twins(cands)
    # ⚠️ Рыночная категория снимается ДО следа корпуса и оценки: она не только
    # засоряет ТОП, но и стоит прохода по 5.5 млн работ и вызова модели.
    result.area_en = area_en
    result.facets_en = list(facets_en)
    result.submarkets_en = list(ex.submarkets_en)
    result.categories = [c.label for c in cands if is_category(c.label, words)]
    cands = [c for c in cands if not is_category(c.label, words)]
    if result.categories:
        say(f"снято как рыночная категория: {len(result.categories)}")
    # ⚠️ Стадия идёт минуты (условие по 5.5 млн работ стоит секунды) и молчащая
    # читается как зависание.
    stage("evidence")
    say(f"меряю след ядер в корпусе: {len(cands)} условий, это минуты")
    # ⚠️ Ядро ищется точной фразой, ярлык — по набору слов (`weak.core._match`), и
    # СЧИТАЮТСЯ ОНИ ПОРОЗНЬ: след ядра нужен всем кандидатам (по нему идёт отбор в
    # оценку), а след ярлыка — только дошедшим до оценки, то есть вдвое меньшему числу.
    prints = footprints(store, list(dict.fromkeys(k for c in cands for k in [c.core, *c.cores])))
    blank = Footprint("", 0, None, 0)

    def trace(name: str) -> Footprint:
        return prints.get(name.strip(), blank)

    for c in cands:
        c.core = _traced_core(c, lambda k: trace(k).works)

    # ⚠️ Отбор в оценку — по НОВИЗНЕ ядра, а не по частоте ярлыка: частый ярлык это
    # ровно зрелое ML в прикладных статьях (первый прогон вывел в ТОП LSTM и GNN).
    # Денежный поток идёт первым: разметка заказчика — по деньгам.
    # ⚠️ Ядро без единой работы — «измерить нечем», а не «новее всех»: с подстановкой 9999
    # такие кандидаты гарантированно проходили отсев до 45 и вытесняли настоящие научные
    # с реальным, пусть и старым, ядром (PEL-E-03). Неизвестный год — в хвост.
    cands.sort(
        key=lambda c: (
            c.stream != MONEY_STREAM,
            -(trace(c.core).first_year or 0),
            trace(c.core).works,
        )
    )
    cands = cands[:CANDIDATES]
    result.candidates = len(cands)
    result.headlines_seen = seen_headlines
    emit(
        "counts",
        {
            "candidates": len(cands),
            "categories_removed": len(result.categories),
            "categories": list(result.categories),
        },
    )
    facet_of = assign_facets(cands, facets, hits, by_tech)
    say(f"меряю след названий в корпусе: {len(cands)} условий")
    label_prints = footprints(store, [c.label for c in cands], loose=True)

    def label_trace(name: str) -> Footprint:
        return label_prints.get(name.strip(), blank)

    say(f"живой поиск: новости и русскоязычные источники по {len(cands)} кандидатам")
    # Ярлык группы — уже английское название приёма (модель называет его по
    # английской аннотации), поэтому новостной запрос — сам ярлык, без перевода.
    news_en = news_many_measured([c.label for c in cands], "en")
    live = live_search(query, area_en)
    result.live_by_source = dict(live.queried)
    result.live_errors = dict(live.errors)
    if note := money_cover.note():
        result.live_errors["google_news_money"] = note
    if failed := result.tech_stats.get("failed_batches"):
        result.live_errors["tech"] = (
            f"разметка приёмами не удалась в {failed} пакетах: их работы без приёма"
        )
    unmeasured = {c.label for c, docs in zip(cands, news_en, strict=True) if docs is None}
    if unmeasured:
        # ⚠️ Отказ обязан быть виден: кандидат без новостей из-за сбоя неотличим от
        # кандидата, о котором новостей нет, — а это разные выводы о зрелости.
        result.live_errors["google_news_candidates"] = (
            f"не ответил по {len(unmeasured)} из {len(cands)} кандидатов и после повтора"
        )
    result.context = [
        _live_source(i + 1, d, CODE if d.source == "github" else NEWS)
        for i, d in enumerate(live.docs)
    ]

    # Свидетельства собираются ДО модели: имена компаний берутся одной пачкой на весь
    # прогон, а не отдельным вызовом на кандидата.
    attached: list[list[LiveDoc]] = []
    for c, fresh in zip(cands, news_en, strict=True):
        taken = {d.title for d in c.headlines}
        attached.append(
            (c.headlines + [d for d in fresh or [] if d.title not in taken])[:NEWS_PER_CARD]
        )
    # ⚠️ Игрок — компания из РЫНОЧНОЙ новости. У научной заметки «компаниями»
    # оказываются журнал, университет и конференция («Scientific Reports», «College of
    # Engineering», «AAAI-26»), и научный кандидат проходил бы порог игроков даром.
    # ⚠️ Читаются ВСЕ заголовки направления, а не пять показанных модели: пятёрка —
    # это предел внимания промпта, а не предел распространения. «Identity and access
    # management for AI agents» с шестью заголовками получал одного игрока и вылетал
    # из ТОП — строка датасета, которую мы же и нашли.
    for c, docs in zip(cands, attached, strict=True):
        known = {d.title for d in docs}
        docs.extend(d for d in c.headlines if d.title not in known)
    flat = list(
        {
            d.title: d for docs in attached for d in docs if d.trust.kind not in (SCIENCE, PREPRINT)
        }.values()
    )
    say(f"нахожу компании в {len(flat)} заголовках")
    firms = dict(zip([d.title for d in flat], companies_of(flat), strict=True))

    stage("assess")
    say(f"оцениваю зрелость {len(cands)} кандидатов")
    built: list[tuple[Signal, list[Evidence]]] = []
    news_by_label: dict[str, list[LiveDoc]] = {}
    processed = len(ids) + len(live.docs)
    for c, docs in zip(cands, attached, strict=True):
        work_ids = [sid for t in c.members for sid in by_tech.get(t, []) if sid in papers]
        # ⚠️ Научная опора — это работа, В ТЕКСТЕ которой стоит ядро технологии, а не
        # любая найденная привязкой: поиск возвращает k работ всегда, и проверка на
        # «нашлись ли работы» проходила бы по построению. Сравнение по тексту
        # одинаково честно для обоих потоков, в отличие от совпадения ярлыков:
        # ярлык денежного кандидата приходит из заголовка и с формулировкой статьи
        # не совпадёт никогда.
        needle = (c.core or c.label).strip().lower()
        named_works = [sid for sid in work_ids if names_core(papers[sid][1], needle)]
        processed += len(docs[:NEWS_PER_CARD])
        # ⚠️ Слово из самого ярлыка компанией не считается: у «ensemble of specialized
        # large language models» игроком оказался «Ensemble».
        label_words = {w for w in re.findall(r"[a-z0-9]+", c.label.lower())}
        named = _named_by(docs, firms, label_words)
        if flags.ONPOINT_COMPANIES and named:
            named = _onpoint(c.label, area_en, named)
        shown = docs[:NEWS_PER_CARD]
        news_by_label[c.label] = list(shown)
        # Компания без показанного источника читателем не проверяется, а ТЗ требует
        # источники. Добираем заголовки, которые накрывают больше всего таких компаний.
        backing = _backing(shown, docs[NEWS_PER_CARD:], named)
        processed += len(backing)
        core_fp, label_fp = trace(c.core), label_trace(c.label)
        ev, sources, assessed = _evidence(
            shown, backing, [papers[sid] + (sid,) for sid in work_ids[:PAPERS_PER_CARD]],
            core_fp, label_fp, today,
        )  # fmt: skip
        shown = shown + backing
        market = sum(1 for e in ev if e.kind == NEWS)
        # В карточку идут только те игроки, чей заголовок в ней ПОКАЗАН.
        titles = {d.title for d in shown}
        companies = [x for x, d in named.items() if d.title in titles]
        # Расхождение двух моделей: группировщик назвал компанию, а извлечение по
        # заголовкам её не нашло. Такое имя в карточку не идёт — и это считается вслух.
        result.players_unnamed += sum(
            1 for x in c.companies if x not in named and x.lower() not in label_words
        )

        # Оценщик видит ровно замеренный состав: новости, работы, след. Добор — только
        # карточке и таблице источников.
        assert assessed <= ASSESS_EVIDENCE, f"{assessed} свидетельств при потолке {ASSESS_EVIDENCE}"
        candidate = Candidate(c.label, c.label, ev[:assessed], area_en)
        try:
            a = _assess(candidate)
        except (LLMError, ValueError, httpx.HTTPError) as exc:
            # ⚠️ Отказ оценки обязан быть виден: кандидат иначе исчезал из выдачи без следа
            # — ни в сигналах, ни в исключённых, ни в отказах источников (PEL-E-02).
            result.unassessed.append(c.label)
            say(f"оценка не удалась: {c.label} ({type(exc).__name__})")
            emit("verdict", {"label": c.label, "title": c.label, "kind": "error",
                             "kind_label": "оценка не удалась", "stage": "", "why": "",
                             "companies": [], "stream": c.stream,
                             "facet": facet_of.get(c.label), "sources": 0})
            continue

        recent = _age_share([e.date for e in ev if e.kind != CORPUS], today)
        # ⚠️ По домену ИЗДАТЕЛЯ: у Google News `url` — переадресация, и по нему любой
        # источник читался бы как «неизвестный = средний», то есть подтверждение из
        # разных источников проходило бы всегда.
        # ⚠️ Подтверждение считается по ТИПУ источника, а не по домену издателя: об одном
        # раунде пишут все подряд, и «два разных домена» набирало что угодно — в
        # «Инфраструктуре ИИ» проверка проходила у 92%, то есть не различала ничего
        # (docs/todo.md §72). Собственный след корпуса сюда не идёт: он наш, а не
        # независимый, и с ним проверка снова проходила бы по построению.
        kinds = {s.kind for s in sources if s.evidence != CORPUS and s.kind in INDEPENDENT_KINDS}
        publishers = {s.domain for s in sources if s.evidence == NEWS and s.domain}
        checks = [
            Check("жанр: зарождающаяся", a.kind == EMERGING, kind_label(a.kind), entry=True),
            Check(
                "несколько независимых игроков",
                len(companies) >= MIN_PLAYERS,
                "компании: " + (", ".join(companies[:4]) or "не названы"),
                scored=False,
            ),
            Check(
                "разные типы источников",
                len(kinds) >= 2,
                "типы: " + (", ".join(sorted(kinds)) or "нет независимых"),
            ),
            Check(
                "научная опора",
                len(named_works) >= 2,
                f"работ, называющих «{needle}»: {len(named_works)} из {len(work_ids)}",
            ),
            Check(
                "разные издатели",
                len(publishers) >= 2,
                f"разных доменов среди новостей: {len(publishers)}",
            ),
            Check("объяснение со ссылками", bool(a.why), f"ссылок: {len(a.cited)}"),
        ]
        # ⚠️ Уверенность — доля пройденных проверок СВЕРХ условий отбора и подписей.
        scored = [x for x in checks if x.counted]
        conf = sum(x.passed for x in scored) / len(scored)
        sig = Signal(
            label=c.label,
            title=c.label,
            kind=a.kind,
            kind_label=kind_label(a.kind),
            stage=a.stage,
            confidence=conf,
            evidence_share=conf,
            checks=checks,
            why=card_mod.drop_foreign(a.why) or excluded_why(a.kind, a.judgments),
            sources=sources,
            variants=c.members[:8],
            papers=len(named_works),
            news=market,
            recent_share=recent,
            core=core_fp.core or c.core,
            core_works=core_fp.works,
            core_since=core_fp.first_year,
            core_recent_share=_share(core_fp),
            label_works=label_fp.works,
            companies=companies,
            stream=c.stream,
            judgments=a.judgments,
            news_unmeasured=c.label in unmeasured,
            facet=facet_of.get(c.label),
        )
        built.append((sig, ev))
        emit("verdict", _event_signal(sig))
    result.sources_processed = processed
    if result.unassessed:
        result.live_errors["assess"] = (
            f"не удалась у {len(result.unassessed)} из {len(cands)} кандидатов: "
            + ", ".join(result.unassessed[:5])
        )

    # Видимость: ниже медианы пула — это перцентиль у BERTrend, посчитанный по
    # ЭТОМУ прогону. ⚠️ Сравнивать видимость между запросами нельзя: пулы разные.
    middle = _median([s.label_works for s, _ in built])
    for s, _ in built:
        s.low_visibility = s.label_works <= middle

    shown = [(s, ev) for s, ev in built if s.shown]
    result.excluded = [s for s, _ in built if not s.shown]
    if LISTED_IN_ASK:
        # Модель списка — ключ порядка, поэтому считается у ВСЕГО пула до сортировки:
        # у уже отобранного ТОП ключ порядка считать нельзя.
        _score_by_model(store, [s for s, _ in shown], news_by_label, trace, label_trace, today, say)
    # ⚠️ Порядок внутри ОДНОЙ выдачи: сравнивать баллы разных запросов нельзя
    # (docs/lessons-and-gotchas.md, парадокс Симпсона).
    shares = [s.core_recent_share for s, _ in shown if s.core_recent_share is not None]
    prior = _median_float(shares)
    stage("score")
    if score([s for s, _ in shown], say):
        shown.sort(key=lambda item: (-item[0].confidence, top_key(item[0], prior)))
    else:
        shown.sort(key=lambda item: top_key(item[0], prior))
    if flags.LAYERS:
        shown = _drop_trends(shown, result)
    if flags.TECHNIQUE:
        say(f"называю приём за направлением: {len(shown)}")
        shown = _techniques(shown, result)
    if area_en:
        say("проверяю, о предмете запроса ли кандидаты")
        before = [s for s, _ in shown]
        shown = _on_topic(shown, area_en, result, LAYER_POOL if flags.LAYERS else TOP)
        for s in before:
            if s.relevance is not None:
                emit("relevance", {"label": s.label, "relevance": s.relevance,
                                   "relevance_why": s.relevance_why})
    if flags.LAYERS:
        # Патенты слоя идут здесь, на весь пул — стадия объявляется тут же, иначе экран
        # ожидания перескакивает с «этап 7» на «этап 9» (PQA-016).
        stage("patents")
        # ⚠️ Без срока бюджета (расходится с BR-33, 07 §8.6): срок срабатывает в большинстве
        # прогонов стенда, а без патентов кандидат теряет подтверждение и уходит в
        # «наблюдать» — состав ТОП сдвинулся бы против замера Big Tech 20 → 10, сделанного
        # без срока. Включать — только с перемером ТОП.
        shown = _split_layers(shown, result, say)
        emit("patents", {"patents": {s.label: s.patents for s, _ in shown if s.patents}})
    # Не вошедшие в ТОП по месту (не по жанру) — отдельным списком: страница и бэктест
    # считают их контролем того же пула (прежде здесь были «без нескольких игроков»).
    result.thin = [s for s, _ in shown[TOP:]]
    shown = shown[:TOP]
    emit(
        "top",
        {"labels": [s.label for s, _ in shown], "confidence": [s.confidence for s, _ in shown]},
    )

    # Патенты — третий канал (docs/patents.md): счётчик активности по окнам и свежие
    # публикации с заявителем. Спрашиваются ТРЕМЯ решениями, и каждое стоит объяснить.
    #
    # ⚠️ ПОСЛЕ отбора, а не до: на 45 кандидатах это вчетверо больше запросов и минуты к
    # живому демо, у которого заказчик назвал потолок в 20 минут, а чужие домены уже
    # идут 16–17.
    # ⚠️ ПОСЛЕ оценки зрелости: промпт `assess` замерен на своём составе свидетельств и
    # показывает их ровно `assess.EVIDENCE`, так что девятое вытеснило бы след корпуса —
    # незамеренной правкой, да ещё и молча.
    # ⚠️ И ПОСЛЕ проверок уверенности: патент — источник, который добавили мы сами, и
    # пускать его в «разные типы источников» значило бы завести проверку, проходящую по
    # построению (docs/todo.md §72). В балл он не идёт, в карточку и в таблицу — идёт.
    # Со слоями патенты уже посчитаны на весь их пул (`_split_layers`).
    # ⚠️ Бюджет времени: патенты и углубление идут, пока после них остаётся резерв на
    # карточки. Пропуск — с пометкой в `live_errors`: карточка без патентной строки иначе
    # неотличима от карточки, у которой патентов нет.
    cards_by = deadline - CARDS_RESERVE_S
    if not flags.LAYERS:
        stage("patents")
        attach_patents(shown, result.live_errors, say, deadline=cards_by)
        if flags.EXTRA_SOURCES:
            attach_extra(shown, result.live_errors, say)
        emit("patents", {"patents": {s.label: s.patents for s, _ in shown if s.patents}})

    stage("cards")
    if time.monotonic() + DEEPEN_MIN_S < cards_by:
        deepen_top(shown, result.live_errors, say)
    else:
        result.live_errors["deepen"] = "аннотации не дочитывались: исчерпан бюджет времени запроса"
    # Лестница — всем, кого видно в отчёте (BR-15), а не только ТОП: иначе у исключённых и
    # «наблюдать» в JSON остаётся ступень модели, а пересобранный HTML (`page.from_json`)
    # показывает лестницу — один отчёт с двумя стадиями.
    for sig in [s for s, _ in shown] + result.excluded + result.thin + result.watch:
        sig.stage = evidence_stage(sig.stage, sig.judgments)
    write_cards(shown, say)
    russian_summaries([s for s, _ in shown], result.live_errors, say, deadline)
    for sig, _ in shown:
        emit("card", {"label": sig.label, "title": sig.title, "stage": sig.stage,
                      "description": sig.description, "advantage": sig.advantage,
                      "case": sig.case, "hypothesis": sig.hypothesis})
    # Карточка без русского заголовка (сбой вызова или латинский «перевод») — в тот же
    # перевод названий, что и исключённые (NFR-01, PQA-004).
    latin_top = [s for s, _ in shown if not card_mod.has_cyrillic(s.title)]
    rest = result.excluded + result.thin + result.watch
    russian_titles(rest + latin_top, say)
    rest += latin_top
    emit("titles", {"titles": {s.label: s.title for s in rest if s.title != s.label}})
    result.signals = [s for s, _ in shown]
    result.seconds = (datetime.now(UTC) - t0).total_seconds()
    say("готово")
    return result
