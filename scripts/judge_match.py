"""Сверка ТОП с датасетом ПО НАПРАВЛЕНИЮ, а не по общей компании.

## Зачем ещё одна метрика

Совпадение по компании — подсказка, а не мерило: одно направление заказчик и мы находим
через разных игроков, а общий «Cisco» связывает несовпадающее. Замерено, что цена деления у
неё ±1 совпадение, то есть она не отличает правку от перетасовки кандидатов на границе
отсечения (docs/todo.md §75), и три подряд правки провалили порог ровно на единицу.

## Как судит

**LLM-as-judge с эхо-проверкой**: модель получает одно название из ТОП и ВСЕ строки датасета
этого домена, отвечает номером строки или нулём. Номер вне диапазона — ответ отбрасывается,
как в `weak.money`. ⚠️ Судья видит только НАЗВАНИЯ: ни компаний, ни свидетельств, иначе он
начнёт подтверждать нашу же выдачу.

⚠️ **Судью проверяют, а не принимают на веру**: `--check` гоняет его на 14 парах, размеченных
руками (`scripts/judge_pairs.tsv`), и печатает согласие. Метрикой судья становится только
если согласие высокое; иначе это ещё одна догадка.

    python scripts/judge_match.py --check
    python scripts/judge_match.py --dir reports            # судить текущие TSV
    python scripts/judge_match.py --dir reports/ask-baseline
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from pelican.config import settings  # noqa: E402
from pelican.llm import LLMClient  # noqa: E402

DATASET = REPO / "scripts" / "weak_signals_100.tsv"
PAIRS = REPO / "scripts" / "judge_pairs.tsv"

PROMPT = """You are given ONE technology direction found by a system, and a numbered list of
technology directions written by an analyst.

Answer with the number of the analyst's direction that describes THE SAME technology, or 0
if none of them does.

The same direction means the same technical thing is being built: the same mechanism,
artifact, protocol or capability. Different wording, different companies and a broader or
narrower phrasing are fine. A shared field ("both are about AI security", "both are about
payments") is NOT the same direction - answer 0 for those."""


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {"row": {"type": "integer"}, "why": {"type": "string"}},
        "required": ["row", "why"],
        "additionalProperties": False,
    }


def _client() -> LLMClient:
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )


async def _judge_one(http: httpx.AsyncClient, label: str, rows: list[str]) -> tuple[int, str]:
    listing = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rows))
    got = await _client().json_completion(
        http,
        system=PROMPT,
        user=f"FOUND: {label}\n\nANALYST'S DIRECTIONS:\n{listing}",
        schema=_schema(),
        name="judge_match",
        max_tokens=300,
    )
    n = got.get("row")
    n = int(n) if isinstance(n, int) else 0
    if not (0 <= n <= len(rows)):
        # ⚠️ Номер вне диапазона — модель сочиняет; это не совпадение.
        n = 0
    return n, str(got.get("why") or "")[:160]


async def _judge_many(items: list[tuple[str, list[str]]]) -> list[tuple[int, str]]:
    async with httpx.AsyncClient() as http:
        out = []
        for label, rows in items:
            out.append(await _judge_one(http, label, rows))
        return out


def _dataset() -> dict[str, list[str]]:
    with DATASET.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    by_domain: dict[str, list[str]] = {}
    for r in rows:
        by_domain.setdefault(r["domain"], []).append(r["tech"])
    return by_domain


def _top(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as f:
        out = []
        for row in csv.reader(f, delimiter="\t"):
            if not row or not row[0].isdigit():
                if out:
                    break
                continue
            out.append(row[1])
        return out


def check() -> None:
    with PAIRS.open(encoding="utf-8", newline="") as f:
        pairs = list(csv.DictReader(f, delimiter="\t"))
    items = [(p["label"], [p["row"]]) for p in pairs]
    got = asyncio.run(_judge_many(items))
    agree = 0
    for p, (n, why) in zip(pairs, got, strict=True):
        mine = p["same"] == "1"
        theirs = n == 1
        ok = mine == theirs
        agree += ok
        mark = "  " if ok else "РАСХОД"
        print(f"{mark} я={int(mine)} судья={int(theirs)}  {p['label'][:44]:<44} | {why[:60]}")
    print(f"\nсогласие судьи с рукой: {agree} из {len(pairs)} ({agree / len(pairs):.0%})")


def judge(folder: Path) -> None:
    """Печатает ПОКРЫТИЕ строк заказчика, а не число совпавших карточек.

    ⚠️ Считать карточки нельзя: две разные карточки попадают в одну строку датасета
    («Кремний под агентный ИИ» нашли и «long-term memory», и «long context window»), и
    счёт карточек растёт от размера ТОП. У покрытия знаменатель постоянный — это строки
    методолога, и накрутить его размером выдачи нельзя.
    """
    data = _dataset()
    covered = all_rows = 0
    # ⚠️ Итог ложится РЯДОМ С ПРОГОНОМ, а не в общий файл: иначе покрытие бэктеста
    # (прогон на срезе 2024) молча затирает покрытие сегодняшней выдачи, и в отчёт
    # уходит чужое число.
    out = folder / "judge-coverage.tsv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["домен", "карточка", "строка датасета", "почему"])
        for domain, rows in data.items():
            path = folder / f"ask-match-{domain.replace(' ', '-')}.tsv"
            if not path.exists():
                continue
            labels = _top(path)
            got = asyncio.run(_judge_many([(lab, rows) for lab in labels]))
            hits = [
                (lab, rows[n - 1], why)
                for lab, (n, why) in zip(labels, got, strict=True)
                if n
            ]
            for lab, row, why in hits:
                w.writerow([domain, lab, row, why])
            uniq = {row for _lab, row, _why in hits}
            covered += len(uniq)
            all_rows += len(rows)
            print(
                f"\n== {domain}: покрыто {len(uniq)} строк из {len(rows)}"
                f" ({len(labels)} в ТОП)"
            )
            for row in sorted(uniq):
                print(f"   {row[:88]}")
    print(f"\n-- покрытие датасета: {covered} строк из {all_rows}")
    print(f"   выписано: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="проверить судью на ручной разметке")
    ap.add_argument("--dir", default="reports", help="папка с ask-match-*.tsv")
    args = ap.parse_args()
    if args.check:
        check()
    else:
        judge(REPO / args.dir)


if __name__ == "__main__":
    main()
