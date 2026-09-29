"""Достоверность карточки: подтверждает ли свидетельство то, что карточка о нём говорит.

## Зачем

`weak/card.py` требует ссылку в каждом разделе и вычищает раздел без ссылок — но
СОВПАДАЕТ ли утверждение с тем, на что оно ссылается, не проверял никто. Для жюри
выдуманная сумма раунда — приговор всему решению, а не помарка.

## Две ступени, дешёвая первой

1. **Детерминированная** (без модели) — то, что можно сверить буквой. ⚠️ Дефекта тут
   ДВА РОДА, и складывать их в одно число нельзя:
   - **выдумка** — число из текста карточки (сумма раунда, год, доля), которого нет ни в
     одном процитированном свидетельстве, и ссылка `[n]` на источник, которого в карточке
     нет. Это ошибка, и она чинится;
   - **не проверить по карточке** — названная компания, которой нет в показанных
     источниках. ⚠️ Это НЕ ложь: имя пришло из заголовка денежного потока, прошло там
     эхо-проверку (`weak/money.py`), но сам заголовок в карточку не попал — показывается
     пять новостей из сотен. Для ТЗ это всё равно дефект: читатель не может сверить.
   Приём тот же, что у эхо-проверок `weak/money.py` и `weak/tech.py`: сверяется
   подстрока, а не смысл, и потому проверка сама не ошибается.

   ⚠️ **Сверяется ЧИСЛО, а не единица**: «$66M» против «$66 million» — одно и то же,
   и требование дословного совпадения единицы давало бы ложную тревогу на каждой
   второй сумме. Поэтому из числа вынимаются цифры («66», «1.7»), а разделители
   разрядов снимаются.

   ⚠️ **Свидетельство — это заголовок, дата и издатель**, то есть ровно та строка,
   которую видела модель, когда писала карточку. Год из даты считается подтверждённым.

   ⚠️ **Проверка ошибается только в СНИСХОДИТЕЛЬНУЮ сторону**: цифры свидетельств
   сверяются одной склеенной строкой, и «38» плюс «2026» дают подстроку «820», которая
   пропустит выдуманное число. Значит доля дефектов — оценка СНИЗУ; обвинить невиновного
   она не может, и это важнее.

2. **Модельная** — подтверждаемость источником. Приём стандартный для RAG: текст режется
   на утверждения, и каждое проверяется против процитированного — **faithfulness** у RAGAS
   (Es и др. 2023, arXiv:2309.15217), **атомарные факты** у FActScore (Min и др. 2023,
   arXiv:2305.14251), сама постановка — **AIS**, attributable to identified sources
   (Rashkin и др. 2023). Ответ: «подтверждает / не подтверждает / противоречит».

   ⚠️ **Расхождение с каноном**: канон режет на АТОМАРНЫЕ факты, мы — на предложения со
   ссылками. Причина: у нас утверждение и так короткое (карточка — 2–3 предложения на
   раздел), а лишнее дробление умножает вызовы модели на кандидата. Цена расхождения:
   предложение с двумя фактами, из которых подтверждён один, засчитывается целиком —
   значит доля подтверждённых у нас ЗАВЫШЕНА, и читать её надо как верхнюю оценку.

⚠️ **Правило зачёта, записанное ДО прогона**: доля неподтверждённых утверждений выше
10% — это дефект выдачи, и он чинится до показа, а не объявляется в сноске.

⚠️ **Судью проверяют рукой**, как в `scripts/judge_precision.py`: `--sample 20`
выписывает пары «утверждение — свидетельства», человек проставляет вердикт,
`--check` печатает согласие.

    python scripts/measure_card_facts.py --dir reports --strict   # только буква, без модели
    python scripts/measure_card_facts.py --dir reports            # обе ступени
    python scripts/measure_card_facts.py --sample 20
    python scripts/measure_card_facts.py --check
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
# ⚠️ Под перенаправленным выводом консоль Windows даёт cp1251, и один «⚠️» валит прогон.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from pelican.config import settings  # noqa: E402
from pelican.llm import LLMClient  # noqa: E402
from pelican.weak.stats import span  # noqa: E402

PAIRS = REPO / "scripts" / "facts_pairs.tsv"
#: ⚠️ Итоги кладутся РЯДОМ С ПРОГОНОМ: общий файл замер по чужим областям затирал молча.
TSV_NAME = "card-facts.tsv"
JSON_NAME = "card-facts.json"

#: Разделы карточки, которые пишет модель. Заголовок не проверяется: это название, а не
#: утверждение о мире.
FIELDS = ("description", "advantage", "case")

#: ⚠️ Ссылка бывает и «[1]», и «[1, 3]»: одиночный шаблон терял вторую, а её номер
#: потом читался как число из текста карточки.
_REF = re.compile(r"\[([\d,\s]+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
#: Число с разделителями разрядов: «1,7», «1.7», «570», «12 000».
_NUMBER = re.compile(r"\d[\d.,  ]*\d|\d")

VERDICTS = {1: "подтверждает", 2: "не подтверждает", 3: "противоречит"}
SUPPORTED = 1

PROMPT = """You are given ONE claim from an analyst card and the EVIDENCE the card cited
for it.

Answer with one verdict:

1 supported     - the evidence states this, or states it in other words.
2 unsupported   - the evidence does not state this. It may still be true in the world:
                  that does not matter, only the evidence does.
3 contradicted  - the evidence states something different: another number, another
                  company, another date, the opposite outcome.

A claim that generalises what the evidence says ("several startups do X" when the
evidence shows two of them) is supported. An invented number, company or date is
contradicted, not unsupported."""


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {"verdict": {"type": "integer"}, "why": {"type": "string"}},
        "required": ["verdict", "why"],
        "additionalProperties": False,
    }


def _client() -> LLMClient:
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )


# Одна сверка чисел на выдачу и замер: `card.parse` вычищает по ней предложения.
from pelican.weak.card import digits  # noqa: E402


def source_text(src: dict) -> str:
    """Строка свидетельства ровно в том виде, в каком её видела модель."""
    return " ".join(
        str(src.get(key, "") or "")
        for key in ("title", "date", "publisher", "summary_ru", "excerpt")
    )


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s.strip()]


def cited(text: str, by_n: dict[int, str]) -> tuple[list[str], list[int]]:
    """Тексты процитированных свидетельств и номера, которых в карточке нет."""
    got, missing = [], []
    for group in _REF.findall(text):
        for ref in group.replace(" ", "").split(","):
            if not ref:
                continue
            n = int(ref)
            if n in by_n:
                got.append(by_n[n])
            else:
                missing.append(n)
    return got, missing


#: Два РАЗНЫХ дефекта, и складывать их нельзя (см. докстринг модуля).
WRONG = "выдумка"
UNVERIFIABLE = "не проверить по карточке"


def strict_check(sig: dict) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Буквенная ступень. Возвращает дефекты с их родом и число проверок каждого рода."""
    by_n = {int(s.get("n", 0)): source_text(s) for s in sig.get("sources", [])}
    everything = " ".join(by_n.values())
    flat_digits = digits(everything)
    defects: list[tuple[str, str]] = []
    checks = {WRONG: 0, UNVERIFIABLE: 0}

    for field in FIELDS:
        text = str(sig.get(field, "") or "")
        if not text:
            continue
        _here, missing = cited(text, by_n)
        for n in missing:
            checks[WRONG] += 1
            defects.append((WRONG, f"{field}: ссылка [{n}] — такого источника в карточке нет"))
        # ⚠️ Номера ссылок из чисел карточки вычищаются: «[1, 3]» — это не сумма и не год.
        for raw in _NUMBER.findall(_REF.sub(" ", text)):
            num = digits(raw)
            # Однозначное число — это «2 компании» и «3 месяца»: сверять нечего.
            if len(num) < 2:
                continue
            checks[WRONG] += 1
            if num not in flat_digits:
                defects.append(
                    (WRONG, f"{field}: числа «{raw.strip()}» нет ни в одном свидетельстве")
                )

    for name in sig.get("companies", []):
        checks[UNVERIFIABLE] += 1
        if name.lower() not in everything.lower():
            defects.append(
                (UNVERIFIABLE, f"компания «{name}» есть в карточке, но не в её источниках")
            )
    return defects, checks


def claims(sig: dict) -> list[tuple[str, str, str]]:
    """Утверждения со ссылками: (раздел, предложение, тексты процитированного)."""
    by_n = {int(s.get("n", 0)): source_text(s) for s in sig.get("sources", [])}
    out = []
    for field in FIELDS:
        for sentence in sentences(str(sig.get(field, "") or "")):
            here, _missing = cited(sentence, by_n)
            if here:
                out.append((field, sentence, "\n".join(here)))
    return out


async def _judge_one(http: httpx.AsyncClient, claim: str, evidence: str) -> tuple[int, str]:
    got = await _client().json_completion(
        http,
        system=PROMPT,
        user=f"CLAIM: {claim}\n\nEVIDENCE:\n{evidence}",
        schema=_schema(),
        name="card_facts",
        max_tokens=250,
    )
    n = got.get("verdict")
    n = int(n) if isinstance(n, int) else 0
    if n not in VERDICTS:
        n = 0
    return n, str(got.get("why") or "")[:160]


async def _judge_many(items: list[tuple[str, str]]) -> list[tuple[int, str]]:
    async with httpx.AsyncClient() as http:
        out = []
        for i, (claim, evidence) in enumerate(items, 1):
            out.append(await _judge_one(http, claim, evidence))
            if i % 20 == 0:
                print(f"  · проверено {i} из {len(items)}", flush=True)
        return out


def _runs(folder: Path) -> list[tuple[str, dict]]:
    out = []
    for path in sorted(folder.glob("ask-cards-*.json")):
        out.append((path.stem.removeprefix("ask-cards-").replace("-", " "), json.loads(
            path.read_text(encoding="utf-8")
        )))
    return out


def _cards(folder: Path, limit: int) -> list[tuple[str, dict]]:
    cards = [(domain, sig) for domain, run in _runs(folder) for sig in run.get("signals", [])]
    return cards[:limit] if limit else cards


def measure(folder: Path, limit: int, strict_only: bool) -> None:
    out_tsv, out_json = folder / TSV_NAME, folder / JSON_NAME
    cards = _cards(folder, limit)
    if not cards:
        print(f"нет файлов ask-cards-*.json в {folder}: прогнать measure_ask.py --domains")
        return

    summary: dict[str, int] = {}
    all_defects: list[tuple[str, str, str, str]] = []
    checks = {WRONG: 0, UNVERIFIABLE: 0}
    clean = 0
    for domain, sig in cards:
        defects, n = strict_check(sig)
        for kind, count in n.items():
            checks[kind] += count
        clean += not [d for k, d in defects if k == WRONG]
        all_defects += [(domain, sig.get("label", ""), k, d) for k, d in defects]

    print(f"=== БУКВА: числа, ссылки и компании ({len(cards)} карточек) ===")
    for kind in (WRONG, UNVERIFIABLE):
        bad = sum(1 for _d, _l, k, _t in all_defects if k == kind)
        n = checks[kind]
        print(f"  {kind}: {bad} на {n} проверок ({bad / max(n, 1):.1%}) {span(bad, max(n, 1))}")
    print(
        f"  карточек без единой выдумки: {clean} из {len(cards)} "
        f"({clean / len(cards):.0%}) {span(clean, len(cards))}"
    )
    for domain, label, kind, d in all_defects[:16]:
        print(f"  · [{kind}] {domain} / {label[:36]}: {d}")
    if len(all_defects) > 16:
        print(f"  … и ещё {len(all_defects) - 16}, все в {out_tsv}")

    summary.update(
        {
            "карточек": len(cards),
            "без выдумок": clean,
            "проверок на выдумку": checks[WRONG],
            "выдумок": sum(1 for _d, _l, k, _t in all_defects if k == WRONG),
            "названных компаний": checks[UNVERIFIABLE],
            "компаний без источника в карточке": sum(
                1 for _d, _l, k, _t in all_defects if k == UNVERIFIABLE
            ),
        }
    )
    rows: list[list[str]] = [[d, lab, k, text, ""] for d, lab, k, text in all_defects]

    if not strict_only:
        items, meta = [], []
        for domain, sig in cards:
            for field, claim, evidence in claims(sig):
                items.append((claim, evidence))
                meta.append((domain, sig.get("label", ""), field))
        print("")
        print(f"=== СМЫСЛ: {len(items)} утверждений против свидетельств ===", flush=True)
        got = asyncio.run(_judge_many(items))
        verdicts = [n for n, _why in got]
        ok = verdicts.count(SUPPORTED)
        print(
            f"подтверждено: {ok} из {len(items)} ({ok / max(len(items), 1):.0%}) "
            f"{span(ok, len(items))}"
        )
        for n, name in VERDICTS.items():
            if n != SUPPORTED and verdicts.count(n):
                print(f"  {name}: {verdicts.count(n)}")
        lost = verdicts.count(0)
        if lost:
            print(f"  не разобрано: {lost}")
        summary["утверждений"] = len(items)
        summary["подтверждено"] = ok
        share = 1 - ok / max(len(items), 1)
        print(
            f"⚠️ доля неподтверждённых {share:.0%} — "
            + ("выше правила 10%: это дефект выдачи" if share > 0.10 else "в пределах правила 10%")
        )
        for (domain, label, field), item, (n, why) in zip(meta, items, got, strict=True):
            if n != SUPPORTED:
                verdict = VERDICTS.get(n, "не разобран")
                rows.append([domain, label, field, item[0], f"{verdict}: {why}"])

    summary.setdefault("утверждений", 0)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    with out_tsv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["домен", "карточка", "раздел", "утверждение/дефект", "вердикт"])
        w.writerows(rows)
    print("")
    print(f"выписано: {out_tsv}")


def sample(folder: Path, n: int, seed: int) -> None:
    items = [
        (domain, sig.get("label", ""), field, claim, evidence)
        for domain, sig in _cards(folder, 0)
        for field, claim, evidence in claims(sig)
    ]
    random.Random(seed).shuffle(items)
    with PAIRS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["домен", "карточка", "раздел", "утверждение", "свидетельства", "мой вердикт"])
        for domain, label, field, claim, evidence in items[:n]:
            w.writerow([domain, label, field, claim, evidence, ""])
    print(f"выписано {min(n, len(items))} утверждений: {PAIRS}")
    print("колонка «мой вердикт» — одно из: " + " / ".join(VERDICTS.values()))


def check() -> None:
    rows = list(csv.DictReader(PAIRS.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    filled = [r for r in rows if (r.get("мой вердикт") or "").strip()]
    if not filled:
        print(f"колонка «мой вердикт» пуста в {PAIRS}: размечать нечего")
        return
    got = asyncio.run(_judge_many([(r["утверждение"], r["свидетельства"]) for r in filled]))
    agree = 0
    for r, (n, why) in zip(filled, got, strict=True):
        mine = r["мой вердикт"].strip().lower()
        theirs = VERDICTS.get(n, "не разобран")
        ok = mine == theirs
        agree += ok
        mark = "  " if ok else "РАСХОД"
        print(f"{mark} я={mine:<16} модель={theirs:<16} {r['утверждение'][:50]} | {why[:50]}")
    print(f"\nсогласие с рукой: {agree} из {len(filled)} ({agree / len(filled):.0%})")
    if agree / len(filled) < 0.8:
        print("⚠️ ниже 80%: доля неподтверждённых объявляется ОЦЕНКОЙ, а не метрикой")


def main() -> None:
    ap = argparse.ArgumentParser(description="достоверность карточек: буква и смысл")
    ap.add_argument("--dir", default="reports", help="папка с ask-cards-*.json")
    ap.add_argument("--limit", type=int, default=0, help="первые N карточек")
    ap.add_argument("--strict", action="store_true", help="только буквенная ступень, без модели")
    ap.add_argument("--sample", type=int, default=0, help="выписать N утверждений на разметку")
    ap.add_argument("--seed", type=int, default=20260916, help="зерно выборки")
    ap.add_argument("--check", action="store_true", help="согласие модели с ручной разметкой")
    args = ap.parse_args()
    folder = REPO / args.dir
    if args.sample:
        sample(folder, args.sample, args.seed)
    elif args.check:
        check()
    else:
        measure(folder, args.limit, args.strict)


if __name__ == "__main__":
    main()
