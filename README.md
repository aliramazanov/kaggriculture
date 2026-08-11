# Kaggriculture agent

Competition agent for [Kaggriculture](https://www.kaggle.com/competitions/kaggriculture).

Two players each run a 10x10 farm for a 720-turn season (30 days of 24 turns)
and sell into one shared market. The larger final bank wins. Prices move with
what both players have sold, so a sale is priced against the pool it lands in.

## Structure

```
agent/          the agent, bundled verbatim into the submission
  gamedata.py   constants the game defines: prices, crops, animals, shops
  params.py     constants we chose: one frozen dataclass of tunables
  market.py     price curve, town-demand forecast, sell sizing
  tasks.py      prices every job available on the board this turn
  assign.py     matches units to jobs under travel cost
  planner.py    build targets and trading policy for the season
  main.py       entry point: agent(obs) -> actions

tests/          checks gamedata.py and market.py against the installed game
scripts/        build_submission.py: bundles agent/ and verifies the artifact
```

`gamedata.py` must match the game exactly. `params.py` is free to change. They
are separate files so it is clear which numbers may be edited.

## How it works

Each turn runs three stages.

1. `planner` sets what the farm should become: how many of each animal and
   crop, how much labour to hire, what to buy and sell. The town opens a shop
   every three days and the draw allows repeats, so which shops are open
   changes what each product is worth. Targets are recomputed every turn.
2. `tasks` prices every job on the board in coins: water, feed, care, harvest,
   plant, build, collect, dig, carry. Values are marginal, meaning what the
   action adds compared with not taking it.
3. `assign` gives each unit the best job it can reach, scoring
   `value / (1 + distance)`, which is value per turn spent including the walk.
   Assignments persist between turns, so a unit part way through a walk
   finishes it.

`market` reproduces the game's price curve and forecasts town demand for the
rest of the season. Each product has its own curve shape on each side of
equilibrium, so the cost of oversupply differs per product. Selling continues
while the next unit clears a reserve price derived from what it should fetch
later.

`main.py` wraps every turn so a failure returns a legal `PASS`. An uncaught
error ends the season as a loss.

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The tests compare the agent's own tables and price model against the installed
game across 10,000 price points, 800 randomised order sequences and 200
town-demand steps. The agent ships as one self-contained file and cannot import
`kaggle_environments`, so it carries its own copy of those constants. The game
version is pinned and checked on import.

## Building a submission

```bash
python -m scripts.build_submission
```

Inlines `agent/` into a single `dist/main.py`, imports that built file, and
plays full seasons with it against the installed game, including the self-play
episode run on upload. It checks the final bank rather than exit status, since
the agent catches its own errors and passes. The build fails if any turn
approaches the time limit.

The script never uploads. It prints the submit command instead of running it.

## License

MIT, see [LICENSE](LICENSE).
