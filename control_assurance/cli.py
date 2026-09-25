#!/usr/bin/env python
"""Single entry point: `control-assurance <verb> ...`.

Two verbs, in the order a new user meets them:

    discover   point at a codebase with NO control register and get a draft one
    prove      break each declared control on purpose and require its named test to fail

`discover` exists first because `prove` has a prerequisite -- a register -- that almost nobody has. A tool
whose first run is impossible for most of its audience has no first run.

Each verb's `main` also accepts its own name as a leading argument, so `python -m control_assurance.prove`
and `control-assurance prove` behave identically.
"""
from __future__ import annotations

import sys

VERBS = {"discover": "control_assurance.discover", "prove": "control_assurance.prove"}

USAGE = """control-assurance <verb> [options]

  discover   propose a control register for a codebase that has none
             control-assurance discover --root . --out controls.yml

  prove      prove each declared control can actually fail
             control-assurance prove --registry controls.yml --root . --all

Start with `discover` if you have no register yet.
"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    verb = argv[0]
    if verb not in VERBS:
        print(f"unknown verb {verb!r}\n")
        print(USAGE)
        return 2
    import importlib
    return importlib.import_module(VERBS[verb]).main(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
