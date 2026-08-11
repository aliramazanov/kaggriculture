"""The installed competition engine, used as ground truth in differential tests.

`agent/` carries its own copy of the market constants, because the shipped
submission is a single file with no dependency on `kaggle_environments`. That
copy is only safe while the two agree, so the tests compare against the
installed package directly.

The version is checked on import, so an upgraded dependency fails here rather
than silently changing every price the agent calculates.
"""

from __future__ import annotations

from importlib.metadata import version

from kaggle_environments.envs.kaggriculture import kaggriculture as engine

PINNED_VERSION = "1.32.6"

_installed = version("kaggle-environments")

if _installed != PINNED_VERSION:
    raise RuntimeError(
        f"kaggle-environments {_installed} is installed but the agent is "
        f"calibrated against {PINNED_VERSION}. Re-pin, or re-validate the "
        f"market model against it first."
    )

MARKET_PARAMS = engine.MARKET_PARAMS
PRODUCTS = engine.PRODUCTS
SHOPS = engine.SHOPS
TOWN_CENTER_PRODUCTS = engine.TOWN_CENTER_PRODUCTS
PRICE_FLOOR = engine.PRICE_FLOOR
market_price = engine.market_price

__all__ = [
    "MARKET_PARAMS",
    "PINNED_VERSION",
    "PRICE_FLOOR",
    "PRODUCTS",
    "SHOPS",
    "TOWN_CENTER_PRODUCTS",
    "engine",
    "market_price",
]
