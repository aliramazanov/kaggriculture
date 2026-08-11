"""Price the market: what a sale earns now, and what it would earn later.

Both players sell into one shared pool, and a product's price falls as that pool
fills. This module does three things:

1. Prices a sale before it is made, by reproducing the game's price curve.
2. Predicts what the town will buy for the rest of the season. Town demand
   empties the pool and lifts prices, which sets how much can be sold per day.
3. Decides how much to sell now, by comparing what the next unit earns today
   against what it should earn later.

"""

from __future__ import annotations

import math

from agent.gamedata import (
    MARKET_PARAMS,
    MAX_SHOP_INSTANCES,
    PRICE_FLOOR,
    PRODUCTS,
    SHOPS,
    TOWN_CENTER_PRODUCTS,
)

_AMP_CACHE: dict[tuple[str, bool], float] = {}


def _shape(func: str, x: float) -> float:
    x = max(0.0, x)

    if func == "linear":
        return x

    if func == "sq":
        return x * x

    if func == "sqrt":
        return math.sqrt(x)

    if func == "log":
        return math.log(1.0 + x)

    if func == "log10":
        return math.log10(1.0 + x)

    return x


def _amp(item: str, below: bool) -> float:
    key = (item, below)
    cached = _AMP_CACHE.get(key)

    if cached is None:
        p = MARKET_PARAMS[item]
        side = "below" if below else "above"

        cached = p[f"{side}_target"] * p["base"] / _shape(p[f"{side}_func"], p["T"])

        _AMP_CACHE[key] = cached

    return cached


def price(item: str, inventory: float) -> int:
    """Price of one unit at this pool level. Never below the floor of $1."""
    p = MARKET_PARAMS[item]

    base, i0 = p["base"], p["I0"]

    if inventory < i0:
        value = base + _amp(item, True) * _shape(p["below_func"], i0 - inventory)
    else:
        value = base - _amp(item, False) * _shape(p["above_func"], inventory - i0)

    return max(PRICE_FLOOR, int(round(value)))


def sell_revenue(item: str, inventory: float, qty: int) -> int:
    """Coins earned selling `qty` units one at a time into the pool.

    Units sell one at a time and each lowers the price for the next, so this is
    a loop rather than a multiplication. A unit sold at the $1 floor does not
    join the pool.
    """
    total = 0
    inv = inventory

    for _ in range(max(0, qty)):
        unit = price(item, inv)
        total += unit
        if unit > PRICE_FLOOR:
            inv += 1

    return total


def buy_cost(item: str, inventory: float, qty: int) -> int:
    """Cost of buying `qty` units, each priced after it leaves the pool.

    Each unit is quoted after it leaves the pool, so buying a unit and selling
    it straight back breaks even.
    """
    total = 0

    inv = inventory

    for _ in range(max(0, qty)):
        total += price(item, inv - 1)
        inv -= 1

    return total


def town_drain_per_step(
    step: int, shops, shop_interval: int = 4, center_interval: int = 24
) -> dict[str, int]:
    """Units the town buys on one step. Shops buy every few steps, the town
    centre once a day, and a shop selling a single product buys double."""
    drain: dict[str, int] = {}

    if step % shop_interval == 0:
        for shop in shops:
            products = SHOPS.get(shop)
            if not products:
                continue

            mult = 2 if len(products) == 1 else 1
            for item in products:
                drain[item] = drain.get(item, 0) + mult

    if step % center_interval == 0:
        for item in TOWN_CENTER_PRODUCTS:
            drain[item] = drain.get(item, 0) + 1

    return drain


def drain_per_day(
    shops, shop_interval: int = 4, center_interval: int = 24, turns_per_day: int = 24
) -> dict[str, float]:
    """Steady-state units per day the town pulls, given the shops unlocked now."""
    ticks = turns_per_day / shop_interval
    out = {p: turns_per_day / center_interval for p in TOWN_CENTER_PRODUCTS}
    out.setdefault("FERTILIZER", 0.0)

    for shop in shops:
        products = SHOPS.get(shop)
        if not products:
            continue
        mult = 2 if len(products) == 1 else 1

        for item in products:
            out[item] = out.get(item, 0.0) + ticks * mult

    return out


def projected_drain(
    step: int,
    shops,
    episode_steps: int = 720,
    shop_unlock_interval: int = 3,
    turns_per_day: int = 24,
    shop_interval: int = 4,
    center_interval: int = 24,
) -> dict[str, float]:
    """Town demand expected between `step` and the end of the season.

    Shops already open are counted exactly. The rest are still unknown, and
    because the draw allows repeats any shop can appear more than once, so they
    are counted at their average rate instead of guessed.
    """
    remaining = max(0, episode_steps - step)
    days_left = remaining / turns_per_day
    known = drain_per_day(shops, shop_interval, center_interval, turns_per_day)
    out = {p: known.get(p, 0.0) * days_left for p in PRODUCTS}

    # Shops not yet open, counted at the average across all shop types.
    unlocked = len(list(shops))
    slots = max(0, MAX_SHOP_INSTANCES - unlocked)

    if slots:
        ticks = turns_per_day / shop_interval
        expected: dict[str, float] = {}

        for products in SHOPS.values():
            mult = 2 if len(products) == 1 else 1
            for item in products:
                expected[item] = expected.get(item, 0.0) + ticks * mult / len(SHOPS)

        day_now = step // turns_per_day

        for i in range(slots):
            unlock_day = (unlocked + i + 1) * shop_unlock_interval
            active_days = max(0.0, (episode_steps / turns_per_day) - max(unlock_day, day_now))
            for item, rate in expected.items():
                out[item] = out.get(item, 0.0) + rate * active_days

    return out


def future_price(
    item: str,
    inventory: float,
    step: int,
    shops,
    horizon_steps: int,
    episode_steps: int = 720,
    opponent_supply: float = 0.0,
) -> int:
    """Price this product should fetch `horizon_steps` from now.

    Town demand empties the pool and lifts the price; the opponent selling into
    it does the opposite. Both are counted. This is the reserve price used when
    deciding whether keeping a unit beats selling it today.
    """
    end = min(episode_steps, step + horizon_steps)
    drain_now = projected_drain(step, shops, episode_steps)[item]
    drain_end = projected_drain(end, shops, episode_steps)[item]
    consumed = max(0.0, drain_now - drain_end)

    return price(item, inventory - consumed + opponent_supply)
