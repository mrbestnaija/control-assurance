#!/usr/bin/env python3
"""prove <control> -- does anything notice when this control breaks?

A control registry can already say that a control EXISTS and is CALLED. Neither answers the question that
matters: if the control stopped working, would any test fail? `prove` answers it by breaking the control on
purpose and requiring the control's own declared falsifier to fail.

For each mutation declared on the control:
  1. the anchor must occur EXACTLY ONCE in its file -- zero or many is a HARNESS_FAILURE, never a skip, because
     a mutation that did not apply proves nothing;
  2. the mutation is applied inside a SANDBOX COPY of the project, never to the working tree;
  3. the control's `falsified_by` test runs there and MUST FAIL.
And once, unmutated, that same test must PASS -- otherwise it is failing for its own reasons and proves nothing.

Exit 0 only when the baseline passes and every mutation is detected.

The sandbox is the point. An in-place harness in this repository restored a file with Path.write_text(), which
translates LF to CRLF on Windows, and its own integrity check could not see it because the check hashed TEXT.
Copying sidesteps that class entirely: the source tree is never written to.

Usage:
    python -m control_assurance.prove --registry config/control_registry.yml --root . --control max_position_cap
    python -m control_assurance.prove --registry ... --root . --all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from control_assurance.ignore import JUNK_DIRS  # noqa: E402

# The shared junk set, PLUS what a sandbox specifically must not carry: compiled artefacts, and the heavy
# runtime directories of the project this package was built in. `prove` may ADD to the shared set and never
# subtract from it -- the two modules previously kept separate lists that drifted, and `discover` ended up
# scanning the pytest scratch directory `prove` had always excluded.
IGNORE = shutil.ignore_patterns(*sorted(JUNK_DIRS),
                                "*.pyc", "*.egg-info", "*_env_win", "data", "logs")
SCHEMA_VERSION = 1


class HarnessFailure(RuntimeError):
    """The mutation could not be applied, so nothing was proved."""


def load_registry(path: Path) -> dict:
    import yaml
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "controls" not in doc:
        raise SystemExit(f"{path}: no 'controls' key")
    return doc


def select(doc: dict, name: str | None) -> list[dict]:
    controls = [c for c in doc["controls"] if isinstance(c, dict)]
    if name:
        hit = [c for c in controls if c.get("name") == name]
        if not hit:
            raise SystemExit(f"no control named {name!r}")
        return hit
    return [c for c in controls if c.get("mutations")]


def sandbox(root: Path) -> tuple[Path, list[str]]:
    """Copy the project to a throwaway tree. Returns (tree, unreadable paths).

    A locked or permission-denied path must not abort a proof: copytree copies everything else and raises at the
    end with the list, so the failures are RECORDED in the report rather than swallowed or fatal. Found by
    running this against its own repository, where stale .pytest_tmp directories from an unclean shutdown denied
    access and killed the whole run.
    """
    tmp = Path(tempfile.mkdtemp(prefix="prove_"))
    dst = tmp / root.resolve().name
    skipped: list[str] = []
    try:
        shutil.copytree(root, dst, ignore=IGNORE, symlinks=False)
    except shutil.Error as exc:
        skipped = [str(src) for src, _dst, _why in exc.args[0]]
    return dst, skipped


def run_test(where: Path, test: str, timeout: int) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
                          cwd=str(where), capture_output=True, text=True, timeout=timeout)
    tail = [ln for ln in (proc.stdout or "").splitlines() if " passed" in ln or " failed" in ln or " error" in ln]
    return proc.returncode, (tail[-1] if tail else "no pytest summary")


def apply_mutation(tree: Path, mutation: dict) -> None:
    target = tree / mutation["file"]
    if not target.exists():
        raise HarnessFailure(f"{mutation['file']} not in the project")
    text = target.read_text(encoding="utf-8")
    anchor = mutation["anchor"]
    n = text.count(anchor)
    if n != 1:
        raise HarnessFailure(f"anchor occurs {n} times; a mutation that did not apply proves nothing")
    mutated = text.replace(anchor, mutation.get("replacement", ""))
    # A mutation that breaks SYNTAX proves nothing: the falsifier then fails on an import error, not
    # because it noticed the defect, and the control is recorded PROVED on a false positive. Measured
    # 2026-09-21 on this very file's registry -- a YAML plain scalar folded a three-line replacement into
    # one line, so `spec = ... from scipy.stats import norm ...` was a SyntaxError and the "detection" was
    # the interpreter refusing to load the module. Same shape as the anchor rule above: never a skip.
    if target.suffix == ".py":
        try:
            compile(mutated, str(target), "exec")
        except SyntaxError as exc:
            raise HarnessFailure(
                f"mutation makes {mutation['file']} syntactically invalid ({exc.msg} at line {exc.lineno}); "
                f"the falsifier would fail on a parse error, not on the defect. A mutation must be VALID "
                f"code that behaves wrongly"
            ) from exc
    target.write_text(mutated, encoding="utf-8", newline="\n")


def prove_control(root: Path, control: dict, timeout: int, jobs: int = 1) -> dict:
    name = control.get("name", "<unnamed>")
    test = control.get("falsified_by")
    result = {"control": name, "asserts": control.get("asserts"), "falsified_by": test, "mutations": []}
    if not test:
        result["verdict"] = "NO_FALSIFIER"
        result["detail"] = "the control names no test that would fail if it broke, so nothing can be proved"
        return result
    mutations = control.get("mutations") or []
    if not mutations:
        result["verdict"] = "NO_MUTATIONS"
        result["detail"] = "the control declares no mutation, so its falsifier is untested against a real break"
        return result

    base, skipped = sandbox(root)
    try:
        rc, line = run_test(base, test, timeout)
        result["baseline"] = {"exit": rc, "pytest": line, "passes": rc == 0}
        if skipped:
            result["sandbox_unreadable_paths"] = skipped[:10]
    finally:
        shutil.rmtree(base.parent, ignore_errors=True)
    if not result["baseline"]["passes"]:
        result["verdict"] = "BASELINE_FAILS"
        result["detail"] = "the falsifier fails before any mutation, so its failure proves nothing"
        return result

    # Mutations are independent by construction -- each gets its own sandbox -- so they run concurrently.
    # Measured on this repository before changing anything: the tree copy is 2.41s and the pytest run 6.02s,
    # so pytest is 71% of the cost and optimising the copy alone could never win more than a third. Threads
    # are the right tool because both halves block on subprocess and disk rather than on the GIL. Isolation
    # is UNCHANGED: still one full copy per mutation, never a shared tree with in-place restore, because a
    # restore that goes wrong is how a harness once certified its own corruption.
    def _one(mutation: dict) -> dict:
        tree, _skipped = sandbox(root)
        row = {"id": mutation.get("id", "<unnamed>"), "file": mutation.get("file")}
        try:
            apply_mutation(tree, mutation)
            rc, line = run_test(tree, test, timeout)
            row.update({"exit": rc, "pytest": line, "detected": rc != 0})
        except HarnessFailure as exc:
            row.update({"detected": None, "HARNESS_FAILURE": str(exc)})
        finally:
            shutil.rmtree(tree.parent, ignore_errors=True)
        return row

    workers = max(1, min(jobs, len(mutations)))
    if workers == 1:
        result["mutations"] = [_one(m) for m in mutations]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Results are reassembled in DECLARATION order, not completion order, so the report is identical
            # whatever the scheduler does. A proof whose output depends on timing is not a proof.
            result["mutations"] = list(pool.map(_one, mutations))
        result["parallel_workers"] = workers

    undetected = [m for m in result["mutations"] if not m.get("detected")]
    result["verdict"] = "PROVED" if not undetected else "NOT_PROVED"
    result["undetected"] = [m["id"] for m in undetected]
    return result


def main(argv=None) -> int:
    # Accept an optional leading `prove` verb so BOTH invocations work:
    #     control-assurance prove --registry ...        (console script; the documented form)
    #     python -m control_assurance.prove --registry  (module path already supplies the verb)
    # Measured 2026-09-22 by installing the wheel into a clean virtualenv OUTSIDE the repository: the console
    # script maps to this function, which had no `prove` verb, so the documented command failed with
    # "unrecognized arguments: prove" on a stranger's first run. Every line of the product documentation used
    # that form. Running the tool only from the source checkout had hidden it, because the module path was
    # supplying the word. The docs were right and the entry point was wrong, so the entry point changed.
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if argv and argv[0] == "prove":
        argv = argv[1:]
    ap = argparse.ArgumentParser(prog="control-assurance prove",
                                 description="Prove that a control's falsifier notices when the control breaks.")
    ap.add_argument("--registry", required=True, type=Path)
    ap.add_argument("--root", default=Path("."), type=Path)
    ap.add_argument("--control", default=None, help="control name; omit with --all")
    ap.add_argument("--all", action="store_true", help="every control that declares mutations")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--top", type=int, default=None, metavar="N",
                    help="prove only the first N controls. A discovered register can hold hundreds; proving "
                         "all of them on a first run means one baseline pytest run EACH, which is a wall of "
                         "tens of minutes in front of the first finding. Bound it, see a NOT_PROVED, then "
                         "widen")
    ap.add_argument("--jobs", "-j", type=int, default=4,
                    help="mutations proved concurrently (each in its own sandbox); 1 disables")
    ap.add_argument("--json", dest="json_out", default=None, type=Path, help="write the report here")
    args = ap.parse_args(argv)
    if not args.control and not args.all:
        ap.error("pass --control NAME or --all")

    doc = load_registry(args.registry)
    controls = select(doc, args.control)
    # Parallelise across CONTROLS when there is more than one, with each control's own mutations serial,
    # so total concurrency stays bounded by --jobs instead of multiplying to jobs^2. Measured on this
    # registry: serial 97s; mutations-parallel-only 66s, because each control still paid for its own
    # baseline run in series and four baselines are most of what is left. Parallelising the outer loop is
    # what removes that. Results are reassembled in declaration order either way.
    total_selected = len(controls)
    if args.top is not None and args.top > 0:
        controls = controls[:args.top]

    if args.jobs > 1 and len(controls) > 1:
        with ThreadPoolExecutor(max_workers=min(args.jobs, len(controls))) as pool:
            results = list(pool.map(lambda c: prove_control(args.root, c, args.timeout, jobs=1), controls))
    else:
        results = [prove_control(args.root, c, args.timeout, jobs=args.jobs) for c in controls]
    if len(controls) < total_selected:
        # A partial run must never be reported as if it covered the register. The overall VERDICT below is
        # computed on what RAN, so saying how much did not is the difference between a bounded proof and a
        # flattering one.
        print(f"  proving {len(controls)} of {total_selected} control(s) that declare mutations "
              f"(--top {args.top}); the remaining {total_selected - len(controls)} were NOT examined")
        print()
    for r in results:
        detected = sum(1 for m in r["mutations"] if m.get("detected"))
        print(f"  {r['control']:34s} {r['verdict']:14s} {detected}/{len(r['mutations'])} mutations detected")
        for m in r["mutations"]:
            if m.get("HARNESS_FAILURE"):
                print(f"      {m['id']}: HARNESS_FAILURE {m['HARNESS_FAILURE']}")
            elif not m.get("detected"):
                print(f"      {m['id']}: UNDETECTED -- the falsifier passed while the control was broken")
    report = {"artifact": "control_assurance_proof", "schema_version": SCHEMA_VERSION,
              "generated_utc": datetime.now(timezone.utc).isoformat(),
              "registry": str(args.registry), "root": str(args.root.resolve()),
              "results": results,
              "controls_proved": len(controls), "controls_declaring_mutations": total_selected,
              "partial_run": len(controls) < total_selected,
              "VERDICT": "PROVED" if all(r["verdict"] == "PROVED" for r in results) else "NOT_PROVED"}
    payload = json.dumps(report, indent=2, sort_keys=True)
    report["sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"  report {args.json_out} sha256={report['sha256'][:16]}")
    return 0 if report["VERDICT"] == "PROVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
