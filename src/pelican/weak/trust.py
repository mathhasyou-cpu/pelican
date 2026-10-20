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
агрегаторы) — и четвёртый, `unknown`: домена нет в списках.

⚠️ **Неизвестный домен — `unknown`, а не `medium` и не `high`.** Длинный хвост
издателей Google News состоит из отраслевых медиа, но и из перепечаток; поднимать
безымянное до доверенного значило бы сделать правило ТЗ обходимым любым новым доменом,
а «средняя» на странице утверждала бы оценку, которой не было. Тип у него — медиа: в
проверку «разные типы источников» он идёт как медиа.

⚠️ **Правило ТЗ проверяется на карточке, а не на источнике** (`corroborated`): нужен
хотя бы один ИЗВЕСТНЫЙ источник выше `low`. Неизвестный домен основанием не считается.
Замер на 203 карточках стенда: ни одна не держалась на одних неизвестных доменах — у
каждой есть работа корпуса, — так что правило меняет пометку только у карточек без науки.
Список медиа дополнен самыми частыми неизвестными доменами тех же отчётов.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
UNKNOWN_LEVEL = "unknown"

LEVEL_LABELS = {HIGH: "высокая", MEDIUM: "средняя", LOW: "пониженная", UNKNOWN_LEVEL: "не оценена"}

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
    # Препринты наук о жизни и химии (`weak.extra`, через Europe PMC). Как у arXiv —
    # препринт, а не медиа: иначе «разные типы источников» считали бы их рынком.
    "biorxiv.org": Trust(PREPRINT, HIGH),
    "medrxiv.org": Trust(PREPRINT, HIGH),
    "chemrxiv.org": Trust(PREPRINT, HIGH),
    "researchsquare.com": Trust(PREPRINT, HIGH),
    "preprints.org": Trust(PREPRINT, HIGH),
    "ssrn.com": Trust(PREPRINT, HIGH),
    "europepmc.org": Trust(SCIENCE, HIGH),
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
    # Частые неизвестные домены отчётов стенда (замер 19 отчётов, 1012 вхождений).
    "frontiersin.org": Trust(SCIENCE, HIGH),
    "ojs.aaai.org": Trust(SCIENCE, HIGH),
    "onlinelibrary.wiley.com": Trust(SCIENCE, HIGH),
    "advanced.onlinelibrary.wiley.com": Trust(SCIENCE, HIGH),
    "pubs.acs.org": Trust(SCIENCE, HIGH),
    "ahajournals.org": Trust(SCIENCE, HIGH),
    "alphaxiv.org": Trust(PREPRINT, HIGH),
    "eurekalert.org": Trust(PRESS, LOW),
    "fortune.com": Trust(MEDIA, MEDIUM),
    "cnbc.com": Trust(MEDIA, MEDIUM),
    "forbes.com": Trust(MEDIA, MEDIUM),
    "wsj.com": Trust(MEDIA, MEDIUM),
    "businessinsider.com": Trust(MEDIA, MEDIUM),
    "axios.com": Trust(MEDIA, MEDIUM),
    "statnews.com": Trust(MEDIA, MEDIUM),
    "calcalistech.com": Trust(MEDIA, MEDIUM),
    "industrialcyber.co": Trust(MEDIA, MEDIUM),
    "mobihealthnews.com": Trust(MEDIA, MEDIUM),
    "genengnews.com": Trust(MEDIA, MEDIUM),
    "fintech.global": Trust(MEDIA, MEDIUM),
    "finovate.com": Trust(MEDIA, MEDIUM),
    "fintechfutures.com": Trust(MEDIA, MEDIUM),
    "thequantuminsider.com": Trust(MEDIA, MEDIUM),
    "helpnetsecurity.com": Trust(MEDIA, MEDIUM),
    "biopharmadive.com": Trust(MEDIA, MEDIUM),
    "tomshardware.com": Trust(MEDIA, MEDIUM),
    "aws.amazon.com": Trust(VENDOR, MEDIUM),
    "developer.nvidia.com": Trust(VENDOR, MEDIUM),
    "nvidianews.nvidia.com": Trust(VENDOR, MEDIUM),
    "microsoft.com": Trust(VENDOR, MEDIUM),
    "news.microsoft.com": Trust(VENDOR, MEDIUM),
    "machinelearning.apple.com": Trust(VENDOR, MEDIUM),
    "research.google": Trust(VENDOR, MEDIUM),
    "ibm.com": Trust(VENDOR, MEDIUM),
    "app.dealroom.co": Trust(BLOG, LOW),
    "startupfortune.com": Trust(BLOG, LOW),
    "marktechpost.com": Trust(BLOG, LOW),
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

#: Неизвестный домен: тип — медиа, уровень — `unknown` (см. докстринг модуля).
UNKNOWN = Trust(MEDIA, UNKNOWN_LEVEL)


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
    """Правило ТЗ: есть ли хоть один ИЗВЕСТНЫЙ источник выше пониженной доверенности.

    ⚠️ На вход — уровни, уже посчитанные по домену ИЗДАТЕЛЯ (`Source.trust`), а не ссылки:
    у Google News ссылка — переадресация `news.google.com`, и по ней уровень не считается.
    ⚠️ Неизвестный домен основанием не считается: иначе правило проходило при
    единственной перепечатке с безымянного сайта (PEL-H-07).
    """
    return any(level in (HIGH, MEDIUM) for level in levels)
