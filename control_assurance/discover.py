#!/usr/bin/env python
"""discover.py -- point it at ANY repository and get a draft control register in one command.

WHY THIS EXISTS. `prove` requires a control register: a list of claims, each naming the test that would fail
if the claim stopped holding. Almost no engineering team has one. That prerequisite -- not price, not
features -- is the ceiling on who can use this tool at all, because it makes the first run impossible for
anyone who has not already done the work. This module removes it: it reads a codebase and proposes the
register, so the first run costs the user nothing but a command.

WHAT IT PRODUCES, and what it deliberately does not. It emits CANDIDATES for a human to accept, edit or
delete -- never a verdict about anyone's code. A candidate is a function that looks like an enforcement point
(it refuses, rejects, blocks, validates, caps, guards) together with what was found near it: how many call
sites outside its own file, and whether any test names it. From those two facts it suggests an `asserts`
level, and it NEVER proposes `behaviour` without a named test, because that is precisely the claim this
package refuses to let anyone record unproved.

HOW IT DECIDES, and the assumption that would break it. Detection is LEXICAL: the function's name and its
docstring are matched against enforcement verbs. A guard named `_h` with no docstring is invisible to it, and
a helper called `validate_email_format` is proposed though it enforces nothing interesting. Both errors are
cheap -- one omission a human notices, one deletion a human makes -- and neither is hidden. It reads Python
only, and it makes no network call.

Usage:
    control-assurance discover --root ../their-repo
    control-assurance discover --root . --out controls.yml
Exit codes: 0 always, unless the root is unreadable. This command reports; it never judges.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

from control_assurance.ignore import JUNK_DIRS, is_ignored  # noqa: E402

# Kept as a name for readability and for anyone importing it; the DEFINITION lives in one place.
# It previously omitted `.pytest_tmp`, which `prove` excluded -- so `discover` scanned 227 throwaway .py
# files from pytest fixture runs and its candidate count drifted upward all session while the scoring logic
# had not changed at all.
IGNORE_DIRS = JUNK_DIRS

# Verbs of REFUSAL. Deliberately verbs only: an earlier version included the nouns `threshold`, `ceiling`,
# `floor` and `limit`, which appear in ordinary analysis code constantly -- one docstring reading "sweep TS
# thresholds" made a `main()` a proposed control. That single change is most of the difference between 2,127
# candidates and a register a human will actually read.
ENFORCE = re.compile(
    r"refus|reject|block|deny|forbid|prevent|enforce|guard|validat|abort|halt|quarantin|clamp|"
    r"disallow|veto|must_not|sanitis|sanitiz|authoris|authoriz", re.I)

# Names too generic to be a control, and whose call counts collide across a whole repository.
GENERIC = {"main", "run", "setup", "teardown", "init", "wrapper", "inner", "handler", "process",
           "execute", "call", "apply", "check", "get", "set", "update", "load", "save", "parse"}
# Words that mean "this is a test", used to find a falsifier that already exists.
TEST_FILE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$")


def _py_files(root: Path):
    """Every .py under root, skipping ignored directories BELOW root.

    The ignore check must run on the path RELATIVE to root, never the absolute path. Matching absolute parts
    means the root's own ancestors are tested too: pointed at
    `.../cavenv/Lib/site-packages/_pytest`, every single file was excluded because an ancestor is named
    `site-packages`, and the command reported "0 candidate control(s) found" in zero seconds -- a clean bill
    of health for a codebase it had not read one byte of. Any user whose project sits under a directory
    called build, dist, venv or archive would have got the same silent nothing. Found by running against a
    codebase this tool did not write, which is the only way it could have been found.
    """
    for p in root.rglob("*.py"):
        if not is_ignored(p.relative_to(root).parts):
            yield p


def _is_comment(line: str) -> bool:
    s = line.strip()
    return s.startswith("#") or not s


def candidates(root: Path) -> list[dict]:
    """Functions that look like enforcement points, with the evidence found near each."""
    found: list[dict] = []
    for f in _py_files(root):
        if TEST_FILE.search(f.relative_to(root).as_posix()):
            continue                                            # a test is not a control
        text = f.read_text(encoding="utf-8", errors="replace")
        src_lines = text.splitlines()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.lower().strip("_") in GENERIC or node.name.startswith("__"):
                continue
            doc = ast.get_docstring(node) or ""
            first = doc.strip().splitlines()[0] if doc.strip() else ""
            # A control that only ever returns a value and never refuses is usually a computation.
            raises = any(isinstance(n, ast.Raise) for n in ast.walk(node))
            # Two ways in, and the second is deliberately stricter. The NAME saying `reject_...` is a claim
            # the author made on purpose. A docstring mentioning refusal anywhere is far weaker evidence, so
            # it counts only in the FIRST line and only when the function actually raises. Matching the whole
            # docstring proposed 2,127 candidates on this repository, which is a haystack, not a register.
            if not (ENFORCE.search(node.name) or (ENFORCE.search(first) and raises)):
                continue
            found.append({"name": node.name, "file": f.relative_to(root).as_posix(),
                          "line": node.lineno, "raises": raises,
                          "guard": _guard_anchor(node, src_lines, text),
                          "doc_first_line": doc.strip().splitlines()[0][:120] if doc.strip() else ""})
    return found


def _guard_anchor(node, src_lines: list[str], whole: str) -> str | None:
    """The `if ...:` line that leads to a raise -- a mutation anchor that actually breaks the control.

    Emitting a `TODO` placeholder instead means the user's first `prove` run returns HARNESS_FAILURE on every
    control until they hand-edit each anchor. That is a wall in front of the only output that demonstrates
    the product. Proposing the real guard line makes discover -> prove work end to end on the first run.

    Three conditions, all necessary, or None is returned and the entry falls back to a TODO stub:
      - the `if` must be a ONE-LINE statement. A condition wrapped across lines cannot be replaced by a
        single `if False:` without leaving a dangling continuation, which is a SyntaxError -- and `prove`
        would correctly call that a HARNESS_FAILURE rather than a detection, so proposing it is pointless.
      - the line must occur EXACTLY ONCE in the file, because `apply_mutation` refuses an ambiguous anchor.
      - the branch must actually raise, or turning it off changes nothing observable.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.If):
            continue
        if not any(isinstance(n, ast.Raise) for n in ast.walk(sub)):
            continue
        idx = sub.lineno - 1
        if idx >= len(src_lines):
            continue
        line = src_lines[idx]
        if not line.rstrip().endswith(":") or not line.lstrip().startswith("if "):
            continue                                        # multi-line condition, or an elif/ternary
        if whole.count(line) != 1:
            continue                                        # ambiguous anchor; prove would refuse it
        return line
    return None


# One regex, applied once per line, capturing EVERY `identifier(` on it. Inverting the loop this way is the
# difference between a command that finishes and one that does not: the first version re-walked the tree per
# candidate (O(candidates x files) of disk I/O), and the second kept the tree in memory but still ran one
# regex per candidate per line (O(candidates x lines)). On this repository that exceeded ten minutes and was
# killed. Indexing once and looking each candidate up turns the per-candidate cost into a dict hit.
_CALL = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\(")
_WORD = re.compile(r"[A-Za-z_]\w*")


def index_repo(root: Path) -> tuple[dict, dict]:
    """Read every .py ONCE and return ({name: {file: count}}, {name: [test files]}).

    Source lines that are comments, imports or definitions are dropped before indexing, so a control is not
    credited with a call site for its own `def` line or for someone importing it.
    """
    calls: dict[str, dict[str, int]] = {}
    tests: dict[str, list[str]] = {}
    for f in _py_files(root):
        rel = f.relative_to(root).as_posix()
        text = f.read_text(encoding="utf-8", errors="replace")
        if TEST_FILE.search(rel):
            for name in set(_WORD.findall(text)):
                tests.setdefault(name, []).append(rel)
            continue
        for line in text.splitlines():
            if _is_comment(line):
                continue
            st = line.lstrip()
            if st.startswith(("def ", "async def ", "from ", "import ")):
                continue
            for name in _CALL.findall(line):
                calls.setdefault(name, {}).setdefault(rel, 0)
                calls[name][rel] += 1
    return calls, tests


def call_sites(calls: dict, name: str, defining_file: str) -> int:
    """Calls to `name(` outside its own file."""
    return sum(n for rel, n in calls.get(name, {}).items() if rel != defining_file)


def naming_tests(tests: dict, name: str) -> list[str]:
    """Test files that mention the control by name -- a falsifier that may already exist."""
    return sorted(tests.get(name, []))


def build(root: Path) -> dict:
    calls, tests_idx = index_repo(root)
    rows = []
    for c in candidates(root):
        sites = call_sites(calls, c["name"], c["file"])
        tests = naming_tests(tests_idx, c["name"])
        # NEVER propose `behaviour` without a named test. That is the claim this package refuses to record
        # unproved, and proposing it here would be the tool committing the defect it exists to catch.
        asserts = "behaviour" if (tests and c["raises"]) else "existence"
        rows.append({**c, "call_sites": sites, "tests": tests, "asserts": asserts})
    rows.sort(key=lambda r: (r["asserts"] != "behaviour", -r["call_sites"], r["file"]))
    return {"root": str(root), "candidates": rows}


def to_registry_yaml(data: dict) -> str:
    """A registry the user can run `prove` against after filling in the mutations."""
    L = ["# DRAFT control register, proposed by `control-assurance discover`.",
         "#",
         "# These are CANDIDATES, not findings. Accept, edit or delete each one -- a lexical scan cannot know",
         "# which of your functions carry a claim that matters. Nothing here judges your code.",
         "#",
         "# To make an entry provable, add `mutations`: how to break the control on purpose. Until then",
         "# `prove` reports NO_MUTATIONS, which is honest -- the claim is recorded but not carried.",
         "",
         "version: 1", "controls:"]
    for r in data["candidates"]:
        dotted = r["file"][:-3].replace("/", ".") + "." + r["name"]
        L += [f"  - name: {r['name']}",
              "    kind: function",
              f"    target: {dotted}",
              '    search_globs: ["**/*.py"]',
              f"    min_call_sites: {1 if r['call_sites'] else 0}",
              "    status: active",
              f"    asserts: {r['asserts']}"]
        if r["tests"]:
            L.append(f"    falsified_by: {r['tests'][0]}")
        L.append(f"    rationale: >")
        L.append(f"      PROPOSED by discover from {r['file']}:{r['line']}. "
                 f"{'Raises on refusal. ' if r['raises'] else 'No raise found, so it may compute rather than refuse. '}"
                 f"{r['call_sites']} call site(s) outside its own file. "
                 f"{'A test names it. ' if r['tests'] else 'NO test names it. '}"
                 f"{r['doc_first_line']}")
        L.append('    added_on: "PROPOSED"')
        if r["asserts"] == "behaviour":
            L.append("    mutations:")
            if r.get("guard"):
                indent = r["guard"][:len(r["guard"]) - len(r["guard"].lstrip())]
                L += [f"      - id: guard_disabled_{r['name']}",
                      f"        file: {r['file']}",
                      f"        anchor: {json.dumps(r['guard'])}",
                      f"        replacement: {json.dumps(indent + 'if False:')}"]
            else:
                L += [f"      - id: TODO_break_{r['name']}",
                      f"        file: {r['file']}",
                      "        anchor: 'TODO: a line that exists EXACTLY once, which you will break'",
                      "        replacement: 'TODO: the broken version. It must still COMPILE'"]
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if argv and argv[0] == "discover":
        argv = argv[1:]
    ap = argparse.ArgumentParser(prog="control-assurance discover",
                                 description="Propose a control register for a codebase that has none.")
    ap.add_argument("--root", default=".", type=Path)
    ap.add_argument("--out", default=None, type=Path, help="write the draft register here")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    data = build(root)
    rows = data["candidates"]
    behaviour = [r for r in rows if r["asserts"] == "behaviour"]
    untested = [r for r in rows if not r["tests"]]
    uncalled = [r for r in rows if r["call_sites"] == 0]

    n_py = sum(1 for _ in _py_files(root))
    print(f"scanned {root}")
    print(f"  {n_py} Python file(s) read")
    if not rows:
        # Zero candidates is not a clean bill of health, and must never read like one. Reporting the file
        # count alongside it is what distinguishes "nothing here enforces anything" from "nothing was read".
        print("\n0 candidate control(s) found.")
        if n_py == 0:
            print("\n  NO PYTHON FILES WERE READ. This is almost certainly the tool's problem, not yours:")
            print("  check the --root path, and note that directories named build, dist, venv, archive or")
            print("  site-packages are skipped BELOW the root (the root itself is never skipped).")
        else:
            print(f"\n  {n_py} file(s) were read and none looked like an enforcement point. That can be true")
            print("  -- plenty of code computes rather than refuses -- but detection is lexical, so a guard")
            print("  named `_h` with no docstring is invisible to it. Absence here is not evidence of")
            print("  absence in your code.")
        return 0
    print(f"\n{len(rows)} candidate control(s) found.\n")
    print(f"{'control':<34}{'calls':<7}{'test?':<7}{'proposed':<11}file")
    for r in rows[:40]:
        print(f"{r['name'][:33]:<34}{r['call_sites']:<7}{'yes' if r['tests'] else 'NO':<7}"
              f"{r['asserts']:<11}{r['file']}")
    if len(rows) > 40:
        print(f"... and {len(rows) - 40} more")

    print(f"\n  {len(behaviour)} propose a BEHAVIOUR claim (a test names them and they raise on refusal)")
    print(f"  {len(untested)} have NO test naming them at all")
    print(f"  {len(uncalled)} have ZERO call sites outside their own file -- present, possibly never reached")
    print(f"\n  0 of {len(rows)} are PROVED. Nothing here has been broken on purpose yet, so nothing here is")
    print("  known to fail when it should. That is the number this tool exists to move.")
    if uncalled:
        print(f"\n  Start with the {len(uncalled)} uncalled one(s): a control nothing reaches cannot refuse")
        print("  anything, whatever its tests say.")

    if a.out:
        a.out.write_text(to_registry_yaml(data), encoding="utf-8", newline="\n")
        print(f"\ndraft register -> {a.out}")
        print("Edit it, add mutations to the behaviour claims, then:")
        print(f"  control-assurance prove --registry {a.out} --root {a.root} --all")
    else:
        print("\nPass --out controls.yml to write a draft register you can edit and prove.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
