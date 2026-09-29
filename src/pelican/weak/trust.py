"""Тип и доверенность источника: по домену, по правилу ТЗ.

ТЗ задаёт шкалу дословно, и она здесь переписана, а не придумана:

- **доверенные** — «официальные сайты государственных органов, регуляторов,
  международных организаций, университетов, научных центров, компаний-разработчиков,
  отраслевых ассоциаций, а также научные публикации, материалы конференций, патентные
  базы, государственные реестры, профессиональные отраслевые медиа и аналитические
  отчёты — с возможностью проверки первоисточника»;
- **пониженная доверенность** — «социальные сети, личные блоги, агрегаторы, анонимные
  ресурсы, рекламные публикации и пресс-релизы могут использоваться как первичный
  индикатор, но не должны быть единственным основанием для включения технологии».

Отсюда три уровня: `high` (наука, государство, университеты), `medium`
(профессиональные отраслевые медиа, вендоры), `low` (пресс-релизы, блоги, соцсети,
агрегаторы).

⚠️ **Неизвестный домен — `medium`, а не `high`.** Длинный хвост издателей Google News
состоит из отраслевых медиа, но и из перепечаток; поднимать безымянное до доверенного
значило бы сделать правило ТЗ обходимым любым новым доменом. Опускать до `low` тоже
нельзя: тогда почти любая карточка с медиа-подтверждением выглядела бы неподтверждённой.

⚠️ **Правило ТЗ проверяется на карточке, а не на источнике** (`corroborated`):
технология без единого источника выше `low` в выдачу не идёт — или идёт с отметкой
о пониженной доверенности.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

LEVEL_LABELS = {HIGH: "высокая", MEDIUM: "средняя", LOW: "пониженная"}

SCIENCE = "научная публикация"
PREPRINT = "препринт"
GOV = "государство / регулятор"
UNIVERSITY = "университет / научный центр"
ASSOCIATION = "отраслевая ассоциация / международная организация"
MEDIA = "отраслевое медиа"
VENDOR = "компания-разработчик"
PRESS = "пресс-релиз"
BLOG = "блог / соцсеть / агрегатор"
CODE = "репозиторий кода"
PATENT = "патентная база"


@dataclass(frozen=True, slots=True)
class Trust:
    kind: str
    level: str

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]


# Точные домены (без www). Порядок проверки: точный домен → суффикс → зона.
_EXACT: dict[str, Trust] = {
    # Патентные базы: ТЗ относит их к доверенным наравне с наукой и государством.
    "ops.epo.org": Trust(PATENT, HIGH),
    "worldwide.espacenet.com": Trust(PATENT, HIGH),
    "register.epo.org": Trust(PATENT, HIGH),
    "patents.google.com": Trust(PATENT, HIGH),
    "patentscope.wipo.int": Trust(PATENT, HIGH),
    "data.uspto.gov": Trust(PATENT, HIGH),
    "ppubs.uspto.gov": Trust(PATENT, HIGH),
    "fips.ru": Trust(PATENT, HIGH),
    "new.fips.ru": Trust(PATENT, HIGH),
    "arxiv.org": Trust(PREPRINT, HIGH),
    "export.arxiv.org": Trust(PREPRINT, HIGH),
    "openalex.org": Trust(SCIENCE, HIGH),
    "doi.org": Trust(SCIENCE, HIGH),
    "nature.com": Trust(SCIENCE, HIGH),
    "science.org": Trust(SCIENCE, HIGH),
    "sciencedirect.com": Trust(SCIENCE, HIGH),
    "springer.com": Trust(SCIENCE, HIGH),
    "link.springer.com": Trust(SCIENCE, HIGH),
    "ieeexplore.ieee.org": Trust(SCIENCE, HIGH),
    "dl.acm.org": Trust(SCIENCE, HIGH),
    "mdpi.com": Trust(SCIENCE, HIGH),
    "cyberleninka.ru": Trust(SCIENCE, HIGH),
    "elibrary.ru": Trust(SCIENCE, HIGH),
    "ieee.org": Trust(ASSOCIATION, HIGH),
    "owasp.org": Trust(ASSOCIATION, HIGH),
    "oecd.org": Trust(ASSOCIATION, HIGH),
    "bis.org": Trust(ASSOCIATION, HIGH),
    "imf.org": Trust(ASSOCIATION, HIGH),
    "worldbank.org": Trust(ASSOCIATION, HIGH),
    "weforum.org": Trust(ASSOCIATION, MEDIUM),
    "nist.gov": Trust(GOV, HIGH),
    "cbr.ru": Trust(GOV, HIGH),
    "europa.eu": Trust(GOV, HIGH),
    "techcrunch.com": Trust(MEDIA, MEDIUM),
    "siliconangle.com": Trust(MEDIA, MEDIUM),
    "theaiinsider.tech": Trust(MEDIA, MEDIUM),
    "therobotreport.com": Trust(MEDIA, MEDIUM),
    "roboticsandautomationnews.com": Trust(MEDIA, MEDIUM),
    "eetimes.com": Trust(MEDIA, MEDIUM),
    "datacenterdynamics.com": Trust(MEDIA, MEDIUM),
    "geekwire.com": Trust(MEDIA, MEDIUM),
    "reuters.com": Trust(MEDIA, MEDIUM),
    "securityweek.com": Trust(MEDIA, MEDIUM),
    "venturebeat.com": Trust(MEDIA, MEDIUM),
    "theregister.com": Trust(MEDIA, MEDIUM),
    "tadviser.ru": Trust(MEDIA, MEDIUM),
    "cnews.ru": Trust(MEDIA, MEDIUM),
    "rbc.ru": Trust(MEDIA, MEDIUM),
    "kommersant.ru": Trust(MEDIA, MEDIUM),
    "vedomosti.ru": Trust(MEDIA, MEDIUM),
    "github.com": Trust(CODE, MEDIUM),
    "prnewswire.com": Trust(PRESS, LOW),
    "businesswire.com": Trust(PRESS, LOW),
    "globenewswire.com": Trust(PRESS, LOW),
    "einpresswire.com": Trust(PRESS, LOW),
    "accessnewswire.com": Trust(PRESS, LOW),
    "finance.yahoo.com": Trust(BLOG, LOW),
    "news.ycombinator.com": Trust(BLOG, LOW),
    "habr.com": Trust(BLOG, LOW),
    "medium.com": Trust(BLOG, LOW),
    "substack.com": Trust(BLOG, LOW),
    "reddit.com": Trust(BLOG, LOW),
    "x.com": Trust(BLOG, LOW),
    "twitter.com": Trust(BLOG, LOW),
    "linkedin.com": Trust(BLOG, LOW),
    "t.me": Trust(BLOG, LOW),
    "vc.ru": Trust(BLOG, LOW),
    "dzen.ru": Trust(BLOG, LOW),
    "pulse2.com": Trust(BLOG, LOW),
}

_SUFFIX: tuple[tuple[str, Trust], ...] = (
    (".substack.com", Trust(BLOG, LOW)),
    (".medium.com", Trust(BLOG, LOW)),
    (".blogspot.com", Trust(BLOG, LOW)),
)

_ZONES: tuple[tuple[str, Trust], ...] = (
    (".gov", Trust(GOV, HIGH)),
    (".gov.ru", Trust(GOV, HIGH)),
    (".gov.uk", Trust(GOV, HIGH)),
    (".mil", Trust(GOV, HIGH)),
    (".edu", Trust(UNIVERSITY, HIGH)),
    (".ac.uk", Trust(UNIVERSITY, HIGH)),
    (".ac.jp", Trust(UNIVERSITY, HIGH)),
    (".int", Trust(ASSOCIATION, HIGH)),
)

#: Неизвестный домен. ⚠️ `medium`, а не `high` — см. докстринг модуля.
UNKNOWN = Trust(MEDIA, MEDIUM)


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or url).lower()
    return host[4:] if host.startswith("www.") else host


def classify(url: str) -> Trust:
    host = domain_of(url)
    if host in _EXACT:
        return _EXACT[host]
    for suffix, trust in _SUFFIX:
        if host.endswith(suffix):
            return trust
    for zone, trust in _ZONES:
        if host.endswith(zone):
            return trust
    return UNKNOWN


def corroborated(levels: list[str]) -> bool:
    """Правило ТЗ: есть ли хоть один источник выше пониженной доверенности.

    ⚠️ На вход — уровни, уже посчитанные по домену ИЗДАТЕЛЯ (`Source.trust`), а не ссылки:
    у Google News ссылка — переадресация `news.google.com`, и по ней уровень не считается.
    """
    return any(level != LOW for level in levels)
