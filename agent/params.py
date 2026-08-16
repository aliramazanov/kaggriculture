"""Values the agent can be tuned with. None of them are fixed by the game.

One frozen dataclass rather than literals spread through the code, so the
agent's behaviour is a single value that can be copied and varied. "Frozen" is
Python's term for immutable: assigning to a field raises an error rather than
changing an object other code may be holding.

Values the game fixes are in `gamedata.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Params:
    # Build targets: how many of each animal and crop the farm aims to hold.
    #
    # The limit here is labour, not money or market depth. Every animal needs
    # roughly three actions a day (feed, care, collect), so a herd grows past
    # what the hands can service long before it grows past what the market can
    # absorb. Crops are cheaper to hold because an unwatered tile only stops
    # growing, while an unfed animal stops producing entirely.
    #
    # A crop whose target is zero is never eligible for an empty tile, whatever
    # the market is paying, because eligibility is a shortfall test and only the
    # ranking that follows it reads the price. Carrot is held above zero so that
    # ranking can reach it: it first yields on day 2, and past 450 units below
    # equilibrium its price climbs quadratically rather than linearly.
    target_goose: int = 0
    target_cow: int = 6
    target_sheep: int = 7
    target_wheat: int = 46
    target_carrot: int = 8
    target_melon: int = 18
    target_strawberry: int = 20
    target_tomato: int = 2

    # Expansion: the day each extra quadrant becomes eligible for purchase.
    #
    # These are earliest days, not fixed dates. A purchase also has to clear the
    # operating reserve, so in practice the farm buys land as soon as it can
    # afford it. The third quadrant costs $4,000 and adds 25 tiles that the
    # available hands cannot keep watered, so it is left off with day 99.
    land_day_ne: int = 3
    land_day_sw: int = 10
    land_day_se: int = 99

    # The town opens shops at random through the season, and which shops open
    # changes what each product sells for. shop_adapt_strength is how far build
    # targets bend toward that draw; the clamps stop one lucky draw from
    # rewriting the whole plan. counter_strength shifts the build away from
    # whatever the opponent is already growing. Zero disables either.
    shop_adapt_strength: float = 0.316
    shop_adapt_clamp_lo: float = 0.7
    shop_adapt_clamp_hi: float = 1.3
    counter_strength: float = 0.0

    # Labour. Each extra hand hired on the same day costs more than the last,
    # following a Fibonacci sequence, so the tenth hand of a day is cheap and the
    # fifteenth is not. The early days are cash-constrained rather than
    # labour-constrained, so hiring ramps up instead of starting at full crew.
    # Two numbers rather than a table because a search cannot reach a tuple.
    hands_open: int = 2
    hands_ramp: int = 1
    hands_steady: int = 12
    min_cash_for_hire: int = 201
    reserve_hire_days: float = 0.5

    # Animals. Purchases come out of surplus cash only: buying ahead of income
    # leaves nothing to hire with, and the farm then cannot feed what it bought.
    # Buys are ordered by expected value, so the most productive animal is taken
    # first when only one is affordable.
    structure_max_distance: int = 3
    animal_start_day: int = 0
    animal_cash_buffer: int = 80
    max_animal_buys_per_turn: int = 1
    require_home_before_buy: bool = False

    # Feeding. Animals eat wheat, so the farm both grows it and buys it. The
    # reserve keeps enough back that a good selling price cannot starve the herd.
    #
    # Feed is carried in small loads rather than large ones. A unit holding wheat
    # is the only kind that can take a FEED job, and feeding is priced above most
    # field work, so a big load commits that unit to feeding for as many turns as
    # it can carry. Whatever it has left goes back to the shed at the end of the
    # day regardless, so carrying more than the next animals need buys nothing.
    feed_pickup_batch: int = 3
    wheat_feed_reserve: int = 12
    wheat_days_cover: int = 3
    emergency_feed: bool = False
    wheat_buy_hour: int = 20

    # Upkeep priority. An animal that is both fed and cared for yields one extra
    # unit, so care competes with field work for the same hands. These weights
    # decide who wins. Feeding and caring only pay together: feeding alone keeps
    # the animal alive but leaves the bonus unclaimed.
    care_enabled: bool = True
    feed_priority: float = 5.0
    care_priority: float = 1.5

    # Fertilizer is offered once per animal per day as a flag rather than a
    # counter, so an animal left uncollected today does not hold two tomorrow.
    # Nothing in the town consumes fertilizer either, so its pool never refills
    # and both farms are drawing down the same fixed stock: a unit collected
    # first is one the other side cannot sell at all. Priced at the bare market
    # price it loses to feeding and caring, which carry multipliers of their own.
    fert_priority: float = 2.6

    # Market. The shed holds 100 items and destroys the overflow at end of day,
    # so selling starts before that cap rather than at it.
    seed_batch: int = 5
    max_orders: int = 10
    shed_soft_cap: int = 78

    # Selling. A unit is kept back only when the price it should fetch later,
    # once the town has drained the pool, beats the price on offer now by more
    # than the margin. Premium goods get their own margin because their prices
    # move much further over a season than a staple's.
    hold_margin: float = 4.0
    premium_hold_margin: float = 4.0
    hold_horizon_steps: int = 96

    # Both farms sell into one pool, and the first unit in gets the better price
    # because every unit sold lowers the next one. Waiting hands the early sales
    # to the other side.
    sell_start_day: int = 2

    # Order in which our own sells are queued. Orders resolve in list order, so
    # the earlier slot takes the better price when both players sell the same
    # product. Ranking by total value alone treats every product as equally
    # urgent; weighting by how steeply a product's price falls when the pool
    # fills puts the fragile lines first, where losing the slot costs most.
    # 0 disables the weighting and ranks purely by value.
    sell_glut_weight: float = 0.995

    # How a freed tile picks its next crop. Ranking by shortfall times value lets
    # a cheap, fast-cycling crop win every tile: wheat is harvested and destroyed
    # every few days, so its shortfall is permanently large, while a slow crop
    # worth four times as much is short by one tile and loses. True ranks by the
    # value of the tile itself and uses the shortfall only to decide eligibility.
    empty_tile_by_value: bool = True

    # The opponent's farm is public, including how much each tile is holding, so
    # a large sale is visible before it happens. The first seller into a pool gets
    # the better price, so a coming flood triggers selling ahead of it.
    frontrun_enabled: bool = True
    frontrun_threshold: float = 0.5
    frontrun_discount: float = 0.45

    # Fertilizer is worth more spent than sold. Spreading it doubles a scheduled
    # yield, which on an expensive crop beats what the fertilizer itself fetches.
    # It is applied selectively: fertilizing every tile costs more than it earns.
    fertilize_enabled: bool = True
    fertilize_min_gain: float = 148.199

    # Slow crops have to be planted early or they never finish. Strawberry first
    # yields at age 10 and keeps yielding to age 16, so a late planting forfeits
    # most of the plant. Scaling its target by land owned would delay it behind
    # wheat, which can be planted at any point in the season.
    long_lead_unscaled: bool = False

    # Scheduling weights. Jobs are priced in coins, then discounted by how far a
    # unit must walk to reach them, so a distant job has to be worth more than a
    # near one to win. The rest of these bias that contest.
    weed_dig_share: float = 0.475
    weed_dig_value: float = 40.0
    drop_value: float = 260.0
    drop_min_items: int = 6

    # Carrying harvested goods to the shed is worth less than it looks. At the
    # end of each day every unit's inventory moves to the shed anyway, so a
    # mid-day trip buys only the chance to sell one day sooner. It must not be
    # allowed to crowd out field work.
    drop_urgency: float = 0.431

    # A unit that matches no job walks toward the nearest one it could take
    # instead of standing still. Measured worse: it cuts idle turns from 6% to
    # 3% but converts them into travel rather than work, and units drift toward
    # jobs another unit then takes.
    idle_reposition: bool = False

    base_water_share: float = 0.3

    # A one-time crop gains a unit only on a day it is watered inside its yield
    # window, and the windows are short: wheat days 2 to 4, carrot 2 to 3. A day
    # missed there is a unit that tile will never hold, because harvesting a
    # one-time crop clears the tile and there is no later chance to make it up.
    # Watering in the window is therefore worth far more than watering outside it.
    window_water_bonus: float = 7.0
    idle_plant_bonus: float = 1.15
    zone_pull: float = 0.0

    # Once a unit starts walking toward a job it keeps that job unless another is
    # clearly better. Without this the plan is rebuilt every turn and units
    # oscillate between two targets, walking all season and arriving nowhere.
    sticky_bonus: float = 1.021

    def evolve(self, **kw) -> Params:
        """Return a copy with the named fields changed.

        The dataclass is frozen, so this is how a variant is made. `replace`
        copies every other field across unchanged.
        """
        return replace(self, **kw)


DEFAULT = Params()

