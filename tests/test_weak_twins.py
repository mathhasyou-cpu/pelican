import asyncio

import numpy as np

from pelican.weak import twins


def _unit(*rows: list[float]) -> np.ndarray:
    v = np.asarray(rows, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_options_keep_close_heads_nearest_first_and_capped(monkeypatch) -> None:
    monkeypatch.setattr(twins, "K_SELECT", 2)
    v = _unit([1, 0], [1, 0.1], [1, 0.3], [0, 1], [1, 0.02])
    assert twins.options(v, [0, 1, 2, 3], 4) == [0, 1]
    assert twins.options(v, [3], 4) == []
    assert twins.options(v, [], 4) == []


def test_options_put_a_shared_core_first_even_below_the_filter() -> None:
    v = _unit([1, 0], [0, 1], [1, 0.1])
    keys = ["x", "KYC compliance", "kyc compliance "]
    assert twins.options(v, [0, 1], 2, keys) == [1, 0]
    assert twins.options(v, [0, 1], 2, ["x", "", ""]) == [0]


def test_parse_rejects_anything_but_an_option_number() -> None:
    assert twins.parse({"match": 2}, 3) == 2
    assert twins.parse({"match": 0}, 3) == 0
    assert twins.parse({"match": 4}, 3) == 0
    assert twins.parse({"match": "1"}, 3) == 0
    assert twins.parse({}, 3) == 0


def test_assign_asks_only_when_a_head_is_close_and_joins_the_pick() -> None:
    v = _unit([1, 0], [1, 0.1], [0, 1])
    asked: list[tuple[str, list[str]]] = []

    async def pick(record: str, opts: list[str]) -> int:
        asked.append((record, opts))
        return 1

    assert asyncio.run(twins.assign(["a", "b", "c"], v, pick)) == [0, 0, 2]
    assert asked == [("b", ["a"])]


def test_assign_offers_only_heads_so_no_chain_through_a_member() -> None:
    # b ~ a и c ~ b по косинусу, но c от a далеко: b уходит в a, и c выбирать не из кого.
    v = _unit([1, 0], [1, 0.9], [0.2, 1])
    assert float(v[0] @ v[2]) < twins.BLOCK_COS <= float(v[1] @ v[2])

    async def pick(_record: str, _opts: list[str]) -> int:
        return 1

    assert asyncio.run(twins.assign(["a", "b", "c"], v, pick)) == [0, 0, 2]


def test_assign_offers_heads_one_at_a_time_nearest_first() -> None:
    # Две подходящие головы в одном вызове модель отвергает обе — поэтому по одной.
    v = _unit([1, 0], [1, 0.1], [1, 0.12])
    asked: list[list[str]] = []

    async def pick(record: str, opts: list[str]) -> int:
        asked.append(opts)
        return 1 if record == "c" and opts == ["a"] else 0

    assert asyncio.run(twins.assign(["a", "b", "c"], v, pick)) == [0, 1, 0]
    assert asked == [["a"], ["b"], ["a"]]
