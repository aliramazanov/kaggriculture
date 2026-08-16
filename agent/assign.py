"""Match units to jobs, paying for the walk.

Every unit gets one action per turn, and a turn spent walking earns nothing. A
unit that walks three tiles to water one plant spends four turns on one watering,
so distance is part of what a job is worth.

Matching is greedy rather than optimal. The turn must be produced within one
second, and the match is rebuilt every turn, so a better pairing is not worth
the time to compute.
"""

from __future__ import annotations

from collections.abc import Callable

from agent.tasks import Task


def manhattan(ax: int, ay: int, bx: int, by: int) -> int:
    """A unit moves one tile per turn along an axis, so distance is the step count."""
    return abs(ax - bx) + abs(ay - by)


def step_towards(fx: int, fy: int, tx: int, ty: int) -> list[str]:
    """Longer axis first, which keeps a path straight instead of stepping diagonally."""
    dx, dy = tx - fx, ty - fy

    if abs(dx) >= abs(dy):
        if dx > 0:
            return ["EAST"]
        if dx < 0:
            return ["WEST"]

    if dy > 0:
        return ["SOUTH"]

    if dy < 0:
        return ["NORTH"]

    return ["PASS"]


def _carrying(inv: dict) -> int:
    return sum(inv.values())


def _can_do(task: Task, inv: dict) -> bool:
    if task.avoid is not None:
        item, limit = task.avoid
        if inv.get(item, 0) >= limit:
            return False

    if task.needs is None:
        return True

    if task.needs == "__carrying__":
        return _carrying(inv) > 0

    return inv.get(task.needs, 0) > 0


def anchors(n_units: int, board: int) -> list[tuple[int, int]]:
    """Spread home positions evenly over the board, one per unit.

    Units all chasing the single best job converge on one corner and then walk
    back across the farm together. A home area keeps each unit's work local.
    """
    if n_units <= 0:
        return []

    cols = max(1, int(n_units**0.5 + 0.5))
    rows = max(1, (n_units + cols - 1) // cols)
    out = []

    for i in range(n_units):
        cx = (i % cols + 0.5) / cols
        cy = (i // cols + 0.5) / rows
        out.append((int(cx * (board - 1)), int(cy * (board - 1))))

    return out


def assign(
    units: list[tuple[int, int]],
    inventories: list[dict],
    tasks: list[Task],
    board: int,
    seed_budget: dict[str, int] | None = None,
    value_for: Callable[..., float] | None = None,
    sticky: dict[int, tuple[int, int]] | None = None,
    sticky_bonus: float = 1.0,
    zone_pull: float = 0.0,
    idle_reposition: bool = True,
) -> tuple[list[list], dict[int, tuple[int, int]]]:
    """Give every unit an action, and report the tile each one is heading for.

    Jobs are scored `value / (1 + distance)`, which is value per turn spent: a
    job worth V one tile away costs one turn to reach and one to do, so it earns
    V/2 a turn. That makes near and far jobs directly comparable instead of
    making distant work unreachable.

    The returned targets come back as `sticky` on the next turn. A unit part way
    through a walk then keeps its job unless another is clearly better, because
    rebuilding the match each turn otherwise makes units swap targets and walk
    back and forth without arriving.

    `seed_budget` limits PLANT actions per crop. The game rejects every PLANT for a
    crop when they together ask for more seeds than the farm holds, so asking for
    too many wastes the turn of every unit that tried to plant.
    """
    seed_budget = dict(seed_budget or {})
    actions: list[list | None] = [None] * len(units)
    claimed: set[int] = set()

    sticky = sticky or {}
    homes = anchors(len(units), board) if zone_pull > 0 else []

    # Score every unit against every job once, then hand out the best pairs.
    scored: list[tuple[float, int, int, int]] = []
    for ui, (ux, uy) in enumerate(units):
        inv = inventories[ui] if ui < len(inventories) else {}
        held = sticky.get(ui)
        home = homes[ui] if homes else None

        for ti, task in enumerate(tasks):
            if not _can_do(task, inv):
                continue

            dist = manhattan(ux, uy, task.x, task.y)
            base = task.value if value_for is None else value_for(task, inv)
            if base <= 0:
                continue

            score = base / (1.0 + dist)
            if held is not None and (task.x, task.y) == held:
                score *= sticky_bonus
            if home is not None:
                away = manhattan(home[0], home[1], task.x, task.y)
                score /= 1.0 + zone_pull * away

            scored.append((score, dist, ui, ti))

    # Best score first; ties go to the closer job. The leading minus sorts
    # descending, since Python sorts ascending by default.
    scored.sort(key=lambda s: (-s[0], s[1]))

    # Walk the list once, skipping any pair whose unit or job is already taken.
    targets: dict[int, tuple[int, int]] = {}
    for _score, dist, ui, ti in scored:
        if actions[ui] is not None or ti in claimed:
            continue

        task = tasks[ti]
        if task.op[0] == "PLANT":
            crop = task.op[1]
            if seed_budget.get(crop, 0) <= 0:
                continue
            seed_budget[crop] -= 1

        claimed.add(ti)
        targets[ui] = (task.x, task.y)

        ux, uy = units[ui]
        if dist == 0:
            actions[ui] = list(task.op)
        else:
            actions[ui] = step_towards(ux, uy, task.x, task.y)

    # A unit that matched nothing spends the turn either way, so it walks toward
    # the nearest job it could take rather than standing still. Jobs are claimed
    # one unit each and the farm generates several times more of them than there
    # are hands, so an unmatched unit is nearly always short of somewhere useful
    # to be, and arriving early spends a turn that would otherwise be spent later.
    for ui, action in enumerate(actions):
        if action is not None or not idle_reposition:
            continue

        ux, uy = units[ui]
        inv = inventories[ui] if ui < len(inventories) else {}

        reachable = [
            task for ti, task in enumerate(tasks) if ti not in claimed and _can_do(task, inv)
        ]

        if not reachable:
            continue

        target = min(reachable, key=lambda t: manhattan(ux, uy, t.x, t.y))
        actions[ui] = step_towards(ux, uy, target.x, target.y)

    # Units that matched nothing still have to return a legal action.
    return [a if a is not None else ["PASS"] for a in actions], targets
