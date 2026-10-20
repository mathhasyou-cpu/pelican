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
руками (`scripts/judge_pairs.tsv`), в рабочей постановке — со всем списком домена — и
печатает согласие и долю ложных сопоставлений на чужом домене. Метрикой судья становится
только если согласие высокое; иначе это ещё одна догадка.

    python scripts/judge_match.py --check
    python scripts/judge_match.py --dir reports            # судить текущие TSV
    python scripts/judge_match.py --dir reports/ask-baseline
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from pelican import locks  # noqa: E402
from pelican.config import settings  # noqa: E402
from pelican.llm import LLMClient  # noqa: E402
from pelican.runner import MODEL_LOCK  # noqa: E402

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


def _locate(text: str, rows: list[str]) -> str:
    """Строка датасета, которую сокращённо называет `text`: наибольшая доля слов сокращения,
    стоящих в строке (≥ 0.7; строка длиннее сокращения, и Жаккар её штрафовал бы)."""
    if text in rows:
        return text

    def words(s: str) -> set[str]:
        return {w for w in re.findall(r"\w+", s.lower()) if len(w) > 2}

    mine = words(text)

    def share(r: str) -> float:
        return len(mine & words(r)) / max(len(mine), 1)

    best = max(rows, key=share)
    score = share(best)
    if score < 0.7:
        raise SystemExit(f"строка пары не найдена в датасете уверенно: {text[:60]} (лучшая {score:.2f})")
    return best


def check() -> None:
    """Судья — в ТОЙ ЖЕ постановке, что в работе: весь список домена, ответ — номер.

    ⚠️ Бинарный вопрос «та же строка или нет» по одной строке — другая задача: снисходительность
    к зонтикам рождается именно при выборе ближайшего из похожих, и согласие на бинарной
    задаче о точности номера ничего не говорит (PEL-G-11; валидировать LLM-судью надо в
    рабочей постановке и метрикой задачи). Засчитывается: `same=1` — ровно номер этой строки;
    `same=0` — любой ответ, кроме этой строки. Проба «чужой домен»: пара с `same=1` против
    списка ДРУГОГО домена, верный ответ — 0 (доля ложных сопоставлений).
    """
    with PAIRS.open(encoding="utf-8", newline="") as f:
        pairs = list(csv.DictReader(f, delimiter="\t"))
    data = _dataset()
    domain_of = {row: d for d, rows in data.items() for row in rows}
    # В парах строки записаны сокращённо — ищется строка датасета с наибольшим общим
    # набором слов; не нашлась уверенно — отказ, а не угадывание.
    for p in pairs:
        p["row"] = _locate(p["row"], list(domain_of))
    items = [(p["label"], data[domain_of[p["row"]]]) for p in pairs]
    got = asyncio.run(_judge_many(items))
    agree = 0
    for p, (n, why) in zip(pairs, got, strict=True):
        own = data[domain_of[p["row"]]].index(p["row"]) + 1
        same = p["same"] == "1"
        ok = n == own if same else n != own
        agree += ok
        mark = "  " if ok else "РАСХОД"
        print(f"{mark} я={int(same)} строка={own} судья={n}  {p['label'][:40]:<40} | {why[:60]}")
    print(f"\nсогласие судьи с рукой (весь список домена): {agree} из {len(pairs)} ({agree / len(pairs):.0%})")

    domains = sorted(data)
    probes = []
    for p in pairs:
        if p["same"] != "1":
            continue
        home = domain_of[p["row"]]
        other = domains[(domains.index(home) + 1) % len(domains)]
        probes.append((p["label"], data[other]))
    if probes:
        false = [n for n, _why in asyncio.run(_judge_many(probes)) if n]
        print(
            f"проба «чужой домен»: ложных сопоставлений {len(false)} из {len(probes)} "
            "(верный ответ — 0)"
        )
    if agree / len(pairs) < 0.8:
        print("⚠️ согласие ниже 80% — покрытие объявляется оценкой сверху, как у judge_precision")


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
    # Модель одна на стенд (NFR-26): исполнитель API держит тот же замок — «занято» = код 75.
    try:
        with locks.hold(MODEL_LOCK):
            main()
    except locks.Busy as exc:
        print(f"занято: {exc}")
        sys.exit(75)
