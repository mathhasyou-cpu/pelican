"""Точность ТОП: какая доля выданных карточек — действительно слабый сигнал.

## Зачем

Про выдачу до сих пор меряли только ПОЛНОТУ — сколько строк методолога она находит
(`scripts/judge_match.py`, 22 из 100). Какая доля НАШИХ карточек хороша, не мерил никто,
а на защите смотрят именно на это: жюри видит пятнадцать карточек, а не чужой список.

## Как судит

**LLM-as-judge** (Zheng и др. 2023, arXiv:2306.05685) с эхо-проверкой: модель получает ОДНУ
карточку (название, русский текст, свидетельства) и область запроса, отвечает одним вердиктом
из пяти — ровно классы исключения из ТЗ. Вердикт вне словаря отбрасывается, как в
`weak.money`, и считается отдельно.

⚠️ **Судье не показываются наши оценки**: ни `kind`, ни `stage`, ни уверенность, ни позиция
в ТОП. Иначе он подтверждает нашу же разметку, а не судит.

⚠️ **Довод ПРОТИВ пишется раньше вердикта** — это рассуждение до оценки, приём G-Eval
(Liu и др. 2023, arXiv:2303.16634): порядок полей схемы задаёт порядок декодирования.
Заведено не из красоты: на пробе судья назвал сигналом 3 из 3 карточек, которые наша же
модель сняла как зрелые, — чистое соглашательство.

⚠️ **Самая опасная беда здесь — self-preference / self-enhancement bias** (Zheng и др. 2023):
судья по умолчанию ТА ЖЕ модель, что писала карточку, а такой судья завышает оценку своим
текстам (в литературе 10–25%). Отсюда `--model`: канонический способ — судить ДРУГОЙ
моделью, и расхождение двух судей само по себе мера этого перекоса. На этой машине под роль
независимого судьи есть `gigachat3.1-10b-a1.8b` (другое семейство, и он же в белом списке ТЗ).

## Чем проверяется сам судья

⚠️ **Снисходительность измеряется, а не предполагается** — двумя пробами, и обе стоят даром,
потому что берутся из тех же прогонов:

- **чужой домен** — карточка из ТОП другого домена, предъявленная под этот запрос.
  Судья обязан назвать её `offtopic`; доля, которую он назвал сигналом, — это его пол шума.
  ⚠️ Верхняя оценка пола, а не точная: шесть доменов датасета соседние (edge, инфраструктура
  и промышленный ИИ перекрываются по существу), и часть засчитанного судьёй — не
  снисходительность, а правда. Читать её надо вместе с парой доменов, а не одним числом.
- **снятые нашей моделью** (`excluded`: жанр не «зарождающаяся») — предъявляются как обычные
  карточки. ⚠️ Это не независимая разметка, а сверка двух оценок: если судья зовёт сигналом
  то, что наша модель сняла как зрелое, расходятся именно они, и знать об этом надо.

⚠️ **Строки `weak_controls.tsv` сюда подмешивать нельзя**: у них нет свидетельств, и судья
отличит их по пустым разделам, а не по существу. Трудный контроль меряется там, где меряется
жанр (`scripts/measure_signal_kinds.py`), — на равных с положительными.

⚠️ **Судью проверяют рукой.** `--sample 20` выписывает случайные карточки без наших оценок,
человек проставляет вердикт, `--check` печатает согласие — как `judge_pairs.tsv` у покрытия
(там 13 из 14). Согласие ниже 80% — число объявляется оценкой СВЕРХУ, а не метрикой.

    python scripts/judge_precision.py --sample 20      # выписать карточки на разметку
    python scripts/judge_precision.py --check          # согласие судьи с рукой
    python scripts/judge_precision.py --dir reports    # сам замер
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
# ⚠️ Под перенаправленным выводом консоль Windows даёт cp1251, и один «⚠️» в сводке
# валит прогон целиком.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from pelican.config import settings  # noqa: E402
from pelican.llm import LLMClient  # noqa: E402
from pelican.weak.stats import span  # noqa: E402

PAIRS = REPO / "scripts" / "precision_pairs.tsv"
#: Имена итогов. ⚠️ Кладутся РЯДОМ С ПРОГОНОМ, а не в общий файл: замер по чужим областям
#: молча писал поверх основного, и числа шести доменов терялись — та же мина, что была у
#: покрытия (`scripts/judge_match.py`).
TSV_NAME = "judge-precision.tsv"
JSON_NAME = "judge-precision.json"

#: Вердикты судьи — это классы исключения из ТЗ, теми же словами, что в `weak/kinds.py`.
VERDICTS = {
    1: "signal",
    2: "mature",
    3: "market",
    4: "offtopic",
    5: "noise",
}
GOOD = 1
#: Сколько свидетельств показывать. ⚠️ Судья должен видеть то же, что видел оценщик:
#: больше — он судит по объёму, меньше — по одному названию.
EVIDENCE_SHOWN = 7
EVIDENCE_CHARS = 300

PROMPT = """You are given ONE technology card produced by a system in answer to a user's
request, and the AREA the user asked about.

Answer with ONE verdict:

1 signal   - an EMERGING technology: a concrete mechanism, artifact, protocol or
             capability that a few players are building early (out of stealth, seed or
             A rounds, first pilots, first papers). No formed market yet.
2 mature   - widely adopted already: a formed market, clear leaders, standard practice.
3 market   - not a technology but a MARKET CATEGORY or a bundle of products
             ("enterprise ai security platforms", "edge ai deployment solutions").
4 offtopic - a real emerging technology, but NOT in the area the user asked about.
             Hardware, privacy or networking that the asked area itself uses is NOT
             offtopic; a different industry is.
5 noise    - not a technology at all (a company, a person, a report, a funding event
             with no named mechanism), or the evidence supports nothing.

Judge the DIRECTION, not the writing quality. Judge only by the evidence shown.

Before the verdict, write in "against" the STRONGEST argument that this is NOT an emerging
signal - that it is a market category, already mature, off the asked area, or unsupported
by the evidence. Write that argument even when you end up answering 1."""


def _schema() -> dict:
    return {
        "type": "object",
        # ⚠️ Порядок полей — часть приёма: схема декодируется по порядку, и довод ПРОТИВ
        # пишется раньше вердикта. Без него судья соглашается со всем, что ему показали:
        # на пробе он назвал сигналом 3 из 3 карточек, снятых нашей же моделью.
        "properties": {
            "against": {"type": "string"},
            "verdict": {"type": "integer"},
            "why": {"type": "string"},
        },
        "required": ["against", "verdict", "why"],
        "additionalProperties": False,
    }


#: Модель судьи. Ставится из `--model`; по умолчанию — та же, что писала карточки.
MODEL = settings.llm_model


def _client() -> LLMClient:
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=MODEL,
        timeout_s=settings.llm_timeout_s,
    )


def card_text(sig: dict, area: str) -> str:
    """Карточка глазами судьи. ⚠️ Без жанра, стадии, уверенности и позиции."""
    lines = [
        f"THE USER ASKED ABOUT: {area}",
        "",
        f"CARD: {sig.get('label', '')}",
        f"TITLE (ru): {sig.get('title', '')}",
        f"DESCRIPTION (ru): {sig.get('description', '')}",
        f"ADVANTAGE (ru): {sig.get('advantage', '')}",
        f"CASE (ru): {sig.get('case', '')}",
        f"COMPANIES NAMED: {', '.join(sig.get('companies', [])) or '—'}",
        # ⚠️ След корпуса — СВИДЕТЕЛЬСТВО, которое видел оценщик, а не наш вердикт, и
        # без него судить зрелость не на чем: «13 575 работ с 2016» против «104 работы
        # с 2026» — это и есть разница между зрелым и зарождающимся.
        f'CORPUS: core "{sig.get('core', '')}" — {sig.get('core_works', 0)} papers since '
        f'{sig.get('core_since') or "?"}; this exact direction — '
        f'{sig.get('label_works', 0)} papers',
        "",
        "EVIDENCE:",
    ]
    for src in sig.get("sources", [])[:EVIDENCE_SHOWN]:
        who = f", {src.get('publisher')}" if src.get("publisher") else ""
        lines.append(
            f"[{src.get('n')}] ({src.get('date')}, {src.get('evidence')}{who}) "
            f"{str(src.get('title', ''))[:EVIDENCE_CHARS]}"
        )
    return "\n".join(lines)


async def _judge_one(http: httpx.AsyncClient, text: str) -> tuple[int, str]:
    got = await _client().json_completion(
        http,
        system=PROMPT,
        user=text,
        schema=_schema(),
        name="judge_precision",
        max_tokens=300,
    )
    n = got.get("verdict")
    n = int(n) if isinstance(n, int) else 0
    if n not in VERDICTS:
        # ⚠️ Вердикт вне словаря — ответ не разобран; в долю он не идёт ни с какой стороны.
        n = 0
    return n, str(got.get("why") or "")[:160]


async def _judge_many(texts: list[str]) -> list[tuple[int, str]]:
    async with httpx.AsyncClient() as http:
        out = []
        for i, text in enumerate(texts, 1):
            out.append(await _judge_one(http, text))
            if i % 10 == 0:
                print(f"  · судья прошёл {i} из {len(texts)}", flush=True)
        return out


def _runs(folder: Path) -> list[tuple[str, dict]]:
    """Прогоны по доменам: файлы, которые пишет `measure_ask.py --domains`."""
    out = []
    for path in sorted(folder.glob("ask-cards-*.json")):
        domain = path.stem.removeprefix("ask-cards-").replace("-", " ")
        out.append((domain, json.loads(path.read_text(encoding="utf-8"))))
    return out


def _area(run: dict) -> str:
    return run.get("area_en") or run.get("query_en") or run.get("query", "")


def _tally(verdicts: list[int]) -> dict[str, int]:
    counted = {name: verdicts.count(n) for n, name in VERDICTS.items()}
    lost = verdicts.count(0)
    if lost:
        counted["не разобран"] = lost
    return counted


def judge(folder: Path) -> None:
    runs = _runs(folder)
    if not runs:
        print(f"нет файлов ask-cards-*.json в {folder}: прогнать measure_ask.py --domains")
        return
    out = folder / TSV_NAME
    summary_path = folder / (
        JSON_NAME if settings.llm_model == MODEL
        else JSON_NAME.replace(".json", f"-{MODEL.split('/')[-1]}.json")
    )
    good = total = 0
    per_domain: dict[str, dict[str, int]] = {}
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["домен", "n", "карточка", "вердикт", "почему"])
        for domain, run in runs:
            signals = run.get("signals", [])
            got = asyncio.run(_judge_many([card_text(s, _area(run)) for s in signals]))
            for i, (sig, (n, why)) in enumerate(zip(signals, got, strict=True), 1):
                w.writerow([domain, i, sig.get("label", ""), VERDICTS.get(n, "не разобран"), why])
            verdicts = [n for n, _why in got]
            hits = verdicts.count(GOOD)
            good += hits
            total += len(verdicts)
            print(
                f"\n== {domain}: сигналов {hits} из {len(verdicts)} "
                f"({hits / max(len(verdicts), 1):.0%}) {span(hits, len(verdicts))}",
                flush=True,
            )
            per_domain[domain] = {k: v for k, v in _tally(verdicts).items() if v}
            print("   " + " · ".join(f"{k} {v}" for k, v in _tally(verdicts).items() if v))
    print("")
    print(f"-- точность ТОП: {good} из {total} ({good / max(total, 1):.0%}) {span(good, total)}")
    print(f"   выписано: {out}")
    summary = {"судья": MODEL, "signals": good, "cards": total, "by_domain": per_domain}
    summary["probes"] = probes(runs)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   сводка: {summary_path}")


def probes(runs: list[tuple[str, dict]]) -> dict[str, dict[str, int]]:
    """Две пробы на снисходительность судьи. ⚠️ Обе — из тех же прогонов, даром."""
    alien: list[str] = []
    # ⚠️ На одном прогоне проба вырождается: «чужим» оказался бы он сам.
    for i, (_domain, run) in enumerate(runs if len(runs) > 1 else []):
        other = runs[(i + 1) % len(runs)][1]
        # Верх ЧУЖОГО домена под ЭТОТ запрос: судья обязан сказать offtopic.
        for sig in other.get("signals", [])[:3]:
            alien.append(card_text(sig, _area(run)))
    dropped: list[str] = []
    for _domain, run in runs:
        for sig in run.get("excluded", [])[:3]:
            dropped.append(card_text(sig, _area(run)))

    got: dict[str, dict[str, int]] = {}
    for name, texts, expect in (
        ("чужой домен", alien, "offtopic"),
        ("снятые нашей моделью", dropped, "не сигнал"),
    ):
        if not texts:
            continue
        print(f"\n-- проба «{name}» ({len(texts)} карточек, ожидается {expect})", flush=True)
        verdicts = [n for n, _why in asyncio.run(_judge_many(texts))]
        wrong = verdicts.count(GOOD)
        print(
            f"   судья назвал сигналом {wrong} из {len(texts)} "
            f"({wrong / len(texts):.0%}) {span(wrong, len(texts))}"
        )
        print("   " + " · ".join(f"{k} {v}" for k, v in _tally(verdicts).items() if v))
        got[name] = {"карточек": len(texts), "названо сигналом": wrong}
    return got


def _sources_column(sig: dict) -> str:
    """Источники строкой: заголовок, издатель, дата и ссылка — чтобы было что открыть."""
    out = []
    for src in sig.get("sources", [])[:EVIDENCE_SHOWN]:
        who = src.get("publisher") or src.get("domain") or src.get("evidence", "")
        out.append(
            f"[{src.get('n')}] {src.get('title', '')} — {who}, {src.get('date', '')} "
            f"{src.get('url', '')}"
        )
    return " || ".join(out)


def _corpus_column(sig: dict) -> str:
    """Зрелость человеческими словами: сколько работ у ядра и с какого года."""
    since = sig.get("core_since") or "?"
    return (
        f"ядро «{sig.get('core', '')}»: {sig.get('core_works', 0)} работ с {since}; "
        f"само направление: {sig.get('label_works', 0)} работ"
    )


def sample(folder: Path, n: int, seed: int) -> None:
    """Выписать случайные карточки на РУЧНУЮ разметку — без наших оценок.

    ⚠️ Вопрос человеку БИНАРНЫЙ: «показал бы я эту карточку заказчику как слабый сигнал».
    Пять классов нужны модели, чтобы назвать причину отказа; человека они заставляют
    угадывать чужую рубрику, а нужен от него продуктовый ответ.

    ⚠️ Показывается ВСЁ, по чему такой ответ возможен: русский текст карточки, названные
    компании, след технологии в корпусе (сколько работ и с какого года — это и есть
    зрелость) и источники со ссылками. Без следа и ссылок задача нерешаема — первая же
    попытка разметки это показала.
    """
    runs = _runs(folder)
    pool = [
        (domain, i, sig)
        for domain, run in runs
        for i, sig in enumerate(run.get("signals", []), 1)
    ]
    random.Random(seed).shuffle(pool)
    with PAIRS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=chr(9))
        w.writerow(
            [
                "домен",
                "n",
                "направление",
                "название (ru)",
                "о чём",
                "преимущество",
                "кейс",
                "компании",
                "след в корпусе",
                "источники",
                "годится? да/нет",
                "если нет — почему",
            ]
        )
        for domain, i, sig in pool[:n]:
            w.writerow(
                [
                    domain,
                    i,
                    sig.get("label", ""),
                    sig.get("title", ""),
                    sig.get("description", ""),
                    sig.get("advantage", ""),
                    sig.get("case", ""),
                    ", ".join(sig.get("companies", [])),
                    _corpus_column(sig),
                    _sources_column(sig),
                    "",
                    "",
                ]
            )
    print(f"выписано {min(n, len(pool))} карточек: {PAIRS}")
    print("колонка «годится? да/нет» — один ответ: да или нет")
    print("причину можно не писать; если пишете — зрелое / рынок / не по теме / шум")


def check(folder: Path) -> None:
    """Согласие судьи с рукой. ⚠️ Сравнение БИНАРНОЕ: «годится» против вердикта `signal`.

    Человек отвечает продуктовым «да/нет», судья — одним из пяти классов; общее у них
    ровно одно — годится ли карточка в выдачу. По нему и считается согласие.
    """
    rows = list(csv.DictReader(PAIRS.read_text(encoding="utf-8").splitlines(), delimiter=chr(9)))
    filled = [r for r in rows if (r.get("годится? да/нет") or "").strip()]
    if not filled:
        print(f"колонка «годится? да/нет» пуста в {PAIRS}: размечать нечего")
        return
    # Прогоны читаются ОДИН раз: в цикле это давало чтение всех JSON на каждую строку.
    runs = dict(_runs(folder))
    by_key = {
        (domain, i): sig
        for domain, run in runs.items()
        for i, sig in enumerate(run.get("signals", []), 1)
    }
    items, kept = [], []
    for r in filled:
        sig = by_key.get((r["домен"], int(r["n"])))
        if sig is None:
            print(f"⚠️ карточки {r['домен']} #{r['n']} нет в прогонах {folder}: пропускаю")
            continue
        items.append(card_text(sig, _area(runs[r["домен"]])))
        kept.append(r)
    if not items:
        return
    got = asyncio.run(_judge_many(items))
    agree = 0
    for r, (n, why) in zip(kept, got, strict=True):
        mine = (r["годится? да/нет"] or "").strip().lower().startswith("д")
        theirs = n == GOOD
        ok = mine == theirs
        agree += ok
        mark = "  " if ok else "РАСХОД"
        verdict = VERDICTS.get(n, "не разобран")
        print(
            f"{mark} я={'да' if mine else 'нет':<3} судья={verdict:<9} "
            f"{r['направление'][:38]:<38} | {why[:52]}"
        )
    share = agree / len(kept)
    print("")
    print(f"согласие судьи с рукой: {agree} из {len(kept)} ({share:.0%}) {span(agree, len(kept))}")
    mine_yes = sum(1 for r in kept if (r["годится? да/нет"] or "").strip().lower().startswith("д"))
    print(f"точность ПО РУКЕ: {mine_yes} из {len(kept)} ({mine_yes / len(kept):.0%}) "
          f"{span(mine_yes, len(kept))} — это и есть якорь, число судьи читается через него")
    if share < 0.8:
        print("⚠️ согласие ниже 80%: число судьи объявляется ОЦЕНКОЙ СВЕРХУ, а не метрикой")


def main() -> None:
    ap = argparse.ArgumentParser(description="точность ТОП: доля настоящих сигналов")
    ap.add_argument("--dir", default="reports", help="папка с ask-cards-*.json")
    ap.add_argument("--sample", type=int, default=0, help="выписать N карточек на разметку")
    ap.add_argument("--seed", type=int, default=20260916, help="зерно выборки")
    ap.add_argument("--check", action="store_true", help="согласие судьи с ручной разметкой")
    ap.add_argument(
        "--model",
        default=settings.llm_model,
        help="модель судьи; независимый судья снимает self-preference (см. докстринг)",
    )
    args = ap.parse_args()
    global MODEL
    MODEL = args.model
    print(f"судья: {MODEL}")
    folder = REPO / args.dir
    if args.sample:
        sample(folder, args.sample, args.seed)
    elif args.check:
        check(folder)
    else:
        judge(folder)


if __name__ == "__main__":
    main()
