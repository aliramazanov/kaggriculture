"""Decide what the farm should become, and what to trade to get there.

This module answers "what should the farm look like". `assign.py` answers "what
does each unit do this turn". They are separate because they change at different
speeds: the shape of the farm moves over days, the work moves every turn.

The town opens a new shop every three days and the draw allows repeats, so one
season can open three yarn stores and no bakery. Which shops open changes what
each product sells for, so build targets are recomputed from the shops actually
open rather than fixed in advance.
"""

from __future__ import annotations

from agent import market
from agent.gamedata import ANIMALS, CROPS, MARKET_PARAMS, SEASON_DAYS
from agent.params import Params
from agent.plan_schedule import KEYS, for_day

PREMIUM = ("STRAWBERRY", "MILK", "WOOL", "MELON")

STRUCTURE_OF = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}


class Plan:
    def __init__(self, obs: dict, params: Params):
        self.obs = obs
        self.p = params

        self.day = obs["day"]
        self.hour = obs["hour"]
        self.step = obs.get("step", self.day * 24 + self.hour)

        self.me = obs["farms"][obs["player"]]
        self.board = len(self.me["tiles"])
        self.money = self.me["money"]

        self.prices = obs["market"]["prices"]
        self.inventory = obs["market"]["inventory"]
        self.shops = obs["town"]["unlocked_shops"]

        self.shed = obs["private"]["shed"]
        self.seeds = obs["private"]["seeds"]

        self._census()
        self._opp_build = self._opponent_build() if params.counter_strength > 0 else {}
        self._targets()
        self._pressure = self._opponent_pressure() if params.frontrun_enabled else {}

    # ------------------------------------------------------------------ state

    def _census(self) -> None:
        self.counts = {k: 0 for k in ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON")}
        self.animals = {"GOOSE": 0, "COW": 0, "SHEEP": 0}
        self.free_structures = {"COOP": 0, "PASTURE": 0}
        self.empty: list[tuple[int, int]] = []
        self.unfed = 0

        for y, row in enumerate(self.me["tiles"]):
            for x, tile in enumerate(row):
                if tile is None:
                    self.empty.append((x, y))
                    continue

                if not isinstance(tile, dict):
                    continue

                kind = tile.get("kind")
                if kind == "PLANT":
                    self.counts[tile["crop"]] += 1
                    continue

                if kind not in ("COOP", "PASTURE"):
                    continue

                animal = tile.get("animal")
                if not animal:
                    self.free_structures[kind] += 1
                    continue

                self.animals[animal] += 1
                if not tile.get("fed_today"):
                    self.unfed += 1

    def _targets(self) -> None:
        """Tile targets, scaled by how much land we own and nudged by town demand."""
        p = self.p

        base = {
            "GOOSE": p.target_goose,
            "COW": p.target_cow,
            "SHEEP": p.target_sheep,
            "WHEAT": p.target_wheat,
            "CARROT": p.target_carrot,
            "MELON": p.target_melon,
            "STRAWBERRY": p.target_strawberry,
            "TOMATO": p.target_tomato,
        }

        if p.target_plan:
            today = for_day(p.target_plan, self.day)
            base = {key: today.get(name, base[key]) for name, key in KEYS.items()}

        unlocked = len(self.me["unlocked_quadrants"])
        scale = min(1.0, unlocked / 3.0)

        # Units per day the town will buy, given the shops open right now.
        drain = market.drain_per_day(self.shops)
        self.target = {}

        LONG_LEAD = ("STRAWBERRY", "MELON", "TOMATO")

        for key, value in base.items():
            product = ANIMALS[key]["product"] if key in ANIMALS else key
            # Slow crops are limited by the calendar, not by land. Planted late
            # they never finish, so they do not wait for a quadrant to be bought.
            local_scale = 1.0 if (p.long_lead_unscaled and key in LONG_LEAD) else scale
            # A product the town buys more of earns more tiles. The effect is
            # kept mild, because how fast a price collapses when the pool fills
            # matters more than how much the town wants.
            pull = drain.get(product, 1.0)
            k = self.p.shop_adapt_strength
            adj = 1.0 + k * (pull - 7.0) / 7.0
            lo, hi = self.p.shop_adapt_clamp_lo, self.p.shop_adapt_clamp_hi
            # Both farms sell into one pool, so a tile is worth less when the
            # opponent already grows a lot of that product. Their farm is public,
            # so this needs no guessing.
            if self.p.counter_strength > 0 and value > 0:
                theirs = self._opp_build.get(key, 0)
                excess = (theirs - value) / max(1.0, float(value))
                adj *= max(0.4, min(1.6, 1.0 - self.p.counter_strength * excess))
            self.target[key] = max(0, int(round(value * local_scale * max(lo, min(hi, adj)))))

        # Late in the season a new planting cannot mature, so stop starting them.
        days_left = SEASON_DAYS - self.day
        for crop, data in CROPS.items():
            if days_left < data["first_yield_day"] + 1:
                self.target[crop] = self.counts.get(crop, 0)

        for animal, data in ANIMALS.items():
            if days_left < data["first_yield_day"] + 2:
                self.target[animal] = self.animals.get(animal, 0)

    # ------------------------------------------------------- tile assignment

    def _deficit(self, key: str) -> int:
        if key in ANIMALS:
            return self.target.get(key, 0) - self.animals.get(key, 0)
        return self.target.get(key, 0) - self.counts.get(key, 0)

    def _shed_distance(self, x: int, y: int) -> int:
        h = self.board // 2
        return min(
            abs(x - sx) + abs(y - sy) for sx, sy in ((h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h))
        )

    def crop_for_empty_tile(self, x: int, y: int, seeds: dict) -> str | None:
        """Pick the crop with the largest unmet need that we hold a seed for.

        Animals are kept near the shed because feed has to be carried from it, so
        tiles close in are reserved for structures while structures are still
        wanted.
        """

        if self._shed_distance(x, y) <= 2 and self._structure_demand() > 0:
            return None

        best, best_score = None, 0.0

        for crop in CROPS:
            if seeds.get(crop, 0) <= 0:
                continue

            deficit = self._deficit(crop)

            if deficit <= 0:
                continue

            value = self.plant_value(crop, self.day)
            score = value if self.p.empty_tile_by_value else deficit * value

            if score > best_score:
                best, best_score = crop, score

        return best

    def _structure_demand(self) -> int:
        want_coop = max(0, self._deficit("GOOSE") - self.free_structures["COOP"])

        want_pasture = max(
            0, self._deficit("COW") + self._deficit("SHEEP") - self.free_structures["PASTURE"]
        )

        return want_coop + want_pasture

    def structure_for_empty_tile(self, x: int, y: int) -> str | None:
        # Feed is carried out from the shed, so distance to an animal is paid
        # again on every feeding, every day, for the rest of the season.
        if self._shed_distance(x, y) > self.p.structure_max_distance:
            return None

        if max(0, self._deficit("GOOSE") - self.free_structures["COOP"]) > 0:
            return "COOP"

        if (
            max(0, self._deficit("COW") + self._deficit("SHEEP") - self.free_structures["PASTURE"])
            > 0
        ):
            return "PASTURE"

        return None

    def animal_for_structure(self, kind: str) -> str | None:
        """Which animal this empty structure wants.

        The animal does not have to be in the shed. A unit picks the animal up
        before placing it, so at that moment it sits in the unit's inventory and
        the shed is empty. Checking the shed would hide this job at the one
        moment it can be done. The `needs` field already restricts the job to a
        unit carrying the animal.
        """
        best, best_deficit = None, 0

        for animal, structure in STRUCTURE_OF.items():
            if structure != kind:
                continue

            deficit = self._deficit(animal)

            if deficit > best_deficit:
                best, best_deficit = animal, deficit

        return best

    def tile_reuse_value(self, x: int, y: int) -> float:
        """What clearing this tile is worth: the best thing we could put on it.

        A fixed value cannot be right. Mid-season a weed sits on a tile worth a
        whole crop cycle; near the end it costs nothing, because anything planted
        then will not mature. Deriving the value from what could be planted makes
        it fall to zero on its own as the season closes.
        """

        best = 0.0

        for crop in CROPS:
            value = self.plant_value(crop, self.day)
            if value > best and self._deficit(crop) > 0:
                best = value
        # A structure is an option too, while we still want animals.

        if self._structure_demand() > 0 and self._shed_distance(x, y) <= 4:
            animal = "GOOSE" if self._deficit("GOOSE") > 0 else "COW"
            best = max(best, self.place_value(animal, self.day) * 0.5)

        return best

    def wants_fertilizer(self) -> bool:
        """True while any crop could still convert fertilizer into extra yield."""
        return self.day <= SEASON_DAYS - 3 and any(
            self.counts.get(crop, 0) for crop in ("STRAWBERRY", "TOMATO", "MELON", "WHEAT")
        )

    def fertilizer_pickup_value(self) -> float:
        best = max(
            (
                self._price(c)
                for c in ("STRAWBERRY", "MELON", "TOMATO", "WHEAT")
                if self.counts.get(c, 0)
            ),
            default=0.0,
        )

        return best * 0.9

    def has_free_structure(self, animal: str) -> bool:
        return self.free_structures.get(STRUCTURE_OF[animal], 0) > 0

    def operating_reserve(self) -> float:
        """Cash that may not be spent, because tomorrow depends on it.

        Hands are hired fresh each morning. Running out of money means no hands,
        which means nothing is watered or fed, which loses the crops and then the
        animals. Land, animals and seeds are bought out of what is left after
        this, never out of this.
        """
        living = sum(self.animals.values())
        feed = 2.0 * living * self._price("WHEAT")

        # Covers tomorrow's hiring plus two days of feed, so a bad day cannot
        # leave the farm unable to work in the morning.
        return self.p.min_cash_for_hire * self.p.reserve_hire_days + feed

    def can_spend(self, cost: float, spent_so_far: float = 0.0) -> bool:
        return self.money - spent_so_far - cost >= self.operating_reserve()

    def should_dig_structure(self, kind: str) -> bool:
        if kind == "COOP":
            return self._deficit("GOOSE") < 0
        return self._deficit("COW") + self._deficit("SHEEP") < 0

    # ------------------------------------------------------------- valuation
    def _price(self, product: str) -> float:
        return float(self.prices.get(product, MARKET_PARAMS[product]["base"]))

    def plant_value(self, crop: str, day: int) -> float:
        """Expected coins from one planting, net of the seed."""
        data = CROPS[crop]
        days_left = SEASON_DAYS - day

        if days_left < data["first_yield_day"]:
            return 0.0

        if data["ongoing"]:
            yields = min(
                data["max_yield"],
                max(0, (days_left - data["first_yield_day"]) // max(1, data["interval"]) + 1),
            )
            units = yields
        else:
            units = min(
                data["max_yield"],
                1 + max(0, data["max_yield_day"] - (data["max_yield_day"] + 1) // 2 + 1),
            )

        return max(0.0, units * self._price(crop) - data["seed"])

    def structure_value(self, structure: str, day: int) -> float:
        if structure == "COOP":
            animal = "GOOSE"
        elif self._deficit("COW") > 0:
            animal = "COW"
        else:
            animal = "SHEEP"

        return self.place_value(animal, day) * 0.8

    def place_value(self, animal: str, day: int) -> float:
        data = ANIMALS[animal]
        producing = max(0.0, SEASON_DAYS - day - data["first_yield_day"])
        per_day = {"GOOSE": 2.0, "COW": 1.5, "SHEEP": 4 / 3}[animal]
        gross = producing * per_day * self._price(data["product"])
        fert = producing * self._price("FERTILIZER") * 0.6
        feed = producing * self._price("WHEAT")

        return max(0.0, gross + fert - feed)

    def feed_value(self, animal: str, day: int, urgent: bool = False) -> float:
        """Feeding protects the animal's entire remaining income stream.

        An animal at `consecutive_unfed >= 1` escapes tonight if it is not fed,
        and escaped animals are unrecoverable, so that case is worth the whole
        remaining stream plus the price of replacing the animal.
        """
        data = ANIMALS[animal]

        # No end-of-season cutoff here, unlike caring. An animal that escapes
        # takes whatever it has not yet had harvested with it, so feeding still
        # protects stock on the last days even once no production tick remains.
        stream = self.place_value(animal, day)

        if urgent:
            return (stream + data["cost"]) * self.p.feed_priority

        return (stream * 0.45 + self._price(data["product"])) * self.p.feed_priority

    def care_value(self, animal: str, day: int) -> float:
        """Caring adds one unit, delivered at the animal's next production."""
        data = ANIMALS[animal]

        if SEASON_DAYS - day <= data["interval"]:
            return 0.0

        return self._price(data["product"]) * 0.95 * self.p.care_priority

    def unfed_animal_count(self) -> int:
        return self.unfed

    def _opponent_build(self) -> dict:
        """Count what the opponent is growing. Their tiles are public."""
        counts = {
            k: 0
            for k in ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "GOOSE", "COW", "SHEEP")
        }

        for seat, farm in enumerate(self.obs["farms"]):
            if seat == self.obs["player"]:
                continue

            for row in farm["tiles"]:
                for tile in row:
                    if not isinstance(tile, dict):
                        continue

                    if tile.get("kind") == "PLANT":
                        counts[tile["crop"]] += 1
                    elif tile.get("animal"):
                        counts[tile["animal"]] += 1

        return counts

    # ------------------------------------------------------- opponent model
    def _opponent_pressure(self) -> dict[str, float]:
        """What the opponent is about to put into the pool, from their public tiles.

        Capacity counts, not just stock. Reading `yield_units` alone measures
        almost nothing, because a tile is only holding produce in the window
        between ripening and their harvest, so the figure is zero nearly every
        turn. A tile that grows the product at all is supply heading our way; a
        tile already holding some is simply nearer.
        """
        pressure: dict[str, float] = {}

        for seat, farm in enumerate(self.obs["farms"]):
            if seat == self.obs["player"]:
                continue

            for row in farm["tiles"]:
                for tile in row:
                    if not isinstance(tile, dict):
                        continue

                    ready = max(0.0, float(tile.get("yield_units", 0) or 0))

                    if tile.get("kind") == "PLANT" and tile.get("crop"):
                        crop = tile["crop"]
                        pressure[crop] = pressure.get(crop, 0.0) + 1.0 + 2.0 * ready
                    elif tile.get("animal"):
                        data = ANIMALS[tile["animal"]]
                        product = data["product"]
                        cadence = 1.0 / max(1, data["interval"])
                        pressure[product] = pressure.get(product, 0.0) + cadence + 2.0 * ready

        return pressure

    # ---------------------------------------------------------------- market
    def wheat_needed(self) -> int:
        living = sum(self.animals.values())
        return living * max(0, SEASON_DAYS - self.day)

    def hire_target(self) -> int:
        if self.p.target_plan:
            return for_day(self.p.target_plan, self.day).get("hands", self.p.hands_steady)

        ramped = self.p.hands_open + self.p.hands_ramp * self.day

        return min(self.p.hands_steady, ramped)

    def reserve_price(self, product: str) -> float:
        """Below this, hold the unit rather than sell it.

        Set from what the town is expected to drain before our horizon, which is
        what lifts the price. Fertilizer is exempt: nothing consumes it, so its
        price only ever falls and holding is strictly worse.
        """
        if product == "FERTILIZER":
            return 0.0

        inv = self.inventory.get(product, MARKET_PARAMS[product]["I0"])
        later = market.future_price(product, inv, self.step, self.shops, self.p.hold_horizon_steps)
        # The two groups differ in how fast the price falls as the pool fills.
        # Wheat and egg fall logarithmically at 0.20 and carrot as a square root
        # at 0.70, so the price holds up and a unit sold now costs the next one
        # little. Strawberry and milk fall linearly at 1.60, wool and melon
        # quadratically at 3.20 and 3.60, where each unit sold cuts the next.
        margin = self.p.premium_hold_margin if product in PREMIUM else self.p.hold_margin

        # Dividing here scales the reserve down as the margin rises, so a larger
        # margin sells sooner rather than later. Subtracting reads the right way
        # round but measured worse, so the behaviour is kept and the name is the
        # thing that is wrong.
        reserve = later / max(0.01, margin)

        if self.p.frontrun_enabled:
            pending = self._pressure.get(product, 0.0)
            per_day = max(1.0, market.drain_per_day(self.shops).get(product, 1.0))

            if pending >= per_day * self.p.frontrun_threshold:
                # They are about to supply this product. Clear ours first.
                reserve *= self.p.frontrun_discount

        return reserve
