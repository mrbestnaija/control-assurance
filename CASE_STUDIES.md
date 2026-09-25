# Five case studies

DRAFT for operator review, 2026-09-22. Each case is a measured defect in the project this tool was built
inside. Commit identifiers are given where the fix is in version control, and the one that is not committed
says so in the same place rather than at the bottom.

**Provenance note, stated once:** these are the authors' own defects, not a customer's. No customer exists yet.
Presenting internal findings as case studies is legitimate; presenting them as third-party validation would not
be, so nothing below claims a client.

---

## Case 1 — A comment was counted as enforcement

**Fix: commit `7dbd8e80`.**

### What the register said

Forty controls, each with a `min_call_sites` requirement. A gate ran on every commit and in CI, counting
production call sites for each control and failing the build if any active control fell below its floor. It
reported **all controls wired, zero failures**, and had done for weeks.

### What was true

Two of those controls were dead, and a **single comment** naming both functions was being counted as their call
sites. The gate matched text. A comment is text.

### How it was found

Not by the gate, and not by a test. By deliberately asking the inverse question — *what would this gate count
that is not enforcement?* — and then measuring. After the gate learned to skip comment lines for token and
function kinds:

| control | call sites before | after |
|---|---|---|
| `send_direct_alert` | 25 | 22 |
| `contaminate_close_leg` | 6 | 5 |
| `clear_evidence_exclusion` | 5 | 4 |

Three controls had been counting their own documentation. Two were below their floor once documentation stopped
counting.

### Why it matters to you

The failure mode of a presence check is not a false negative — it is a **confident false positive**, and the
thing producing it looks exactly like evidence. A green register is not weaker evidence than a red one; it is
evidence of the opposite thing, and it is indistinguishable from the real thing until something breaks the
control on purpose.

---

## Case 2 — A sizing constraint with zero callers, registered for months

**Fix: commit `7dbd8e80`** — the same commit, because wiring the control and repairing the gate that had been
lying about it are one piece of work.

### What the register and the config said

A position-sizing constraint, registered and active. A configuration file declaring the bounds it enforced:
safe bucket minimum weight `0.75`, speculative maximum `0.10`. The function's own docstring described it as
*"the integration point for the live auto-trader sizing path."*

### What was true

**Zero callers.** The function existed, had tests, was documented, was registered — and was never invoked from
the sizing path. The configured bounds constrained nothing at all, for months. Every position the system sized
was sized without reference to them.

The presence claim was **true the entire time**. The control existed. It just never ran.

### How it was found

By registering the control in a register that demanded a *call site count* rather than an existence check, and
watching the count come back zero. The fix wired the constraint into the sizing path for every non-reducing
position change, with a refusal on breach and a refuse-on-exception default — then declared three mutations
(remove the refusal branch, ignore the projection's answer, stop counting cash as safe) and proved the
falsifier catches all three: **PROVED 3/3**.

### Why it matters to you

"Registered" and "enforced" are different facts, and in most organisations only the first one is measured. This
is the class the tool is named for, and it is the one that survives audits — because the artifact an auditor
asks for is the register, and the register was accurate.

---

## Case 3 — The tool produced a false PROVED on its first serious use

**Not yet committed.** The fix is written and its tests pass; it sits in the working tree behind the
originating project's review gate. Stated here because a case study about an honesty mechanism cannot itself
overstate its status.

### What happened

The authors registered a doctrine control: a particular statistical formula must not appear in one module. The
declared mutation planted the formula back in. `prove` ran it and reported **PROVED — 2/2 mutations detected**,
and that result was reported to the project's operator.

### What was true

The mutation's three-line replacement sat in a **YAML plain scalar**, and YAML folds newlines in plain scalars
into spaces. The planted code was therefore one line of invalid Python. The falsifier failed — on a
`SyntaxError`, not on the planted formula. The tool had scored a parse error as a detection.

The control was never proved. The claim reached the operator before anyone read what the YAML parsed to.

### How it was found

While building a second mutation for a different control, the same YAML fold was hit again and the parsed
value was printed. Not by a test. Twenty minutes after the claim was made.

### The fix, in the mechanism rather than the data

`prove` now **compiles every mutated `.py`** and raises `HARNESS_FAILURE` when it does not parse, on the stated
reasoning that *a mutation must be valid code that behaves wrongly — code that does not parse tests the
interpreter, not the control.* Re-running it immediately reproduced the original fault as a failure, which is
how the guard is known to bite. Two tests pin it: one that a non-parsing mutation is a harness failure and not
a detection, and one that a **legitimate** multi-line mutation is still applied normally, so the guard cannot
become an excuse for rejecting real mutations.

Both folded mutations were then rewritten with explicit newline escapes and verified to carry real newlines
before re-running. All four controls now prove genuinely.

### Why it matters to you

The tool that proves your controls needs its own proof, and **the first serious bug in such a tool will be one
that makes it say yes.** That is the direction bugs in verification tools take, because a false negative gets
investigated and a false positive gets believed.

This case is in the sales material deliberately. A vendor who ships a verification tool and has never found it
wrong has not looked.

---

## Case 4 — The tool reported a clean scan of a codebase it never read

**Fix: commit `d406c91a`.**

### What happened

`discover` was pointed at `_pytest` — a real codebase, written by strangers, in a clean virtualenv. It
returned, in zero seconds:

```
0 candidate control(s) found.
```

Read as a clean bill of health. It was nothing of the kind.

### What was true

The ignore list was matched against the **absolute** path, and the virtualenv path contains `site-packages`.
Every file was excluded before a single byte was read. The scan never happened.

Any user whose project sits under a directory named `build`, `dist`, `venv`, `archive` or `site-packages`
would have received the same silent nothing — and a `0` that looks exactly like a passing result.

### How it was found

Not by a test, and not from inside the repository that wrote the tool — that repository has no such ancestor,
so every prior run had looked fine. It was found by **running the tool against code it did not write**, which
had never been done before and took four minutes.

### The fix, and the second one it forced

Match relative to the scan root, verified non-regressive by set comparison: 1,348 files under both the old
and new rule, zero added. And, because a bare `0` must never read as a pass, the command now reports the
**file count** alongside it and distinguishes two entirely different outcomes: *nothing was read* — the
tool's problem, with the likely cause named — versus *files were read and none looked like enforcement*.

### Why it matters to you

This is the tool committing its own defect class: a green result certifying a path that never ran. It is
also the general principle — **an unknown is never a pass** — and the only thing that exposed it was
pointing it somewhere unfamiliar. Every verification tool you own has a configuration in which it silently
checks nothing. Ask which one.

---

## Case 5 — Two lists for one idea, drifting apart in plain sight

**Fix: shared `control_assurance/ignore.py`.**

### What happened

The tool's own reported number moved. On the same repository it said 198 candidates, then 238, then 264 —
across a single session, while nobody had touched the scoring logic.

### What was true

Two commands each carried their own "what is not source code" list, and they disagreed: `prove` excluded the
pytest scratch directory, `discover` did not. That directory held **227 throwaway `.py` files** from test
fixture runs, and every test run added roughly 39 more. The tool was proposing controls from its own rubbish,
and its published number drifted upward as a side effect of running its own tests.

### How it was found

By refusing to let an unexplained number stand. The earlier build was checked out and run against the same
tree: it produced **264 too** — proving the logic was identical and the tree had changed. That inverted the
question from "what did I change in the code" to "what changed in the directory", and the scratch files were
immediately visible.

After consolidating both commands onto one definition: **264 → 199, stable across runs**, and 226 fewer files
read, matching the 227 scratch files.

### Why it matters to you

Two definitions of one concept do not stay equal; they drift, silently, and the drift shows up as a number
that moves for no visible reason. If a figure in your reporting changes and the diff shows nothing, the
disagreement is between two copies of a rule, not inside one.

---

## What these have in common

None of the three was caught by a test. Each was caught by asking what the green signal could mean *other* than
the thing it was taken to mean — and then measuring instead of reasoning.

That is not a workflow you can buy. What you can buy is the one mechanical piece of it: breaking each declared
control on purpose and requiring a named test to fail. The tool does that part, repeatably, in CI, for every
claim you have written down.
