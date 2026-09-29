"""HTML-выдача `trends ask`: одна самодостаточная страница по-русски.

Что обязано быть на странице — дословно из ТЗ, и порядок разделов идёт по нему:

- поисковый запрос; названия технологий; скоринг — уверенность модели;
- ключевые предикторы — объяснение отнесения к зарождающемуся тренду;
- просмотр инсайта — страница «похожая на документ-отчёт»: описание, преимущества,
  кейс-пример, объяснение статуса и уверенности, ссылки на источники;
- по каждому источнику: наименование, ссылка, дата, тип, язык оригинала, уровень
  доверенности; у иностранного — русское резюме с отметкой, что оно машинное;
- причины исключения зрелых технологий и нерелевантных кандидатов;
- плюсом: число найденных кандидатов, число обработанных источников, число сигналов с
  уверенностью выше 75%.

⚠️ **Страница ничего не вычисляет** — все числа приходят готовыми из `AskResult`,
ровно как в `pipeline/report.py`. И ⚠️ **ни одного внешнего запроса**: стили и скрипт
встроены, отчёт открывается офлайн.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict, fields
from typing import Any

from pelican.weak.ask import AskResult, Check, Signal, Source
from pelican.weak.card import drop_foreign, foreign_script
from pelican.weak.kinds import LABELS as KIND_LABELS
from pelican.weak.rubric import STAGE_LABELS, stage_floor

_CSS = """
:root{--bg:#fafaf7;--fg:#1c1d22;--muted:#6b6b76;--line:#e3e1da;--card:#fff;
--accent:#310f53;--good:#1f7a4d;--bad:#a23b3b;--mid:#8a6d1a;--chip:#f1eef8}
@media (prefers-color-scheme:dark){:root{--bg:#141418;--fg:#ececf1;--muted:#9a9aa6;
--line:#2c2c34;--card:#1c1c22;--accent:#c9b6ff;--good:#6fd39f;--bad:#f08a8a;
--mid:#e0c068;--chip:#26213a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1800px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 10px}
h3{font-size:15px;margin:18px 0 6px;color:var(--accent)}
.muted{color:var(--muted)}.q{font-size:18px;margin:4px 0 16px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:10px 14px;min-width:150px}
.stat b{display:block;font-size:24px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;background:var(--card);
table-layout:fixed}
th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top;
overflow-wrap:anywhere}
ol.facts{margin:0;padding-left:1.2em}ol.facts b{font-variant-numeric:tabular-nums}
th{font-size:13px;color:var(--muted);font-weight:600}
a{color:var(--accent)}details.card{background:var(--card);border:1px solid var(--line);
border-radius:12px;margin:12px 0;padding:0 16px}
details.card>summary{cursor:pointer;padding:14px 0;font-weight:600;font-size:16px;list-style:none}
details.card[open]>summary{border-bottom:1px solid var(--line)}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 9px;
font-size:12px;margin:0 4px 4px 0;font-weight:500;max-width:100%;overflow-wrap:anywhere}
.ok{color:var(--good)}.no{color:var(--bad)}.lvl-high{color:var(--good)}.lvl-medium{color:var(--mid)}
.lvl-low{color:var(--bad)}.bar{display:inline-block;height:8px;border-radius:4px;
background:var(--accent);
vertical-align:middle;margin-right:6px}.gen{font-size:12px;color:var(--mid)}
ul.checks{list-style:none;padding:0;margin:0}ul.checks li{padding:2px 0}
.note{background:var(--card);border-left:3px solid var(--accent);padding:10px 14px;margin:12px 0}
.hint{position:relative;display:inline-block;margin-left:3px;color:var(--muted);cursor:help;
font-weight:400;font-size:13px;outline:none}
.hint>.tip{display:none;position:absolute;z-index:20;top:1.7em;left:-8px;
width:min(380px,85vw);background:var(--fg);color:var(--bg);padding:10px 12px;border-radius:8px;
font-size:13px;line-height:1.45;font-weight:400;text-align:left;white-space:normal;
box-shadow:0 6px 24px rgba(0,0,0,.25)}
.hint.r>.tip{left:auto;right:-8px}
.hint:hover>.tip,.hint:focus>.tip{display:block}
.grid{display:grid;grid-template-columns:3fr 19fr 12fr 9fr 9fr 21fr 11fr 16fr;gap:0 10px}
.grid>*{min-width:0;overflow-wrap:anywhere}
.thead{font-size:13px;color:var(--muted);font-weight:600;padding:8px 12px;
border-bottom:1px solid var(--line)}
details.sig{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}
details.sig>summary{list-style:none;cursor:pointer;padding:10px 12px;font-size:14px}
details.sig>summary::-webkit-details-marker{display:none}
details.sig>summary:hover{background:var(--chip);border-radius:10px}
details.sig[open]>summary{border-bottom:1px solid var(--line);border-radius:10px 10px 0 0}
.tw{font-weight:600;font-size:15px}.arrow{display:inline-block;transition:transform .15s;
color:var(--muted)}details[open]>summary .arrow{transform:rotate(90deg)}
.small{font-size:12.5px}.body{padding:4px 18px 16px}
details.more{border-top:1px solid var(--line);margin-top:14px}
details.more>summary{cursor:pointer;padding:10px 0 4px;color:var(--accent);font-weight:600}
details.sec>summary{cursor:pointer;list-style:none}
details.sec>summary h2{display:inline}details.sec>summary::-webkit-details-marker{display:none}
.lbl{display:none}
@media (max-width:900px){.grid{grid-template-columns:2em 1fr}.grid>*{grid-column:2}
.grid>:first-child{grid-column:1}.thead{display:none}.lbl{display:inline;color:var(--muted)}
details.sig>summary .grid>*{margin-top:4px}}
"""


_NONE = '<span class="muted">не сформулировано по свидетельствам</span>'
_NO_WHY = (
    '<span class="muted">причина — класс в заголовке; объяснение модели без ссылки на '
    "источник отброшено</span>"
)


#: Ключи живого поиска (`weak.live.search`, `ask.attach_patents`) — по-русски для страницы.
_LIVE_NAMES = {
    "google_news_en": "Google Новости (англ.)",
    "google_news_ru": "Google Новости (рус.)",
    "cyberleninka": "КиберЛенинка",
    "habr": "Хабр",
    "github": "GitHub",
    "patents": "патенты EPO",
}


def _name(s: Signal) -> str:
    """Название в списке исключённых: русское (`ask.russian_titles`) и английский ярлык
    мелко; у отчётов до перевода — только ярлык."""
    if s.title and s.title != s.label and not foreign_script(s.title):
        return f'<b>{_e(s.title)}</b> <span class="muted small">{_e(s.label)}</span>'
    return f"<b>{_e(s.label)}</b>"
#: Правило ТЗ: только соцсети, блоги, пресс-релизы — отметка, а не молчание (`Signal.corroborated`).
_LOW_TRUST = (
    '<span class="chip no">пониженная доверенность: подтверждено только соцсетями, '
    "блогами или пресс-релизами</span>"
)

#: Объяснения неочевидных терминов страницы — единственное место их текста: из него же
#: собраны тултипы (`_hint`) и раздел «Как считается», чтобы они не расходились.
HINTS: dict[str, str] = {
    "confidence": (
        "Вероятность, что за два года научный след направления (число работ по его ядру в "
        "arXiv и OpenAlex) вырастет сильнее, чем в среднем по области. Логистическая "
        "регрессия, обученная на трёх исторических срезах (2020, 2022, 2024); признаки "
        "считаются относительно остальных кандидатов этого же запроса. Проверка будущим — "
        "обучение на двух срезах, проверка на третьем: AUC 0.85 / 0.82 / 0.73 при случайном "
        "≈ 0.6. По ней построен порядок ТОП. Это НЕ «насколько технология подходит запросу» "
        "— для этого колонка «Соответствие запросу»."
    ),
    "relevance": (
        "О предмете ли запроса свидетельства технологии, по шкале 0–3 (UMBRELA): 3 — "
        "технология сделана для предмета или изучает его; 2 — смежное: материалы, "
        "инструменты, сервисы, инфраструктура, на которые предмет опирается; 1 — могла бы "
        "применяться, но свидетельств нет; 0 — другая отрасль. Оценивает языковая модель "
        "отдельным вызовом по свидетельствам. В ТОП идут только 2 и 3, остальные — в "
        "исключённых; в порядок и уверенность оценка не входит."
    ),
    "predictors": (
        "Три главных слагаемых уверенности: вклад = вес признака в модели × отклонение его "
        "значения от среднего. «За» поднимает уверенность, «против» опускает. Признаки: "
        "доля публикаций по ядру за последний год; насколько она выше, чем у других "
        "кандидатов запроса; стоят ли за технологией компании; сколько их."
    ),
    "stage": (
        "Самая продвинутая стадия, которую показывают свидетельства: концепция/исследование "
        "→ прототип/PoC → пилот → раннее внедрение (шкала рубрики заказчика). Если новость "
        "показывает стадию позже, чем статьи, решает новость. Под стадией — что прочитано в "
        "свидетельствах: раунд или сделка, продажи, пилот."
    ),
    "companies": (
        "Компании, названные в рыночных новостях об этом направлении. Научные заметки не в "
        "счёт: там «компаниями» оказываются журналы и университеты."
    ),
    "why": (
        "Одна фраза модели со ссылками на свидетельства: почему это зарождающаяся "
        "технология и почему такая стадия. Предложение без ссылок отбрасывается."
    ),
    "evidence": (
        "Качество свидетельств — доля пройденных проверок: новости из ≥2 типов источников; "
        "≥2 работы корпуса, где ядро технологии стоит в тексте; новости ≥2 разных издателей; "
        "объяснение ссылается на источники. Условия попадания в ТОП в долю не входят."
    ),
    "held": (
        "В скольких из последних прошлых прогонов этого же запроса есть то же направление. "
        "Согласие выдачи с самой собой, а не качество сигнала: в уверенность и порядок не "
        "входит."
    ),
    "candidates": (
        "Сколько направлений извлечено из научных работ и новостей и оценено моделью: "
        "зрелое, стандарт, хайп, шум или зарождающееся."
    ),
    "confident": "Сигналов, у которых уверенность модели выше 75%.",
    "excluded": (
        "Зрелые технологии, отраслевые стандарты, маркетинговый хайп, шум и слишком общие "
        "категории: ТЗ запрещает включать их в выдачу. Причина у каждого — в разделе ниже."
    ),
    "thin": "Зарождающиеся технологии, которые не вошли в ТОП-15 по уверенности модели.",
    "categories": (
        "Названия, в которых нет ни одного слова сверх самого запроса и рыночного словаря: "
        "такое название подходит любой компании отрасли сразу."
    ),
    "sources": "Сколько документов (работ, новостей, репозиториев, патентов) прочитано за запрос.",
}


def _hint(key: str, right: bool = False) -> str:
    """Значок ⓘ с тултипом на чистом CSS: `:hover` для мыши, `:focus` (tabindex) для тапа.
    `right` — тултип прижат к правому краю, чтобы у крайних колонок не вылез за страницу."""
    text = _e(HINTS[key])
    side = " r" if right else ""
    return (
        f'<span class="hint{side}" tabindex="0" aria-label="{text}">ⓘ'
        f'<span class="tip" role="tooltip">{text}</span></span>'
    )


#: Шкала соответствия запросу (`Signal.relevance`) словами.
RELEVANCE_LABELS = {
    3: "прямо о предмете",
    2: "смежное: то, на что предмет опирается",
    1: "могла бы применяться",
    0: "другая отрасль",
}


def _relevance(sig: Signal) -> str:
    if sig.relevance is None:
        return '<span class="muted">—</span>'
    word = _e(RELEVANCE_LABELS.get(sig.relevance, str(sig.relevance)))
    cls = "ok" if sig.relevance == 3 else "lvl-medium" if sig.relevance == 2 else "no"
    return f'<span class="{cls}">{word}</span>'


#: Что прочитано в свидетельствах о коммерческой стадии (`assess.JUDGMENTS`).
_COMMERCE = (
    ("funding_mentioned", "раунд или сделка"),
    ("commercial_deployment", "продажи"),
    ("pilot_mentioned", "пилот"),
)


def _stage(sig: Signal) -> str:
    """Стадия внедрения и её подтверждения — вместо жанра: в ТОП все зарождающиеся по
    построению, и чип «зарождающаяся» рядом с раундом на $36M только спорил со стадией."""
    marks = [word for key, word in _COMMERCE if sig.judgments.get(key)]
    tail = f'<br><span class="muted small">есть: {_e(", ".join(marks))}</span>' if marks else ""
    return f"{_e(STAGE_LABELS.get(sig.stage, sig.stage))}{tail}"


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _checks_ratio(sig: Signal) -> str:
    scored = [c for c in sig.checks if not c.entry]
    return f"{sum(c.passed for c in scored)} из {len(scored)}"


def _conf_cell(sig: Signal) -> str:
    # Уверенность — вероятность модели скоринга (`ask.score`); без модели — доля проверок, и
    # тогда рядом стоит сама дробь: голый процент читался бы как вероятность.
    width = max(4, round(sig.confidence * 60))
    note = (
        f"свидетельства: {_checks_ratio(sig)}"
        if sig.predictors
        else f"({_checks_ratio(sig)} проверок)"
    )
    # Бейдж — только порог ТЗ (> 75%, тот же, что в плитке `confident`): второй порог
    # («средняя») пришлось бы выдумать, а замерить его не на чем.
    badge = '<span class="chip ok">высокая</span> ' if sig.confidence > 0.75 else ""
    return (
        f'<span class="bar" style="width:{width}px"></span>{badge}{_pct(sig.confidence)} '
        f'<span class="muted">{note}</span>'
    )


def _predictors_cell(sig: Signal) -> str:
    """Колонка «Ключевые предикторы» таблицы ТОП: вклады модели скоринга; без модели —
    пройденные проверки свидетельств."""
    if sig.predictors:
        return "<br>".join(_e(p) for p in sig.predictors)
    passed = [c.name for c in sig.checks if c.passed and not c.entry]
    return _e("; ".join(passed)) or "—"


def _listed_block(sig: Signal) -> str:
    """Шанс попасть в список заказчика (`weak/listed.json`) — только когда модель есть.

    Это ключ порядка ТОП. Подпись говорит, чего он стоит: модель обучена на метке «вошёл в
    список методолога 2026» по трём срезам бэктеста; зачёт — строк списка в верхе на
    temporal holdout (`reports/weak-listed-holdout.*`), не калиброванная вероятность:
    метка positive-unlabeled, отрицательных у заказчика нет (docs/weak-audit.md).
    """
    if sig.listed is None:
        return ""
    items = "".join(f"<li>{_e(x)}</li>" for x in sig.listed_predictors)
    return (
        f"<h3>Шанс попасть в список заказчика: {_pct(sig.listed)}</h3>"
        '<p class="muted">Ключ порядка ТОП. Модель, обученная на трёх срезах бэктеста '
        "(<code>reports/weak-listed-model.md</code>): шанс, что направление войдёт в список "
        "методолога через два года. Не калиброванная вероятность — у списка нет "
        "отрицательных примеров, только строки, в него вошедшие. Главные вклады:</p>"
        f'<ul class="checks">{items}</ul>'
    )


def _model_block(sig: Signal) -> str:
    """«Скоринг — уверенность модели» и «ключевые предикторы» раздела UI ТЗ (`ask.score`).

    Вклады со знаком — те самые слагаемые, из которых сложилась вероятность, а не
    объяснение постфактум. Без модели скоринга блока нет.
    """
    if not sig.predictors:
        return ""
    items = "".join(f"<li>{_e(x)}</li>" for x in sig.predictors)
    return (
        f"<h3>Уверенность модели: {_pct(sig.confidence)}</h3>"
        f'<p class="muted">{_e(HINTS["confidence"])}</p>'
        f'<p class="muted">Ключевые предикторы. {_e(HINTS["predictors"])}</p>'
        f'<ul class="checks">{items}</ul>'
    )


def _cols(*widths: int) -> str:
    """Доли ширины колонок. ⚠️ Таблицы — `table-layout: fixed`: без долей колонки делятся
    поровну, а без fixed длинный текст уводит таблицу в горизонтальную прокрутку уже на
    1920 px (требование: влезать без прокрутки на 1920 и 2560)."""
    return "<colgroup>" + "".join(f'<col style="width:{w}%">' for w in widths) + "</colgroup>"


def _facts(sig: Signal) -> str:
    if not sig.facts:
        return ""
    items = "".join(f"<li><b>{_e(f['date'])}</b> — {_e(f['text'])}</li>" for f in sig.facts)
    return f'<h3>Хронология</h3><ol class="facts">{items}</ol>'


def _sources_table(sources: list[Source]) -> str:
    if not sources:
        return '<p class="muted">источников нет</p>'
    rows = []
    for s in sources:
        title = _e(s.title) or _e(s.url)
        link = (
            f'<a href="{_e(s.url)}" target="_blank" rel="noopener">{title}</a>' if s.url else title
        )
        who = f'<br><span class="muted">{_e(s.publisher)}</span>' if s.publisher else ""
        summary = ""
        if s.summary_ru:
            mark = ""
            if s.machine_summary:
                note = ("машинное резюме на русском, оригинал на языке источника"
                        if s.lang != "ru" else "резюме сгенерировано моделью")
                mark = f' <span class="gen">· {note}</span>'
            summary = f"<br>{_e(s.summary_ru)}{mark}"
        rows.append(
            f"<tr><td>[{s.n}]</td><td>{link}{who}{summary}</td><td>{_e(s.date) or '—'}</td>"
            f"<td>{_e(s.kind)}</td><td>{_e(s.lang)}</td>"
            f'<td class="lvl-{_e(s.trust)}">{_e(s.trust_label)}</td></tr>'
        )
    return (
        '<div class="wrap"><table>' + _cols(4, 58, 10, 12, 5, 11)
        + "<thead><tr><th>№</th><th>Наименование</th><th>Дата</th>"
        "<th>Тип</th><th>Язык</th><th>Доверенность</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _core(sig: Signal) -> str:
    """Два следа в корпусе: ядра и самого ярлыка (docs/weak-signals.md)."""
    if not sig.core:
        return ""
    if not sig.core_works:
        trace = "в проиндексированных работах не встречается"
    else:
        trace = f"{sig.core_works} работ с {sig.core_since} года"
    visible = "ниже медианы выдачи" if sig.low_visibility else "выше медианы выдачи"
    return (
        f'<p class="muted">Ядро технологии: <b>{_e(sig.core)}</b> — {_e(trace)}; '
        f"этими же словами — {sig.label_works} работ ({visible} по видимости). "
        "Корпус arXiv + OpenAlex, 5.5 млн работ.</p>"
    )


def _patents(sig: Signal) -> str:
    """Патентная активность по ядру (docs/patents.md).

    ⚠️ Пусто — это «измерить нечем» (источник выключен или OPS отказал), а не «патентов
    нет»: ноль пишется числом и выглядит как ноль.
    """
    if not sig.patents:
        return ""
    return (
        f'<p class="muted">Патентная активность (EPO OPS, всемирный охват): '
        f"{_e(sig.patents)}. В уверенность не входит — источник добран нами "
        "после оценки.</p>"
    )


def _companies(sig: Signal) -> str:
    """Кто это делает — колонка «Компании» в разметке заказчика."""
    if not sig.companies:
        return '<p class="muted">компании в заголовках не названы</p>'
    return "".join(f'<span class="chip">{_e(c)}</span>' for c in sig.companies)


#: Замер разброса без кэша (docs/weak-measurements.md): по три прогона четырёх запросов ТЗ
#: — по направлению совпадают 5–10 позиций из 15, обычно 9.
_SPREAD = (
    "Прошлых прогонов по этому запросу нет. По замеру верх выдачи одного запроса совпадает "
    "между прогонами примерно на 9 позиций из 15: состав ТОП зависит от новостей дня."
)


def _held(sig: Signal, history_runs: int) -> str:
    """Устойчивость направления между прогонами — подпись, а не балл (`weak.history`)."""
    if not history_runs:
        return _SPREAD
    return (
        f"Направление держится в {sig.held} из {history_runs} прошлых прогонов по этому "
        "запросу (то же направление, названное иначе, засчитывается). В уверенность и в "
        "порядок ТОП это не входит."
    )


def _hypothesis(sig: Signal) -> str:
    """«Сигнал найден → генерация гипотезы» (схема 1 ТЗ). Нет гипотезы (отчёт старше поля
    или не прошла чистку) — нет и блока: пустой заголовок читается как сбой."""
    if not sig.hypothesis:
        return ""
    return (
        f'<h3>Гипотеза для проверки</h3><p>{_e(sig.hypothesis)}</p>'
        '<p class="gen">Гипотеза сгенерирована моделью по источникам карточки; это '
        "предположение для эксперта, а не вывод.</p>"
    )


def _card(i: int, sig: Signal, history_runs: int = 0) -> str:
    checks = "".join(
        f'<li><span class="{"ok" if c.passed else "no"}">{"✔" if c.passed else "✘"}</span> '
        f'{_e(c.name)} <span class="muted">— {_e(c.detail)}'
        f"{' · условие попадания в ТОП' if c.entry else ''}</span></li>"
        for c in sig.checks
    )
    scored = [c for c in sig.checks if not c.entry]
    passed = sum(c.passed for c in scored)
    variants = "".join(f'<span class="chip">{_e(v)}</span>' for v in sig.variants)
    relevance = (
        f'<p><b>Соответствие запросу:</b> {_relevance(sig)}'
        f'{" — " + _e(sig.relevance_why) if sig.relevance_why else ""}</p>'
        if sig.relevance is not None
        else ""
    )
    return f"""
<details class="sig" id="s{i}"><summary>{_row(i, sig)}</summary><div class="body">
<p><span class="chip">стадия: {_e(STAGE_LABELS.get(sig.stage, sig.stage))}</span>{"".join(
    f'<span class="chip">есть {_e(w)}</span>' for k, w in _COMMERCE if sig.judgments.get(k))}
{"" if sig.corroborated else _LOW_TRUST}</p>
{relevance}
{_facts(sig)}
<h3>Описание технологии</h3><p>{_e(sig.description) or _NONE}</p>
<h3>Потенциальное преимущество</h3><p>{_e(sig.advantage) or '<span class="muted">—</span>'}</p>
<h3>Кейс-пример</h3><p>{_e(sig.case) or '<span class="muted">—</span>'}</p>
<h3>Кто это делает</h3><p>{_companies(sig)}</p>
<h3>Почему это слабый сигнал</h3>
<p>{_e(sig.why) or '<span class="muted">объяснение без ссылок на источники отброшено</span>'}</p>
{_hypothesis(sig)}
<details class="more"><summary>Как посчитана уверенность — {_pct(sig.confidence)}</summary>
{_model_block(sig)}
<p class="muted">{_held(sig, history_runs)}</p>
{_listed_block(sig)}
</details>
<details class="more"><summary>Проверки свидетельств — {passed} из {len(scored)}</summary>
<ul class="checks">{checks}</ul>
<p class="muted">{_e(HINTS["evidence"])} Работ корпуса: {sig.papers} · новостей: {sig.news} ·
доля свидетельств моложе года: {_pct(sig.recent_share)}.</p>
</details>
<details class="more"><summary>След в научном корпусе и патентах</summary>
<h3>Как это называют в работах</h3><p>{variants}</p>
{_core(sig)}
{_patents(sig)}
<p class="muted">Откуда кандидат: {_e(sig.stream)} поток.</p>
</details>
<details class="more"><summary>Источники — {len(sig.sources)}</summary>
{_sources_table(sig.sources)}
<p class="gen">Описание, преимущество, кейс и резюме источников — генеративное резюме модели
по перечисленным источникам.</p>
</details>
</div></details>"""


def _row(i: int, sig: Signal) -> str:
    """Строка ТОП — она же заголовок раскрывающегося инсайта: один список вместо таблицы и
    отдельного раздела карточек (progressive disclosure)."""
    why = f"{_e(sig.why[:160])}{'…' if len(sig.why) > 160 else ''}"
    cells = (
        # Чужое письмо в названии — и у отчётов, собранных до проверки в `card.parse`.
        f'<div class="tw">{_e(sig.label if foreign_script(sig.title) else sig.title)}<br>'
        f'<span class="muted small">{_e(sig.label)}</span>'
        f'{"" if sig.corroborated else "<br>" + _LOW_TRUST}</div>',
        f'<div class="small"><span class="lbl">Кто делает: </span>'
        f"{_e(', '.join(sig.companies[:4])) or '—'}</div>",
        f'<div><span class="lbl">Уверенность модели: </span>{_conf_cell(sig)}</div>',
        f'<div class="small"><span class="lbl">Соответствие запросу: </span>{_relevance(sig)}</div>',
        f'<div class="small"><span class="lbl">Ключевые предикторы: </span>'
        f"{_predictors_cell(sig)}</div>",
        f'<div class="small"><span class="lbl">Стадия: </span>{_stage(sig)}</div>',
        f'<div class="small muted">{why}</div>',
    )
    return (
        f'<div class="grid"><div><span class="arrow">▸</span> {i}</div>'
        + "".join(cells)
        + "</div>"
    )


def _models(result: AskResult) -> str:
    """Модели ответа и формат выбора (ТЗ §3.1: раскрытие и явное логирование)."""
    models = result.models or [
        {"role": "генерация", "model": result.model, "endpoint": ""},
        {"role": "эмбеддинги", "model": result.embedder, "endpoint": ""},
    ]
    rows = "".join(
        f"<tr><td>{_e(m['role'])}</td><td>{_e(m['model'])}</td>"
        f'<td class="muted">{_e(m.get("endpoint", ""))}</td></tr>'
        for m in models
        if m.get("model")
    )
    return (
        "<h3>Модели ответа</h3><p class=\"muted\">Выбор модели фиксирован конфигурацией "
        "стенда: автоматического выбора модели на ответ нет, каждая роль ниже выполняется "
        "названной моделью. Итоговая выдача строится только по найденным источникам — "
        "модель оценивает и пересказывает их, но не добавляет сигналов от себя.</p>"
        '<div class="wrap"><table>' + _cols(30, 35, 35)
        + "<thead><tr><th>Роль</th><th>Модель</th><th>Где работает</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


#: Полный ТОП: меньше — страница объясняет, почему не набралось.
TOP_FULL = 15


def _shortfall(result: AskResult) -> str:
    """Почему ТОП пуст или короче `TOP_FULL`: куда ушли оценённые кандидаты.

    Пустая таблица без слов читается как сбой. На самом деле это ответ: кандидаты были, и
    каждый ушёл в исключённые со своей причиной.
    """
    n = len(result.signals)
    if n >= TOP_FULL:
        return ""
    counts: dict[str, int] = {}
    for s in result.excluded:
        counts[s.kind] = counts.get(s.kind, 0) + 1
    reasons = "".join(
        f"<li>{_e(KIND_LABELS.get(kind, kind))} — {k}</li>"
        for kind, k in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    off = (
        f"<li>из них свидетельства не о предмете запроса «{_e(result.area_en)}» — "
        f"{result.off_topic}: модель сочла технологию новой, но источники про другую "
        "отрасль</li>"
        if result.off_topic
        else ""
    )
    head = (
        "Зарождающихся технологий по этому запросу не найдено."
        if n == 0
        else f"В ТОП набралось {n} из {TOP_FULL}."
    )
    return (
        f'<div class="note"><b>{head}</b> Оценено кандидатов: {result.candidates}, '
        f"научных работ найдено: {result.works_found}, новостей просмотрено: "
        f"{result.headlines_seen}. {'Все' if n == 0 else 'Остальные'} кандидаты "
        f"исключены:<ul>{reasons}{off}</ul>"
        "Это не сбой: ТОП собирается только из того, что найдено в источниках (научный "
        "корпус arXiv и OpenAlex, новости, КиберЛенинка, Хабр, GitHub). Если по теме "
        "мало исследований и мало стартапов, ТОП короткий или пустой. Помогает "
        "назвать в запросе, какие именно технологии предмета интересуют: материалы, "
        "техники, оборудование, методы исследования.</div>"
    )


def render(result: AskResult) -> str:
    head = (
        '<div class="grid thead"><div>№</div><div>Технология</div>'
        f'<div>Кто делает{_hint("companies")}</div>'
        f'<div>Уверенность модели{_hint("confidence")}</div>'
        f'<div>Соответствие запросу{_hint("relevance")}</div>'
        f'<div>Ключевые предикторы{_hint("predictors")}</div>'
        f'<div>Стадия{_hint("stage", right=True)}</div>'
        f'<div>Ключевое объяснение{_hint("why", right=True)}</div></div>'
    )
    cards = "".join(_card(i, s, result.history_runs) for i, s in enumerate(result.signals, 1))

    by_kind: dict[str, list[Signal]] = {}
    for s in result.excluded:
        by_kind.setdefault(s.kind, []).append(s)
    excluded = (
        "".join(
            f"<h3>{_e(KIND_LABELS.get(kind, kind))} — {len(items)}</h3><ul>"
            + "".join(f"<li>{_name(s)} — {_e(s.why) or _NO_WHY}</li>" for s in items)
            + "</ul>"
            for kind, items in by_kind.items()
        )
        or '<p class="muted">исключённых нет</p>'
    )

    thin = (
        "<h3>Не вошли в ТОП по месту (порядок — уверенность модели скоринга) — "
        f"{len(result.thin)}</h3><ul>"
        + "".join(
            f"<li>{_name(s)} — "
            f"{_e(', '.join(s.companies)) or 'компании в заголовках не названы'}</li>"
            for s in result.thin
        )
        + "</ul>"
        if result.thin
        else ""
    )

    categories = (
        "<h3>Снято как рыночная категория, а не сигнал — "
        f"{len(result.categories)}</h3>"
        '<p class="muted">В названии нет ни одного слова сверх самого запроса: такое '
        "название подходит любой компании отрасли сразу.</p><ul>"
        + "".join(f"<li>{_e(label)}</li>" for label in result.categories)
        + "</ul>"
        if result.categories
        else ""
    )

    live = " · ".join(
        f"{_e(_LIVE_NAMES.get(k, k))}: {v}" for k, v in result.live_by_source.items()
    )
    errors = (
        '<p class="no">Не ответили: '
        + " · ".join(
            f"{_e(_LIVE_NAMES.get(k, k))} ({_e(v)})" for k, v in result.live_errors.items()
        )
        + "</p>"
        if result.live_errors
        else ""
    )
    facets = "".join(f'<span class="chip">{_e(f)}</span>' for f in result.facets)
    submarkets = (
        '<p class="muted">Подрынки первого круга новостей: '
        + "".join(f'<span class="chip">{_e(f)}</span>' for f in result.submarkets_en)
        + "</p>"
        if result.submarkets_en
        else ""
    )
    confident_note = "по модели скоринга"
    stable = (
        f'<div class="stat"><b>{result.stable}</b>держатся в ≥ половине из '
        f"{result.history_runs} прошлых прогонов</div>"
        if result.history_runs
        else ""
    )

    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Слабые сигналы — {_e(result.query)}</title><style>{_CSS}</style></head><body><main>
<h1>Слабые сигналы</h1>
<p class="q">Запрос: <b>{_e(result.query)}</b></p>
<p class="muted">Направления поиска: {facets}</p>{submarkets}
<div class="stats">
<div class="stat"><b>{len(result.signals)}</b>в ТОП</div>
<div class="stat"><b>{result.confident}</b>уверенность выше 75%{_hint("confident")}<br>
<span class="muted">({confident_note})</span></div>
<div class="stat"><b>{result.candidates}</b>кандидатов оценено{_hint("candidates")}<br>
<a href="#excl">к списку не вошедших ↓</a></div>
<div class="stat"><b>{len(result.excluded)}</b>исключено: зрелое, стандарт, хайп, шум,
общая категория{_hint("excluded")}</div>
<div class="stat"><b>{len(result.thin)}</b>за пределами ТОП{_hint("thin")}</div>
<div class="stat"><b>{len(result.categories)}</b>снято как категория{_hint("categories", right=True)}</div>
<div class="stat"><b>{result.sources_processed}</b>источников обработано{_hint("sources", right=True)}</div>
<div class="stat"><b>{result.works_found}</b>научных работ найдено</div>
<div class="stat"><b>{result.headlines_seen}</b>новостей о раундах и пилотах</div>{stable}
</div>
<p class="muted">Живой поиск: {live}.
Запрос выполнен за {round(result.seconds)} с, {_e(result.started)} UTC.</p>{errors}

<h2>ТОП-{len(result.signals)} слабых сигналов</h2>
<p class="muted">Нажмите на строку, чтобы открыть инсайт: описание, преимущество, кейс,
хронологию и источники. Значок ⓘ объясняет колонку.</p>
{_shortfall(result)}
{head if result.signals else ""}{cards}

<details class="sec" id="excl"><summary><h2>Исключено из выдачи и почему — {len(result.excluded)
    + len(result.thin) + len(result.categories)}</h2> <span class="muted">▸ раскрыть</span></summary>
<div class="note">Зрелые технологии, отраслевые стандарты, слишком общие категории,
маркетинговый хайп и информационный шум в выдачу не попадают. Жанр определяет модель
по свидетельствам — по тем же источникам, что и для выданных сигналов.</div>
{excluded}
{thin}
{categories}
</details>

<details class="sec"><summary><h2>Модели ответа</h2> <span class="muted">▸ раскрыть</span>
</summary>{_models(result)}</details>

<details class="sec"><summary><h2>Как считается</h2> <span class="muted">▸ раскрыть</span>
</summary>
<div class="note">
<p><b>Уверенность модели.</b> {_e(HINTS["confidence"])}</p>
<p><b>Ключевые предикторы.</b> {_e(HINTS["predictors"])}</p>
<p><b>Соответствие запросу.</b> {_e(HINTS["relevance"])}</p>
<p><b>Стадия.</b> {_e(HINTS["stage"])}</p>
<p><b>Качество свидетельств.</b> {_e(HINTS["evidence"])}</p>
<p><b>Тренда и балла в выдаче нет намеренно.</b> Рубрика заказчика — стадия плюс
тренд — воспроизводится на 98 строках датасета из 100, и стадию система предсказывает
(36% точно, 63% с точностью до ступени). А вот тренд не предсказывает ни одна из двух
проверенных осей: свежесть свидетельств даёт 56% при константе 64%, скорость ядра в
корпусе — 38% при 66%. Балл собирается сложением, поэтому показывать его значило бы
выдавать половину шума за оценку.</p>
<p><b>Устойчивость</b> — в скольких из последних прошлых прогонов этого же запроса
(до пяти; прогоны с дословно одинаковым ТОП считаются одним) есть то же направление,
названное так же или по тем же значимым словам. Это согласие выдачи с самой собой,
а не качество сигнала, поэтому в уверенность и в порядок ТОП оно не входит. Без кэша
верх выдачи одного запроса совпадает между прогонами примерно на 9 позиций из 15
(по направлению, не дословно): состав ТОП зависит от того, что новости отдали в этот
день.</p>
<p><b>Доверенность</b> — по правилу ТЗ: наука, государство, университеты — высокая;
отраслевые медиа и вендоры — средняя; пресс-релизы, блоги, соцсети, агрегаторы —
пониженная и не могут быть единственным основанием.</p>
</div>
</details>
</main><script>
// Ссылка плитки ведёт в свёрнутый раздел — раскрыть его, иначе переход ничего не покажет.
document.querySelectorAll('a[href^="#"]').forEach(a => a.addEventListener('click', () => {{
  const d = document.querySelector(a.getAttribute('href'));
  if (d && d.tagName === 'DETAILS') d.open = true;
}}));
</script></body></html>"""


def to_json(result: AskResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2)


def _make(cls: type, d: dict[str, Any]) -> Any:
    """Поля, которых у класса нет (старые отчёты, вычисляемые), отбрасываются."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


def from_json(d: dict[str, Any]) -> AskResult:
    """Обратно к `to_json`: сохранённый отчёт → результат, который снова рендерится."""

    def signal(x: dict[str, Any]) -> Signal:
        s = _make(Signal, x)
        s.checks = [_make(Check, c) for c in x.get("checks") or []]
        s.sources = [_make(Source, c) for c in x.get("sources") or []]
        # Отчёты, собранные до проверки письма в `card.parse`, чистятся при показе: ТЗ
        # требует выдачу на русском, а gemma вставляла деванагари посреди слова.
        for f in ("description", "advantage", "case", "why", "relevance_why", "hypothesis"):
            setattr(s, f, drop_foreign(getattr(s, f)))
        s.facts = [f for f in s.facts if not foreign_script(f.get("text", ""))]
        s.stage = stage_floor(s.stage, s.judgments)
        for src in s.sources:
            src.summary_ru = drop_foreign(src.summary_ru)
        return s

    r = _make(AskResult, d)
    r.signals = [signal(x) for x in d.get("signals") or []]
    r.excluded = [signal(x) for x in d.get("excluded") or []]
    r.thin = [signal(x) for x in d.get("thin") or []]
    r.context = [_make(Source, c) for c in d.get("context") or []]
    return r
