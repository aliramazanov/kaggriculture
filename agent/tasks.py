"""List every job available on the farm this turn, each priced in coins.

Each unit gets 24 actions a day and there is always more work than time, so the
turn is decided by ranking jobs. Ranking needs a common unit, and coins are the
only one the game supplies: a priority tier cannot say whether watering a melon
beats feeding a goose, but a price can.

Every price here is marginal. It answers "what does doing this add, compared to
not doing it", not "what is this tile worth".
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.gamedata import ANIMALS, CROPS, MARKET_PARAMS, SEASON_DAYS
from agent.params import Params


@dataclass
class Task:
    x: int
    y: int
    op: list
    value: float
    kind: str
    # An item the unit must already carry to do the job: FEED needs wheat, PLACE
    # needs the animal itself.
    needs: str | None = None

    # (item, limit): hide this job from a unit already carrying `limit` of the
    # item, so units deliver what they hold instead of collecting more of it.
    avoid: tuple[str, int] | None = None


def shed_tiles(board: int) -> list[tuple[int, int]]:
    h = board // 2
    return [(h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h)]


def _unit_price(prices: dict, product: str) -> float:
    return float(prices.get(product, MARKET_PARAMS[product]["base"]))


def remaining_crop_value(tile: dict, day: int, prices: dict) -> float:
    """Everything this plant will still produce if it stays alive.

    This is what is lost when a plant turns to weed, so it is the right price for
    a watering that saves it. What the plant is holding right now is the wrong
    price: a thirsty young melon holds one unit but is on its way to six.
    """
    crop = tile["crop"]
    data = CROPS[crop]
    price = _unit_price(prices, crop)
    held = max(0, tile.get("yield_units", 0))
    age = day - tile["planted_day"]

    if data["ongoing"]:
        interval = max(1, data["interval"])

        if age < data["first_yield_day"]:
            done = 0
        else:
            done = (age - data["first_yield_day"]) // interval + 1

        future = max(0, data["max_yield"] - done)
    else:
        window_start = (data["max_yield_day"] + 1) // 2
        last_useful = data["max_yield_day"]
        future = max(0, last_useful - max(age, window_start - 1))
        future = min(future, max(0, data["max_yield"] - held))

    return price * (held + future)


def water_value(tile: dict, day: int, prices: dict, params: Params) -> float:
    """What one WATER action is worth on this tile right now.

    Two cases, orders of magnitude apart:
      - one dry day from turning to weed, so the watering saves the whole plant
      - otherwise, worth only the extra unit that watering today adds, if any
    """
    crop = tile["crop"]
    data = CROPS[crop]
    price = _unit_price(prices, crop)

    # Two missed days turns a plant into a weed, so one missed day already means
    # it dies tonight without water.
    if tile.get("consecutive_unwatered", 0) >= 1:
        return remaining_crop_value(tile, day, prices)

    if not data["ongoing"]:
        age = day - tile["planted_day"]
        window_start = (data["max_yield_day"] + 1) // 2
        if window_start <= age <= data["max_yield_day"] and tile["yield_units"] < data["max_yield"]:
            # Each watered day inside this window permanently adds a unit. At
            # face value that always loses to an animal or a dying plant, so the
            # bonus is weighted up: watering only when a plant is about to die
            # keeps it alive but harvests it half grown.
            fertilized = 2 if tile.get("fertilized_until_day", -1) >= day else 1
            return price * fertilized * params.window_water_bonus
    elif tile.get("fertilized_until_day", -1) >= day:
        return price
    # Nothing urgent and no bonus today. Watering a healthy plant is still cheap
    # insurance, because idle hands cost nothing while a dead plant costs
    # everything it would have grown.

    return price * params.base_water_share


def fertilize_value(tile: dict, day: int, prices: dict) -> float:
    """Extra yield one FERTILIZE buys, in coins.

    Fertilizer is active for `day`, `day+1`, `day+2` and adds a second unit on
    each of those days the plant also produces. It is worth nothing unless the
    tile would otherwise finish below `max_yield`: a one-time crop starts at one
    unit and a plant watered every day already reaches the cap on its own for
    melon, tomato and strawberry, so only wheat and carrot have any slack.
    """
    crop = tile["crop"]
    data = CROPS[crop]

    if tile.get("fertilized_until_day", -1) >= day:
        return 0.0

    age = day - tile["planted_day"]
    cap = data["max_yield"]

    # An ongoing crop keeps its tile through harvest, and we take its stock as
    # soon as it holds any, so the cap almost never binds and every production
    # day inside the window is worth a whole extra unit.
    if data["ongoing"]:
        interval = max(1, data["interval"])
        events = [data["first_yield_day"] + k * interval for k in range(cap)]
        extra = sum(1 for a in events if age <= a <= age + 2)
        return extra * _unit_price(prices, crop)

    # A one-time crop is cleared by its only harvest, so its cap binds inside a
    # single life. Melon reaches it unaided and gains nothing.
    window_start = (data["max_yield_day"] + 1) // 2
    plain = boosted = tile.get("yield_units", 0)

    for a in range(max(age, window_start), data["max_yield_day"] + 1):
        plain = min(cap, plain + 1)
        boosted = min(cap, boosted + (2 if a <= age + 2 else 1))

    return max(0.0, boosted - plain) * _unit_price(prices, crop)


def harvest_value(tile: dict, prices: dict) -> float:
    if tile.get("kind") == "PLANT":
        return _unit_price(prices, tile["crop"]) * max(0, tile.get("yield_units", 0))
    animal = tile.get("animal")

    if animal:
        return _unit_price(prices, ANIMALS[animal]["product"]) * max(0, tile.get("yield_units", 0))
    return 0.0


def can_harvest(tile: dict, day: int, days_left: int = 99) -> bool:
    """Whether this tile should be harvested now rather than left to grow.

    Harvesting destroys a one-time crop, so taking it as soon as it is legal
    throws away everything it would still have added. Wheat can be harvested at
    age 2 holding one or two units, but reaches four by age 4. So these wait for
    the last growing day, the yield cap, or the end of the season.

    Ongoing crops and animals are not consumed by harvesting, so they are taken
    as soon as they hold anything.
    """
    if tile.get("yield_units", 0) <= 0:
        return False

    if tile.get("kind") != "PLANT":
        return "animal" in tile

    data = CROPS[tile["crop"]]
    age = day - tile["planted_day"]

    if age < data["first_yield_day"]:
        return False

    if data["ongoing"]:
        return True

    if tile["yield_units"] >= data["max_yield"]:
        return True

    if age >= data["max_yield_day"]:
        return True
    # Out of time to let it finish growing: take what is there.

    return days_left <= (data["max_yield_day"] - age) + 1


def generate(obs: dict, plan, params: Params) -> list[Task]:
    """All jobs available on our farm this turn, priced."""
    me = obs["farms"][obs["player"]]
    tiles = me["tiles"]
    board = len(tiles)
    day = obs["day"]
    hour = obs["hour"]
    prices = obs["market"]["prices"]
    private = obs["private"]
    seeds = private["seeds"]
    tasks: list[Task] = []

    for y in range(board):
        for x in range(board):
            tile = tiles[y][x]
            if tile == "LOCKED":
                continue

            if tile is None:
                # A seed is created already one day dry, so one planted too late
                # to be watered turns to weed the same night: the seed, the turn
                # and the tile all go, and something then has to dig it out.
                crop = (
                    plan.crop_for_empty_tile(x, y, seeds)
                    if hour <= params.plant_last_hour
                    else None
                )
                if crop:
                    worth = plan.plant_value(crop, day) * params.idle_plant_bonus
                    tasks.append(Task(x, y, ["PLANT", crop], worth, "PLANT"))

                structure = plan.structure_for_empty_tile(x, y)
                if structure:
                    worth = plan.structure_value(structure, day)
                    tasks.append(Task(x, y, [f"BUILD_{structure}"], worth, "BUILD"))

                continue

            if not isinstance(tile, dict):
                continue

            kind = tile.get("kind")
            if kind == "WEED":
                # Clearing is worth whatever the freed tile could grow instead,
                # which decays to nothing on its own late in the season.
                worth = plan.tile_reuse_value(x, y) * params.weed_dig_share
                tasks.append(Task(x, y, ["DIG"], worth, "DIG"))
                continue

            if kind == "PLANT":
                if can_harvest(tile, day, SEASON_DAYS - day):
                    worth = harvest_value(tile, prices)
                    tasks.append(Task(x, y, ["HARVEST"], worth, "HARVEST"))

                if not tile.get("watered_today"):
                    worth = water_value(tile, day, prices, params)
                    tasks.append(Task(x, y, ["WATER"], worth, "WATER"))

                if params.fertilize_enabled:
                    net = fertilize_value(tile, day, prices) - (
                        _unit_price(prices, "FERTILIZER") * params.fertilize_cost_ratio
                    )

                    if net >= params.fertilize_min_gain:
                        tasks.append(
                            Task(x, y, ["FERTILIZE"], net, "FERTILIZE", needs="FERTILIZER")
                        )

                continue

            # Coop or pasture, possibly holding an animal.
            animal = tile.get("animal")
            if animal is None:
                want = plan.animal_for_structure(kind)

                if want:
                    worth = plan.place_value(want, day)
                    tasks.append(Task(x, y, ["PLACE", want], worth, "PLACE", needs=want))
                elif plan.should_dig_structure(kind):
                    tasks.append(Task(x, y, ["DIG"], params.weed_dig_value * 0.5, "DIG"))

                continue

            data = ANIMALS[animal]
            if not tile.get("fed_today"):
                # Two missed days and the animal escapes for good, so one already
                # hungry is priced at everything it would still have produced,
                # plus the cost of replacing it.
                urgent = tile.get("consecutive_unfed", 0) >= 1
                worth = plan.feed_value(animal, day, urgent=urgent)

                # Only the escape check and the care bonus read `fed_today`;
                # base production does not. An animal fed yesterday can be left
                # today and still yields, so a full day's feed is worth less than
                # it looks when there is field work waiting.
                if not urgent:
                    worth *= params.feed_fresh_scale

                tasks.append(Task(x, y, ["FEED"], worth, "FEED", needs="WHEAT"))

            # Care only pays on a day the animal is also fed, so caring an
            # unfed animal spends the turn for nothing.
            if params.care_enabled and tile.get("fed_today") and not tile.get("cared_today"):
                tasks.append(Task(x, y, ["CARE"], plan.care_value(animal, day), "CARE"))

            if can_harvest(tile, day, SEASON_DAYS - day):
                # An animal that fills up stops producing, so collection grows
                # more urgent as the tile approaches its limit.
                held = tile.get("yield_units", 0)
                urgency = 1.0 + 0.8 * (held / max(1, data["max_held"]))
                worth = harvest_value(tile, prices) * urgency
                tasks.append(Task(x, y, ["HARVEST"], worth, "HARVEST"))

            if tile.get("fertilizer_available"):
                worth = _unit_price(prices, "FERTILIZER") * params.fert_priority
                tasks.append(Task(x, y, ["COLLECT_FERTILIZER"], worth, "FERT"))

    tasks.extend(_shed_tasks(obs, plan, params, board))
    return tasks


def _shed_tasks(obs: dict, plan, params: Params, board: int) -> list[Task]:
    """Jobs at the four tiles next to the shed: dropping stock and drawing feed.

    Selling reads the shed and nothing else, so produce in a unit's hands cannot
    be sold. Dropping is the step that turns field work into money.
    """
    private = obs["private"]
    shed = private["shed"]
    shed_count = sum(shed.values())
    out: list[Task] = []
    unfed = plan.unfed_animal_count()
    wheat_in_shed = shed.get("WHEAT", 0)

    for x, y in shed_tiles(board):
        out.append(Task(x, y, ["DROP"], params.drop_value, "DROP", needs="__carrying__"))

        if unfed and wheat_in_shed and shed_count < 100:
            batch = min(params.feed_pickup_batch, wheat_in_shed, max(1, unfed))

            # Priced by what the wheat is worth as feed, not by the value of
            # feeding, and hidden from units already carrying wheat.
            worth = plan.feed_value("GOOSE", obs["day"]) * 0.35

            out.append(
                Task(x, y, ["PICKUP", "WHEAT", batch], worth, "PICKUP_FEED", avoid=("WHEAT", 1))
            )

        if params.fertilize_enabled and shed.get("FERTILIZER", 0) > 0 and plan.wants_fertilizer():
            worth = plan.fertilizer_pickup_value()
            out.append(
                Task(
                    x, y, ["PICKUP", "FERTILIZER", 4], worth, "PICKUP_FERT", avoid=("FERTILIZER", 1)
                )
            )

        for animal in ("GOOSE", "COW", "SHEEP"):
            # Only fetch an animal when a home is free for it. Otherwise a unit
            # carries it around all day, drops it back at nightfall, and repeats
            # the same trip tomorrow.
            if shed.get(animal, 0) > 0 and plan.has_free_structure(animal):
                worth = plan.place_value(animal, obs["day"]) * 0.95
                out.append(Task(x, y, ["PICKUP", animal, 1], worth, "PICKUP_ANIMAL"))

    return out
