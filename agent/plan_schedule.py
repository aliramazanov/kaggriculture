"""A day-by-day build plan the agent follows instead of deriving one.

The parametric rules in `main` decide hiring, animals, land, seed and feed from
smooth functions of the day and the bank. A schedule states them outright, one
row per day, which is a thing a search can optimise directly.

Selling is deliberately not scheduled. It has to read the pool, and a fixed
sell plan cannot.
"""

from __future__ import annotations

FIELDS = (
    "hires",
    "cow",
    "sheep",
    "goose",
    "land",
    "seed_wheat",
    "seed_carrot",
    "seed_melon",
    "seed_strawberry",
    "seed_tomato",
    "buy_wheat",
)

ANIMAL_FIELDS = (("cow", "COW"), ("sheep", "SHEEP"), ("goose", "GOOSE"))
SEED_FIELDS = (
    ("seed_wheat", "WHEAT"),
    ("seed_carrot", "CARROT"),
    ("seed_melon", "MELON"),
    ("seed_strawberry", "STRAWBERRY"),
    ("seed_tomato", "TOMATO"),
)

WIDTH = len(FIELDS)


def row(day_plan: tuple[int, ...]) -> dict[str, int]:
    return dict(zip(FIELDS, day_plan, strict=False))


def quota_for(plan: tuple[tuple[int, ...], ...], day: int) -> dict[str, int]:
    if not plan:
        return {}
    return row(plan[day]) if day < len(plan) else row(plan[-1])


def flatten(plan: tuple[tuple[int, ...], ...]) -> list[int]:
    return [v for day in plan for v in day]


def unflatten(flat: list[int] | tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(int(max(0, v)) for v in flat[i : i + WIDTH]) for i in range(0, len(flat), WIDTH)
    )
