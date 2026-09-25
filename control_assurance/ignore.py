"""One definition of "not the user's source", shared by every command in this package.

WHY THIS MODULE EXISTS. `prove` and `discover` each carried their own ignore list, and they disagreed.
`prove` excluded `.pytest_tmp`; `discover` did not. The consequence was measured on 2026-09-22: this
repository's `.pytest_tmp/` held 227 throwaway .py files from pytest fixture runs, `discover` scanned them,
and its candidate count climbed 198 -> 238 -> 264 across one session as each test run added roughly 39 more.
The tool was proposing controls from its own test rubbish, and the number in the documentation drifted while
nothing in the scoring logic had changed -- verified by running the earlier build against the same tree and
getting the identical 264.

That is the second-definition defect: two lists for one concept, drifting apart silently. This package
refuses to let its users record a claim nothing can falsify, so it should not keep two answers to "what is
source code" either.

Each command may ADD to the shared set, and neither may subtract -- `prove` additionally skips heavy runtime
directories it must not copy into a sandbox, and that is a sandboxing decision, not a disagreement about what
source is.
"""
from __future__ import annotations

# Directories that are never the user's source, for any command. Version control, caches, build output,
# virtual environments, vendored or archived copies, and other checkouts of the same project.
JUNK_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", ".pytest_cache", ".pytest_tmp", ".mypy_cache", ".ruff_cache", ".tox", "htmlcov",
    ".venv", "venv", "site-packages", "node_modules",
    "build", "dist", "vendor", "third_party",
    ".claude", "worktrees", "archive", ".archive", "baselines",
})


def is_ignored(relative_parts) -> bool:
    """True when any component of a path RELATIVE TO THE SCAN ROOT is junk.

    Relative is the whole point. Matching absolute parts tests the root's own ancestors too, so a project
    living under a directory named `build` or `site-packages` is excluded entirely -- which is exactly what
    happened when `discover` was first pointed at a package inside a virtualenv and reported "0 candidates"
    in zero seconds without reading a byte.
    """
    return any(part in JUNK_DIRS for part in relative_parts)
