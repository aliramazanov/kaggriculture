"""Bundle `agent/` into a single self-contained `main.py` and verify it.

Kaggle requires `main.py` at the archive root exposing `agent(obs)`. Shipping a
package would depend on the import path resolving inside the competition sandbox,
so instead every module is inlined into one file in dependency order and the
intra-package imports are stripped.

The packed file is then imported from disk and played to completion against the
real game. Checking the source tree would miss a bundle that imports here and
fails in the sandbox.

This never uploads. It writes `dist/`, records the git commit, and prints the
submit command instead of running it.

    python -m scripts.build_submission
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

#: Dependency order. Each module may only use names defined above it, which is
#: what makes naive concatenation a valid bundling strategy.
MODULES = ("gamedata", "params", "market", "tasks", "assign", "planner", "main")

#: A season that ends near the $3,000 opening bank means the agent never acted.
#: Set well below normal play, so this catches breakage without being brittle.
MIN_FINAL_BANK = 20_000

#: Kaggle allows 1000ms per turn. Anything near that on a fast local machine is
#: a timeout waiting to happen on a slower one, so the build fails well short.
TURN_BUDGET_SECONDS = 0.25

HEADER = '''"""Kaggriculture agent.

A closed-loop planner for a two-player farming and market simulation. Entry
point is `agent(obs)` at the bottom of this file.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
'''

# Imports to drop, because the bundle supplies these names itself.
#
# The parenthesised form is matched first and spans lines: `[^)]*` accepts
# newlines, so it swallows the whole statement. Stripping only its opening line
# would leave the names behind as stray, un-indented code.
_STRIP_PATTERNS = (
    r"^from (?:agent(?:\.\w+)?|collections\.abc|dataclasses) import \([^)]*\)$",
    r"^from (?:agent(?:\.\w+)?|collections\.abc|dataclasses|__future__) import .*$",
    r"^import math$",
)


def bundle() -> str:
    """Concatenate the agent package into one importable module."""
    parts = [HEADER]

    for name in MODULES:
        src = (ROOT / "agent" / f"{name}.py").read_text()
        src = re.sub(r'\A\s*"""(?:.|\n)*?"""', "", src, count=1)  # module docstring

        for pattern in _STRIP_PATTERNS:
            src = re.sub(pattern, "", src, flags=re.M)
        # `from agent import assign as assign_mod` style aliases become direct calls.
        src = src.replace("assign_mod.", "").replace("tasks_mod.", "").replace("market.", "")
        parts.append(f"\n# {'-' * 70} {name}\n")
        parts.append(src.strip() + "\n")

    return "\n".join(parts)


def load_packed(path: Path):
    """Import the built artifact from disk, exactly as the sandbox would."""
    spec = importlib.util.spec_from_file_location("packed_submission", path)

    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["packed_submission"] = module
    spec.loader.exec_module(module)

    return module


def verify(path: Path) -> None:
    """Play a full season with the packed file against the real game.

    Raises `RuntimeError` on any failure. The agent catches its own errors and
    passes instead, so a successful import does not show that it works.
    """
    from kaggle_environments import make

    module = load_packed(path)

    if not hasattr(module, "agent"):
        raise RuntimeError("packed main.py does not expose agent()")

    slowest = 0.0

    def timed(obs, config=None):
        nonlocal slowest
        started = time.perf_counter()
        action = module.agent(obs)
        slowest = max(slowest, time.perf_counter() - started)
        return action

    for opponent in ("starter", "random"):
        env = make("kaggriculture", configuration={"seed": 4242424})
        env.run([timed, opponent])
        us, them = env.state[0], env.state[1]

        if us.status != "DONE":
            raise RuntimeError(f"vs {opponent}: status={us.status}")

        bank = us.observation["farms"][0]["money"]
        theirs = them.observation["farms"][1]["money"]
        print(f"   vs {opponent:<8} bank {bank:>9,.0f}  theirs {theirs:>9,.0f}")

        # A turn that raises is caught and turned into a legal PASS, so a broken
        # agent still finishes DONE holding its opening cash. Status alone
        # therefore proves nothing; the bank is what shows the agent acted.
        if bank < MIN_FINAL_BANK:
            raise RuntimeError(
                f"vs {opponent}: final bank {bank:,.0f} is below {MIN_FINAL_BANK:,}. "
                f"The agent is failing every turn and passing instead of playing."
            )

        if bank <= theirs:
            raise RuntimeError(f"vs {opponent}: lost, {bank:,.0f} against {theirs:,.0f}")

    # Self-play, both seats driven from one module, which also exercises any
    # state shared between them.
    env = make("kaggriculture", configuration={"seed": 20260923})
    env.run([timed, timed])
    banks = [env.state[i].observation["farms"][i]["money"] for i in (0, 1)]

    print(f"   validation (self-play)  banks {banks[0]:,.0f} / {banks[1]:,.0f}")

    for i, s in enumerate(env.state):
        if s.status != "DONE":
            raise RuntimeError(f"validation episode: seat {i} status={s.status}")

    if min(banks) < MIN_FINAL_BANK:
        raise RuntimeError(
            f"validation episode: seat bank {min(banks):,.0f} below {MIN_FINAL_BANK:,}"
        )

    print(f"   slowest turn {slowest * 1000:.1f}ms (budget {TURN_BUDGET_SECONDS * 1000:.0f}ms)")

    if slowest > TURN_BUDGET_SECONDS:
        raise RuntimeError(f"slowest turn {slowest * 1000:.0f}ms exceeds budget")


def git(*args: str) -> str:
    try:
        done = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT)
        return done.stdout.strip()
    except OSError:
        return ""


def main() -> int:
    DIST.mkdir(exist_ok=True)
    main_py = DIST / "main.py"
    main_py.write_text(bundle())
    print(f"bundled {len(MODULES)} modules -> {main_py} ({main_py.stat().st_size:,} bytes)")

    compiled = subprocess.run(
        [sys.executable, "-m", "py_compile", str(main_py)], capture_output=True, text=True
    )

    if compiled.returncode:
        print("SYNTAX ERROR in bundle:\n" + compiled.stderr)
        return 1

    print("\nverifying the PACKED artifact against the real game:")

    try:
        verify(main_py)
    except Exception as exc:
        print(f"VERIFY FAILED: {exc}")
        return 1

    archive = DIST / "submission.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(main_py, arcname="main.py")
    with tarfile.open(archive) as tar:
        members = tar.getnames()

    sha = hashlib.sha256(main_py.read_bytes()).hexdigest()
    commit = git("rev-parse", "--short", "HEAD") or "uncommitted"
    dirty = bool(git("status", "--porcelain"))
    tag = commit + ("-dirty" if dirty else "")

    print(f"\narchive  {archive}  members={members}")
    print(f"main.py  {main_py.stat().st_size:,} bytes  sha256 {sha}")
    print(f"commit   {tag}")

    if dirty:
        print(
            "  NOTE: uncommitted changes present; this build is not reproducible "
            "from the repository as it stands."
        )

    print("\nNothing has been uploaded. To submit, run this yourself:")
    print("  kaggle competitions submit kaggriculture \\")
    print(f"      -f {archive} \\")
    print(f'      -m "{tag} | sha {sha[:12]}"')

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
