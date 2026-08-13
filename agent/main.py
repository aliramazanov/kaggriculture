"""Entry point. One turn in, one set of actions out.

    agent(obs) -> {"farmer": [...], "hands": [[...], ...], "market": [[...], ...]}

Each turn runs the same three steps:

    planner  what the farm should become, and what to trade
    tasks    what every job on the board is worth right now
    assign   which unit does which job

Every turn is wrapped so that a failure returns a legal PASS instead of
raising. An uncaught error ends the season as a loss.
"""

from __future__ import annotations

from agent import assign as assign_mod
from agent import tasks as tasks_mod
from agent.gamedata import ANIMALS, CROPS, LAND_PRICES, MARKET_PARAMS, TERMINAL_STEP
from agent.params import DEFAULT, Params
from agent.planner import Plan

# Memory between turns. This function is called again and again in one process,
# so a module-level dict is the only thing that survives from turn to turn.
#
# Kept per player. Both seats can be driven from one process, and then the calls
# arrive alternating between them; a single shared record would be overwritten
# by the other seat on every turn.
_STATE: dict[int, dict] = {}


def _episode_state(obs: dict) -> dict:
    """The calling player's memory, cleared when a new season or day starts."""
    player = obs.get("player", 0)
    step = obs.get("step", 0)
    state = _STATE.get(player)

    # A step that did not advance means this is a new season, not a new turn.
    if state is None or step <= state["step"]:
        state = {"step": step, "targets": {}, "day": obs.get("day", 0)}
        _STATE[player] = state

    # Hands are hired fresh every morning and numbered again from scratch, so
    # yesterday's assignments point at units that no longer exist.
    if obs.get("day") != state["day"]:
        state["targets"] = {}
        state["day"] = obs.get("day", 0)

    state["step"] = step
    return state


def _safe_action(obs: dict) -> dict:
    try:
        hands = obs["farms"][obs["player"]].get("hands", []) or []
    except Exception:
        hands = []

    return {"farmer": ["PASS"], "hands": [["PASS"] for _ in hands], "market": []}


def _market_orders(obs: dict, plan: Plan, params: Params) -> list[list]:
    orders: list[list] = []
    money = plan.money
    day, hour, step = plan.day, plan.hour, plan.step
    shed = plan.shed

    def afford(cost: float) -> bool:
        nonlocal money

        if money >= cost:
            money -= cost
            return True

        return False

    # Final turns: sell everything.
    #
    # Stock left over at the end scores nothing. Unit actions are applied before
    # market orders in the same turn, so goods dropped at the shed this turn can
    # still be sold this turn.
    if step >= TERMINAL_STEP - 2:
        for product in sorted(shed, key=lambda p: -plan._price(p) if p in MARKET_PARAMS else 0):
            if product in MARKET_PARAMS and shed.get(product, 0) > 0:
                orders.append(["SELL", product, shed[product]])
        return orders[: params.max_orders]

    # Labour, taken before anything else and out of the protected reserve.
    # Hands are what turn land into money, and a full day of them costs less than
    # a single animal.
    if hour <= 2:
        want = plan.hire_target() - plan.me["hires_today"]
        n = 0
        # Only two of the ten order slots are held back for everything else,
        # since leaving more unused caps hiring for no benefit.
        while n < want and len(orders) < params.max_orders - 2:
            cost = _fib(plan.me["hires_today"] + n)
            if money - cost < params.min_cash_for_hire:
                break
            if not afford(cost):
                break
            orders.append(["HIRE"])
            n += 1

    # Land.
    owned_extra = len(plan.me["unlocked_quadrants"]) - 1
    land_schedule = (params.land_day_ne, params.land_day_sw, params.land_day_se)
    if owned_extra < len(land_schedule):
        price = LAND_PRICES[owned_extra]
        affordable = day >= land_schedule[owned_extra] and money - price >= plan.operating_reserve()
        if affordable and afford(price):
            orders.append(["BUY_LAND"])

    # Animals, bought only out of surplus and only when a home is free.
    if day >= params.animal_start_day:
        bought = 0
        # Most productive animal first, so that when only one is affordable it
        # is the one worth having. A fixed order buys the cheapest instead.
        by_value = sorted(("GOOSE", "COW", "SHEEP"), key=lambda a: -plan.place_value(a, day))

        for animal in by_value:
            if bought >= params.max_animal_buys_per_turn or len(orders) >= params.max_orders - 2:
                break

            # Animals already carried by a unit count as owned. Picking one up
            # removes it from the shed, so counting the shed alone reads zero
            # and buys a replacement for an animal already in hand.

            in_transit = shed.get(animal, 0) + sum(
                inv.get(animal, 0) for inv in obs["private"].get("inventories", [])
            )

            deficit = plan._deficit(animal) - in_transit

            if deficit <= 0:
                continue

            # An animal with nowhere to live produces nothing and still has to
            # be fed.
            if params.require_home_before_buy:
                structure = ANIMALS[animal]["structure"]
                if plan.free_structures.get(structure, 0) <= in_transit:
                    continue
            elif in_transit and not plan.has_free_structure(animal):
                continue

            cost = ANIMALS[animal]["cost"]
            reserved = plan.operating_reserve() + params.animal_cash_buffer

            if money - cost >= reserved and afford(cost):
                orders.append(["BUY_ANIMAL", animal, 1])
                bought += 1

    # Seeds, which respect the reserve exactly as land and animals do. Spending
    # the bank on seed leaves nobody to water what was planted.
    for crop in ("WHEAT", "CARROT", "MELON", "STRAWBERRY", "TOMATO"):
        if len(orders) >= params.max_orders - 1:
            break

        deficit = plan._deficit(crop) - plan.seeds.get(crop, 0)

        if deficit > 0 and plan.plant_value(crop, day) > 0:
            want = min(deficit, params.seed_batch)
            cost = CROPS[crop]["seed"] * want
            while want > 0 and money - cost < plan.operating_reserve():
                want -= 1
                cost = CROPS[crop]["seed"] * want
            if want > 0 and afford(cost):
                orders.append(["BUY_SEED", crop, want])

    # Feed. Wheat carried by units counts as held: a unit picking wheat up
    # empties the shed, and counting the shed alone then buys it all again.
    living = sum(plan.animals.values())

    if living:
        carried = sum(inv.get("WHEAT", 0) for inv in obs["private"].get("inventories", []))
        on_hand = shed.get("WHEAT", 0) + carried
        need = living * params.wheat_days_cover

        # Feed is normally bought once a day. The exception is animals already
        # hungry with no wheat anywhere, where waiting for the usual hour costs
        # a day of production.
        starving = params.emergency_feed and plan.unfed > 0 and on_hand <= 0

        if (
            on_hand < need
            and (hour == params.wheat_buy_hour or starving)
            and len(orders) < params.max_orders
        ):
            want = int(need - on_hand)
            # Buy less rather than break the reserve. Feed with nobody left to
            # carry it is worse than no feed at all.
            unit = plan._price("WHEAT")
            while want > 0 and money - unit * want < plan.operating_reserve():
                want -= 1
            if want > 0 and afford(unit * want):
                orders.append(["BUY_PRODUCT", "WHEAT", want])

    # --- sales ---------------------------------------------------------------
    # What overflows tonight is the shed plus everything units are holding,
    # because every inventory empties into the shed at the end of the day and
    # whatever passes the cap is destroyed.
    shed_used = sum(shed.values()) + sum(
        sum(inv.values()) for inv in obs["private"].get("inventories", [])
    )

    if day >= params.sell_start_day:
        sellable = []

        for product, held in shed.items():
            if held <= 0 or product not in MARKET_PARAMS:
                continue
            if product == "WHEAT":
                reserve = max(params.wheat_feed_reserve, living * params.wheat_days_cover)
                held = max(0, held - reserve)
                if held <= 0:
                    continue
            price_now = plan._price(product)
            glut = MARKET_PARAMS[product]["above_target"] if params.sell_glut_weight else 1.0
            urgency = glut ** params.sell_glut_weight
            reserve = plan.reserve_price(product)
            forced = shed_used >= params.shed_soft_cap
            if forced or price_now >= reserve:
                sellable.append((price_now * held * urgency, product, held))

        sellable.sort(reverse=True)

        for _, product, held in sellable:
            if len(orders) >= params.max_orders:
                break
            orders.append(["SELL", product, held])

    return orders[: params.max_orders]



def _fib(n: int) -> int:
    a, b = 1, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def _seed_budget(plan: Plan) -> dict[str, int]:
    return {crop: plan.seeds.get(crop, 0) for crop in CROPS}


def act(obs: dict, params: Params = DEFAULT) -> dict:
    plan = Plan(obs, params)
    me = plan.me
    board = plan.board

    units = [tuple(me["farmer"])] + [tuple(h) for h in me.get("hands", [])]
    inventories = obs["private"].get("inventories", [])
    inventories = [dict(inv) for inv in inventories]

    while len(inventories) < len(units):
        inventories.append({})

    jobs = tasks_mod.generate(obs, plan, params)

    def value_for(task, inv):
        # Dropping is worth exactly what it puts within reach of a SELL order,
        # so it has to be priced per unit from what that unit is actually
        # carrying. A flat value made empty-handed trips look worthwhile.
        if task.kind == "DROP":
            # Everything in hand reaches the shed for free at nightfall, so a
            # mid-day trip only pays for a full load. Without a floor, units near
            # the shed shuttle back and forth instead of working the field.
            if sum(inv.values()) < params.drop_min_items:
                return 0.0

            carried = sum(plan._price(item) * n for item, n in inv.items() if item in MARKET_PARAMS)

            return carried * params.drop_urgency

        return task.value

    state = _episode_state(obs)

    unit_actions, targets = assign_mod.assign(
        units,
        inventories,
        jobs,
        board,
        _seed_budget(plan),
        value_for=value_for,
        sticky=state["targets"],
        sticky_bonus=params.sticky_bonus,
        zone_pull=params.zone_pull,
    )

    state["targets"] = targets

    # On the final executable turns, get everything carried into the shed so the
    # market orders below can convert it to coins before the season ends.
    if plan.step >= TERMINAL_STEP - 2:
        shed_set = set(tasks_mod.shed_tiles(board))

        for i, (ux, uy) in enumerate(units):
            if sum(inventories[i].values()) <= 0:
                continue
            if (ux, uy) in shed_set:
                unit_actions[i] = ["DROP"]
            else:
                target = min(shed_set, key=lambda t: abs(t[0] - ux) + abs(t[1] - uy))
                unit_actions[i] = assign_mod.step_towards(ux, uy, target[0], target[1])

    return {
        "farmer": unit_actions[0] if unit_actions else ["PASS"],
        "hands": unit_actions[1:],
        "market": _market_orders(obs, plan, params),
    }


def agent(obs) -> dict:
    try:
        if not isinstance(obs, dict):
            obs = dict(obs)
        return act(obs)

    except Exception:
        return _safe_action(obs if isinstance(obs, dict) else {})
