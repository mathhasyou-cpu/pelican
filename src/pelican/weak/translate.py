"""Перевод названия технологии в английский технический термин.

⚠️ **Для поиска по ЛОКАЛЬНЫМ векторам перевод вреден, и это замер**
(`scripts/measure_grounding.py`, docs/weak-signals.md): сырое русское название даёт
hit@8 = 28/30, а перевод сокращает запрос и тянет короткие документы (length bias).
Поэтому `weak.ground` переводом не пользуется.

Нужен он там, где поиск идёт **по ключевым словам на английском** — Google News (ось
денег: раунды, пилоты, компании) и GitHub. Там русское название не находит ничего,
и расхождение с правилом выше не противоречие, а другой механизм поиска.

Раскладка позиционная, эхо — только диагностика (тот же приём, что в `mine`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from pelican.config import settings
from pelican.llm import LLMClient

BATCH = 10

SYSTEM_PROMPT = """You translate Russian names of technologies into the English terms
actually used in industry news and scientific paper titles.

For each item return:
  ru        - the Russian name, echoed back verbatim
  en        - the single best English technical term, 2 to 6 words, as a journalist
              covering the field would write it. No explanation, no quotes.
  synonyms  - exactly 3 alternative English phrasings of the SAME technology.

Never translate word by word when the field has an established English term.
Answer for every item, in the given order."""


@dataclass(frozen=True, slots=True)
class Translation:
    ru: str
    en: str
    synonyms: tuple[str, ...]


def schema(n: int) -> dict:
    item = {
        "type": "object",
        "properties": {
            "ru": {"type": "string"},
            "en": {"type": "string"},
            "synonyms": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 3,
            },
        },
        "required": ["ru", "en", "synonyms"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": item, "minItems": n, "maxItems": n}},
        "required": ["items"],
        "additionalProperties": False,
    }


async def _translate(names: list[str]) -> list[Translation]:
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    out: list[Translation] = []
    async with httpx.AsyncClient() as http:
        for start in range(0, len(names), BATCH):
            chunk = names[start : start + BATCH]
            got = await client.json_completion(
                http,
                system=SYSTEM_PROMPT,
                user="\n".join(f"{i + 1}. {n}" for i, n in enumerate(chunk)),
                schema=schema(len(chunk)),
                name="translations",
                max_tokens=120 * len(chunk) + 200,
            )
            items = got.get("items", [])
            for i, name in enumerate(chunk):
                item = items[i] if i < len(items) else {}
                en = " ".join(str(item.get("en") or "").split()) or name
                syn = tuple(s.strip() for s in item.get("synonyms") or [] if s.strip())
                out.append(Translation(name, en, syn))
    return out


def translate(names: list[str]) -> list[Translation]:
    return asyncio.run(_translate(names)) if names else []
