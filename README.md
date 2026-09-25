# control-assurance

**Prove that a safety control can actually fail.**

A guard that cannot go red is not a guard. This tool takes the controls you have *declared*, breaks each one on
purpose in a throwaway copy of your project, and requires the test you named to fail. If the test still passes,
the control was decoration — and you find that out from a command instead of from an incident.

```
max_position_cap          PROVED       1/1 mutations detected
admission_floor           NOT_PROVED   0/1 mutations detected
```

**That second line is the product.**

## What this measures that other tools do not

Coverage tells you a line ran. Mutation testing (`mutmut`, `cosmic-ray`) tells you your suite notices changes to
code the tool picked. Neither answers the question an auditor actually asks:

> You wrote down that this system refuses orders above the cap. What test fails if that stops being true, and
> have you ever seen it fail?

`control-assurance` binds a **declared claim** to a **named falsifier** and refuses the claim when nothing
carries it. The unit is not a line or a function — it is the sentence in your control register.

And it enforces one distinction that is the whole point: **a scan proves a thing exists; it never proves that it
works.** A registry entry matching a string, a function name or an invocation line establishes presence, nothing
more. Behaviour is established only by breaking the thing and watching a named test notice.

## Install

```bash
pip install git+https://github.com/mrbestnaija/control-assurance
```

Python 3.10+. Requires `pyyaml` and `pytest`. There is **no PyPI release yet**, so
`pip install control-assurance` will not resolve — use the line above until this note is gone.

Verified on a first run from a clean clone: Ubuntu 22.04 / Python 3.10.12, and Windows / Python 3.12.

**If `control-assurance` is not found after installing,** pip put the console script in a directory that is not
on your `PATH`. This is the normal outcome of a `--user` install, which pip chooses automatically when
site-packages is not writable — and it is easy to mistake for a failed install, because the package is in fact
installed:

```bash
python -m control_assurance.cli prove --help   # always works, no PATH needed
~/.local/bin/control-assurance prove --help    # the script pip actually wrote
export PATH="$HOME/.local/bin:$PATH"           # or put it on PATH
```

The `python -m` form is the one to use in CI and in scripts, because it does not depend on `PATH` at all.

**Hard limit, stated before you install rather than after:** your falsifiers must be runnable by pytest.
`run_test` shells `python -m pytest <falsifier>`. A project whose tests run under `unittest` alone, `go test`,
`jest` or a shell script is out of scope until a runner adapter exists. There is no adapter and no date.

## Start here if you have no control register

Most teams don't have one, and `prove` needs one. So don't write it — generate it:

```bash
control-assurance discover --root . --out controls.yml
```

One command, no configuration, no prerequisite. It reads your Python, proposes the functions that look like
enforcement points, and tells you three things immediately:

```
  19 propose a BEHAVIOUR claim (a test names them and they raise on refusal)
 106 have NO test naming them at all
 152 have ZERO call sites outside their own file -- present, possibly never reached

  0 of 198 are PROVED.
```

That last line is the number this tool exists to move, and you get it on the first run against a codebase
that had no register a minute earlier.

**These are candidates, not findings.** Detection is lexical — a function named `reject_*`, or one whose
docstring's first line speaks of refusal *and* which actually raises. A guard named `_h` with no docstring is
invisible to it; a helper called `validate_email_format` is proposed though it enforces nothing interesting.
Both errors are cheap and neither is hidden. Delete what does not matter; the ones left are your register.

**`discover` writes the mutations too, so the next command already works:**

```bash
control-assurance prove --registry controls.yml --root . --all
```

```
enforce_max_position   PROVED       1/1 mutations detected
enforce_min_lot        NOT_PROVED   0/1 mutations detected
    guard_disabled_enforce_min_lot: UNDETECTED -- the falsifier passed while the control was broken
```

Two commands, no configuration, no register beforehand, nothing hand-edited — and the second line is a real
finding: that control's test passes whether or not the control works.

It proposes a mutation by finding the `if ...:` guard that leads to a `raise` and turning it off. It only does
so when the guard is a single line and appears exactly once in the file, because `prove` refuses an ambiguous
anchor and a multi-line condition cannot be replaced without producing a syntax error. Where no such guard is
found it writes a `TODO` stub instead and says so — a mutation you must write yourself, rather than a
plausible-looking one that proves nothing.

## Try it on the shipped example

The distribution ships a two-control example — one honest falsifier, one lazy one — so you see a `PROVED` and
a `NOT_PROVED` together:

```bash
control-assurance prove --registry examples/demo/controls.yml --root examples/demo --all
```

A tool that only ever prints success teaches nothing about what it measures.

## Use it on your own project

```yaml
# controls.yml
controls:
  - name: max_position_cap
    asserts: behaviour
    falsified_by: tests/test_risk_limits.py
    mutations:
      - id: cap_removed
        file: src/risk_limits.py
        anchor: '    if size > cap:'
        replacement: '    if False:'
```

```bash
control-assurance prove --registry controls.yml --root . --all --json proof.json
```

Exit 0 when every control proved, 1 otherwise. `--json` writes a report carrying a sha256 of its own content,
suitable for attaching to an audit trail.

## Verdicts

| Verdict | Meaning |
|---|---|
| `PROVED` | every declared mutation applied and the named falsifier failed on each |
| `NOT_PROVED` | a mutation left the falsifier passing — the control did not notice being broken |
| `BASELINE_FAILS` | the falsifier was already failing; it can prove nothing |
| `NO_FALSIFIER` | the control names no test |
| `NO_MUTATIONS` | the control names no way to break it |

`HARNESS_FAILURE` appears per mutation when a mutation could not be honestly applied — the anchor matched zero
or several times, or the mutated file does not parse. It is **never** counted as a detection and never silently
skipped.

## Three refusals, each one a real defect it was built from

**A mutation that did not apply proves nothing.** An anchor must occur exactly once. A stale anchor is the most
common way a mutation suite rots, and treating it as a skip converts rot into a green light.

**A mutation must compile.** On 2026-09-21 a YAML plain scalar folded a three-line replacement into one line of
invalid Python. The falsifier failed — on a `SyntaxError`, not on the planted defect — and the tool reported
`PROVED`. That false proof reached the project's operator before anyone read what the YAML parsed to. The guard
now compiles every mutated `.py`, because a mutation must be valid code that behaves *wrongly*; code that does
not parse tests the interpreter, not your control. Two tests pin it, including one that a legitimate multi-line
mutation is still applied normally.

**Your source tree is never written to.** Mutations land in a temp copy, and a test asserts the bytes of the
mutated file are identical before and after a full run. That test exists because an in-place harness once
rewrote every line ending and then hashed the *text* rather than the bytes, reporting the restore as clean.

## Honest limits

- **Pytest only.** See above.
- **You write the mutations.** Deliberately: a generated mutation tests your code, a hand-written one tests *the
  claim you made*. Your coverage is exactly as good as your willingness to write down how each control could
  break, and that is the real work. The tool is the cheap part.
- **A proof decays.** It is a fact about one tree at one moment. Run it in CI or it means nothing next month.
- **Minutes, not seconds.** Each mutation copies the tree and runs pytest. Measured 1m35s for four controls on
  the originating project. CI-scale, not pre-commit-scale.
- **An existence claim is legitimate.** Many controls honestly assert only that something is present, and
  mutating those is theatre. The tool restricts its demand to entries declaring `asserts: behaviour`.

## The authors' own coverage

Published because a tool whose authors will not show their own number should not be trusted with yours. On the
originating project: **4 of 22** active behaviour claims are mutation-proved. The other 18 each need a falsifying
test written before they can be proved at all.

## Licence

**Apache License 2.0.** See [`LICENSE`](LICENSE). Copyright 2026 Bestman Ezekwu Enock.

You may use, modify and redistribute this, including commercially, provided you keep the notice and state your
changes. The licence also grants a patent licence from contributors and asks that you not use the author's name
or marks to endorse your derivative. If you find a control this proves is *not* actually falsifiable, a patch or
an issue is worth more to the project than a private workaround.
