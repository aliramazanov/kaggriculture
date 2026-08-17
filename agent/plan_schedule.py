"""A day-by-day target curve the planner aims at.

The planner derives its build targets from one constant per crop and animal,
held for the whole season. A schedule states them per day instead, which is a
thing a search can shape directly.

Targets, not purchases. Scheduling the purchases themselves measured about nine
points worse than the reactive rules, because a fixed order fires whether or not
the farm can afford it or has anywhere to put it. A target keeps every reactive
check and only moves the goal, so a flat schedule reproduces current behaviour
exactly.
"""

from __future__ import annotations

FIELDS = (
    "goose",
    "cow",
    "sheep",
    "wheat",
    "carrot",
    "melon",
    "strawberry",
    "tomato",
    "hands",
)

KEYS = {
    "goose": "GOOSE",
    "cow": "COW",
    "sheep": "SHEEP",
    "wheat": "WHEAT",
    "carrot": "CARROT",
    "melon": "MELON",
    "strawberry": "STRAWBERRY",
    "tomato": "TOMATO",
}

WIDTH = len(FIELDS)


def row(day_plan: tuple[int, ...]) -> dict[str, int]:
    return dict(zip(FIELDS, day_plan, strict=False))


def for_day(plan: tuple[tuple[int, ...], ...], day: int) -> dict[str, int]:
    if not plan:
        return {}

    return row(plan[day]) if day < len(plan) else row(plan[-1])


def seed_from(params, days: int) -> tuple[tuple[int, ...], ...]:
    """The curve the current parameters already describe, so a search starts at parity.

    Crop and animal targets are constant all season, but the crew is not: it
    ramps from `hands_open` to `hands_steady`, and flattening it to the steady
    figure hires a full crew on day zero and bankrupts the opening.
    """
    crops = tuple(int(getattr(params, f"target_{name}", 0)) for name in FIELDS if name != "hands")

    return tuple(
        crops + (min(int(params.hands_steady), int(params.hands_open + params.hands_ramp * d)),)
        for d in range(days)
    )


def flatten(plan: tuple[tuple[int, ...], ...]) -> list[int]:
    return [v for day in plan for v in day]


def unflatten(flat: list[int] | tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(int(max(0, v)) for v in flat[i : i + WIDTH]) for i in range(0, len(flat), WIDTH)
    )
