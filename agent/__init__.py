"""Kaggriculture competition agent.

The public surface is a single callable with the signature the competition
requires: `agent(obs) -> {"farmer": [...], "hands": [...], "market": [...]}`.

Submodules are implementation detail and are inlined into one file at build
time by `scripts/build_submission.py`.
"""

from agent.main import agent

__all__ = ["agent"]
