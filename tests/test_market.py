"""Differential tests: agent/market.py must match the pinned engine exactly.

The submitted agent is a single self-contained file, so it carries its own copy
of the price parameters rather than importing them. If the two ever differ, every
price the agent calculates is wrong and nothing else reports it.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from agent import gamedata, market
from agent.gamedata import (
    MARKET_PARAMS as OURS,
)
from tests.engine_ref import (
    MARKET_PARAMS,
    PRICE_FLOOR,
    PRODUCTS,
    SHOPS,
    TOWN_CENTER_PRODUCTS,
    engine,
    market_price,
)


def test_price_table_matches_engine():
    assert set(OURS) == set(MARKET_PARAMS)
    for item, engine_p in MARKET_PARAMS.items():
        assert OURS[item] == engine_p, item


def test_shop_table_matches_engine():
    assert {k: tuple(v) for k, v in SHOPS.items()} == gamedata.SHOPS
    assert tuple(TOWN_CENTER_PRODUCTS) == gamedata.TOWN_CENTER_PRODUCTS
    assert tuple(PRODUCTS) == gamedata.PRODUCTS
    assert PRICE_FLOOR == gamedata.PRICE_FLOOR


@pytest.mark.parametrize("item", list(MARKET_PARAMS))
def test_price_matches_engine_across_the_curve(item):
    i0 = MARKET_PARAMS[item]["I0"]
    points = [0, 1, i0 - 1, i0, i0 + 1, 2 * i0, -5000, 10 * i0]
    points += [i0 + d for d in (-2000, -500, -100, -1, 1, 100, 500, 2000, 9000)]

    for inv in points:
        assert market.price(item, inv) == market_price(item, inv), (item, inv)


def test_price_matches_engine_on_random_inventories():
    rng = random.Random(20260809)

    for _ in range(10_000):
        item = rng.choice(list(MARKET_PARAMS))
        inv = rng.randint(-20_000, 40_000)
        assert market.price(item, inv) == market_price(item, inv), (item, inv)


def test_sell_revenue_matches_engine_commit_loop():
    """Sell one unit at a time, as the game does, and compare the total."""
    rng = random.Random(7)

    for _ in range(400):
        item = rng.choice(list(MARKET_PARAMS))
        start = rng.randint(9_000, 11_000)
        qty = rng.randint(1, 60)

        inv = start
        expected = 0

        for _ in range(qty):
            unit = market_price(item, inv)
            expected += unit
            if unit > PRICE_FLOOR:
                inv += 1

        assert market.sell_revenue(item, start, qty) == expected, (item, start, qty)


def test_buy_cost_matches_engine_quoting():
    """Buys quote at post-buy inventory, so a buy/sell round trip nets zero."""
    rng = random.Random(11)
    for _ in range(400):
        item = rng.choice(["WHEAT", "FERTILIZER"])
        start = rng.randint(9_000, 11_000)
        qty = rng.randint(1, 40)

        inv = start
        expected = 0

        for _ in range(qty):
            expected += market_price(item, inv - 1)
            inv -= 1

        assert market.buy_cost(item, start, qty) == expected


def test_buy_then_sell_round_trip_nets_zero():
    """Buying a unit then selling it back must break even, not pay out."""
    for item in ("WHEAT", "FERTILIZER"):
        inv = MARKET_PARAMS[item]["I0"]
        cost = market.buy_cost(item, inv, 1)
        revenue = market.sell_revenue(item, inv - 1, 1)
        assert cost == revenue, item


def test_town_drain_matches_engine_step_for_step():
    """Compare the forecast against the town actually consuming, step by step."""
    from kaggle_environments import make

    rng = random.Random(3)
    shop_names = sorted(SHOPS)
    for _ in range(200):
        shops = [rng.choice(shop_names) for _ in range(rng.randint(0, 8))]
        step = rng.randint(0, 719)

        env = make("kaggriculture", configuration={"seed": 1})
        # kaggle_environments ships no type information; State is a dict subclass
        # with attribute access, which no checker can infer.
        state: Any = env.reset()
        obs = state[0].observation
        obs.town["unlocked_shops"] = list(shops)
        before = dict(obs.market["inventory"])
        engine._town_consume(env, state, step)
        actual = {p: before[p] - obs.market["inventory"][p] for p in PRODUCTS}

        predicted = market.town_drain_per_step(step, shops)

        for p in PRODUCTS:
            assert predicted.get(p, 0) == actual[p], (p, step, shops)
