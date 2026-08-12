"""Decide how much of each product to sell this turn.

The rest of the agent decides what to grow. This decides what that produce is
worth right now against what it should be worth later, and sells the units where
waiting does not pay.

Three facts drive every decision here:

1. Price falls as the shared pool fills, so each unit in one order sells for
   less than the one before it. Quantity is chosen unit by unit, not in bulk.
2. The town empties the pool every day, which lifts the price again. Waiting is
   therefore worth something, but only as much as the town will actually drain.
3. The opponent's tiles are public, including how much each one is holding, so
   their next sale is visible before it happens. Supply they are about to add
   pushes the later price down and makes waiting worth less.

Anything still held after the last executable step scores nothing, so the season
ends with everything sold regardless of price.
"""

from __future__ import annotations

from agent import market
from agent.gamedata import MARKET_PARAMS, PRICE_FLOOR, TERMINAL_STEP
from agent.params import Params

#: Products whose price collapses steeply once the pool fills. Losing the first
#: sale of these to the opponent costs far more than it does on a staple.
FRAGILE = ("MELON", "WOOL", "STRAWBERRY", "MILK")


def _later_price(plan, product: str, params: Params) -> float:
    """What one unit should fetch at the planning horizon.

    Counts the town emptying the pool between now and then, and any supply the
    opponent is visibly about to add.
    """
    inventory = plan.inventory.get(product, MARKET_PARAMS[product]["I0"])
    opponent_supply = plan._pressure.get(product, 0.0) if params.frontrun_enabled else 0.0

    return market.future_price(
        product, inventory, plan.step, plan.shops,
        params.hold_horizon_steps, opponent_supply=opponent_supply,
    )


def quantity_to_sell(plan, params: Params, product: str, held: int, shed_used: int) -> int:
    """How many of `held` to sell this turn.

    Walks the units one at a time. Each unit is sold while the price it would
    fetch now still beats what it is expected to fetch later, less the margin
    that makes waiting worthwhile. The walk stops at the first unit that fails,
    because every unit after it sells for even less.
    """
    if held <= 0:
        return 0

    # Nothing unsold scores anything once the season closes.
    if plan.step >= TERMINAL_STEP - 2:
        return held

    # Produce destroyed at nightfall is worth strictly less than produce sold at
    # a bad price, so shed pressure overrides the price judgement entirely.
    if shed_used >= params.shed_soft_cap:
        return held

    if plan.day < params.sell_start_day:
        return 0

    # Fertilizer is never consumed by the town, so its pool only ever fills and
    # its price only ever falls. Waiting cannot help.
    if product == "FERTILIZER":
        return held

    later = _later_price(plan, product, params)
    margin = params.premium_hold_margin if product in FRAGILE else params.hold_margin
    floor = max(PRICE_FLOOR, later - margin)

    inventory = plan.inventory.get(product, MARKET_PARAMS[product]["I0"])
    qty = 0
    while qty < held:
        unit_price = market.price(product, inventory + qty)
        if unit_price < floor:
            break
        qty += 1

    # A price already at the floor cannot fall further, and holding stock that
    # the opponent is about to bury only risks the shed cap.
    if qty == 0 and market.price(product, inventory) <= PRICE_FLOOR + 1:
        return held

    return qty


def _urgency(plan, params: Params, product: str, qty: int) -> float:
    """How much is lost by deferring this sale a turn.

    Orders resolve in list order and both players draw from one pool, so the
    earlier slot takes the better price on a contested product. Ranking by
    revenue alone treats a staple and a collapsing premium line as equally
    urgent; they are not.
    """
    inventory = plan.inventory.get(product, MARKET_PARAMS[product]["I0"])
    now = market.price(product, inventory)
    after = market.price(product, inventory + qty)

    # Cost of being second: the drop our own sale would walk through, plus the
    # drop the opponent's visible supply would add on top.
    pending = plan._pressure.get(product, 0.0) if params.frontrun_enabled else 0.0
    contested = market.price(product, inventory + qty + pending)

    return (now - contested) * qty + (now - after) * qty * 0.5


def plan_sales(plan, params: Params, shed_used: int) -> list[list]:
    """Sell orders for this turn, most urgent first."""
    orders: list[tuple[float, str, int]] = []

    for product, held in plan.shed.items():
        if product not in MARKET_PARAMS or held <= 0:
            continue

        sellable = held
        if product == "WHEAT":
            # Wheat the animals still need is not surplus. Selling it only means
            # buying it back later at a higher price.
            reserve = max(params.wheat_feed_reserve,
                          sum(plan.animals.values()) * params.wheat_days_cover)
            sellable = max(0, held - reserve)

        qty = quantity_to_sell(plan, params, product, sellable, shed_used)
        if qty > 0:
            orders.append((_urgency(plan, params, product, qty), product, qty))

    orders.sort(reverse=True)

    return [["SELL", product, qty] for _, product, qty in orders]
