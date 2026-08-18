"""Behavioural pins for defects that shipped.

The market tests prove the price table matches the engine and nothing else.
Every real defect so far lived in behaviour they cannot see: fertilizer valued
on crops it cannot help, feeding switched off while animals still held stock,
an opponent-pressure signal that was always zero. Each test here is one of
those defects, pinned so it cannot return quietly.
"""

from __future__ import annotations

from agent.gamedata import MARKET_PARAMS, SEASON_DAYS
from agent.params import DEFAULT
from agent.planner import Plan
from agent.tasks import can_harvest, fertilize_value

PRICES = {k: float(v["base"]) for k, v in MARKET_PARAMS.items()}


def _tiles(board: int = 10) -> list[list[dict | None]]:
    return [[None] * board for _ in range(board)]


def _obs(day=0, hour=0, mine=None, theirs=None):
    return {
        "day": day,
        "hour": hour,
        "step": day * 24 + hour,
        "player": 0,
        "farms": [
            {
                "tiles": mine or _tiles(),
                "money": 3000,
                "farmer": [2, 2],
                "hands": [],
                "unlocked_quadrants": ["NW"],
                "hires_today": 0,
            },
            {
                "tiles": theirs or _tiles(),
                "money": 3000,
                "farmer": [7, 7],
                "hands": [],
                "unlocked_quadrants": ["NW"],
                "hires_today": 0,
            },
        ],
        "market": {"prices": dict(PRICES), "inventory": dict.fromkeys(PRICES, 10_000)},
        "town": {"unlocked_shops": []},
        "private": {"shed": {}, "seeds": {}, "inventories": [{}]},
    }


def _plant(crop, planted_day, yield_units=1):
    return {
        "kind": "PLANT",
        "crop": crop,
        "planted_day": planted_day,
        "yield_units": yield_units,
        "watered_today": False,
        "consecutive_unwatered": 0,
        "fertilized_until_day": -1,
    }


def test_fertilizer_gains_nothing_on_melon_watered_on_schedule():
    # A melon behind on watering can still catch up at two units a day, so the
    # zero-gain claim holds only for a tile watered on schedule. That tile is
    # the common case, and it was drawing 567 fertilize offers a game.
    for day in range(0, 13):
        on_track = min(6, 1 + max(0, min(day, 12) - 6))
        tile = _plant("MELON", planted_day=0, yield_units=on_track)

        assert fertilize_value(tile, day, PRICES) == 0.0


def test_fertilizer_pays_on_wheat_and_carrot_at_window():
    wheat = _plant("WHEAT", planted_day=0)
    carrot = _plant("CARROT", planted_day=0)
    assert fertilize_value(wheat, 2, PRICES) == 2 * PRICES["WHEAT"]
    assert fertilize_value(carrot, 2, PRICES) == 1 * PRICES["CARROT"]


def test_wheat_is_not_harvested_half_grown():
    tile = _plant("WHEAT", planted_day=0, yield_units=2)
    assert not can_harvest(tile, day=2)
    assert can_harvest(tile, day=4)


def test_feeding_still_pays_at_season_end():
    # Reverted regression: a cutoff here let the whole herd escape on days
    # 27 to 29, taking its unharvested stock with it.
    tiles = _tiles()
    tiles[1][1] = {"kind": "PASTURE", "animal": "COW", "fed_today": False, "cared_today": False}
    plan = Plan(_obs(day=SEASON_DAYS - 2, mine=tiles), DEFAULT)
    assert plan.feed_value("COW", SEASON_DAYS - 2, urgent=True) > 0


def test_opponent_pressure_sees_growing_tiles():
    # The signal read yield_units alone, which is zero outside the brief window
    # between ripening and their harvest, so it measured nothing all game.
    theirs = _tiles()
    theirs[3][3] = _plant("MELON", planted_day=0, yield_units=0)
    plan = Plan(_obs(day=1, theirs=theirs), DEFAULT)
    assert plan._pressure.get("MELON", 0.0) >= 1.0


def test_flat_target_plan_matches_constants():
    # The schedule must reproduce the constant targets exactly at its seed,
    # or every search starts from a handicap instead of from parity.
    from agent import plan_schedule as schedule

    flat = DEFAULT.evolve(target_plan=())
    seeded = flat.evolve(target_plan=schedule.seed_from(flat, SEASON_DAYS))
    for day in (0, 5, 12, 25):
        obs = _obs(day=day)
        assert Plan(obs, seeded).target == Plan(obs, flat).target
        assert Plan(obs, seeded).hire_target() == Plan(obs, flat).hire_target()


def test_full_game_produces_a_real_bank():
    # The one check that catches a silently broken agent: a crashed or all-PASS
    # agent still finishes DONE with zero errors, at the starting bank.
    from kaggle_environments import make

    from agent.main import agent

    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 424242})
    env.run([agent, "starter"])

    final = env.steps[-1]
    assert [s["status"] for s in final] == ["DONE", "DONE"]
    assert final[0]["reward"] > 20_000
