"""Pin `prove`: the command has to fail in every way a proof can be fake.

A proof is fake when the falsifier passes while the control is broken, when the falsifier was already failing,
when the mutation never applied, or when the control names no falsifier at all. Each is a test here.

Every case drives the real runner against a real throwaway project on disk, and asserts on its verdict -- never
on source text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

# Portability: prefer the INSTALLED package and fall back to this repository only when it is absent.
# The previous form was unconditional -- `ROOT = Path(__file__).resolve().parents[2]` followed by a sys.path
# insert -- which hardcodes "two directories above this file" as the project root. That is true only in this
# layout: installed from a wheel, `import control_assurance` needs no path surgery, and the insert would put
# an unrelated directory on sys.path. This suite builds every case in a tmp_path project, so with the import
# resolved it is portable as-is and needs no separate copy; duplicating it into tests/portable/ would create
# a second definition of fourteen tests, which is the defect class this package exists to surface.
try:                                                                    # installed (wheel, site-packages)
    from control_assurance.prove import main, prove_control
except ModuleNotFoundError:                                             # running from a source checkout
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from control_assurance.prove import main, prove_control  # noqa: E402

CONTROL_SRC = '''def enforce_max_position(size, cap):
    """Refuse a position above the cap."""
    if size > cap:
        raise ValueError("position above cap")
    return size
'''
GOOD_TEST = '''import pytest
from src.risk_limits import enforce_max_position

def test_cap_refuses():
    with pytest.raises(ValueError):
        enforce_max_position(150.0, 100.0)

def test_cap_allows():
    assert enforce_max_position(50.0, 100.0) == 50.0
'''
LAZY_TEST = '''from src.risk_limits import enforce_max_position

def test_it_imports():          # notices nothing: the classic vacuous test
    assert enforce_max_position(50.0, 100.0) == 50.0
'''
CAP_MUTATION = {"id": "cap_removed", "file": "src/risk_limits.py",
                "anchor": "    if size > cap:", "replacement": "    if False:"}


def project(tmp_path: Path, test_src: str, control: dict) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)     # the CLI test nests a second project inside the first
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "risk_limits.py").write_text(CONTROL_SRC, encoding="utf-8")
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_risk_limits.py").write_text(test_src, encoding="utf-8")
    reg = tmp_path / "registry.yml"
    reg.write_text(yaml.safe_dump({"version": 1, "controls": [control]}), encoding="utf-8")
    return reg, tmp_path


def base_control(**over) -> dict:
    c = {"name": "max_position_cap", "kind": "function", "asserts": "behaviour",
         "target": "src.risk_limits.enforce_max_position", "min_call_sites": 1, "status": "active",
         "falsified_by": "tests/test_risk_limits.py", "mutations": [dict(CAP_MUTATION)]}
    c.update(over)
    return c


def test_a_real_falsifier_proves_the_control(tmp_path):
    reg, root = project(tmp_path, GOOD_TEST, base_control())
    result = prove_control(root, base_control(), timeout=300)
    assert result["verdict"] == "PROVED"
    assert result["baseline"]["passes"] is True
    assert result["mutations"][0]["detected"] is True


def test_a_test_that_notices_nothing_is_not_a_proof(tmp_path):
    """The whole point: the control is broken and the suite still passes."""
    reg, root = project(tmp_path, LAZY_TEST, base_control())
    result = prove_control(root, base_control(), timeout=300)
    assert result["verdict"] == "NOT_PROVED"
    assert result["mutations"][0]["detected"] is False
    assert result["undetected"] == ["cap_removed"]


def test_a_falsifier_that_already_fails_proves_nothing(tmp_path):
    broken = GOOD_TEST + "\ndef test_unrelated_failure():\n    assert False\n"
    reg, root = project(tmp_path, broken, base_control())
    result = prove_control(root, base_control(), timeout=300)
    assert result["verdict"] == "BASELINE_FAILS"
    assert result["mutations"] == []


def test_an_anchor_that_does_not_apply_is_a_harness_failure_not_a_skip(tmp_path):
    stale = base_control(mutations=[{"id": "stale", "file": "src/risk_limits.py",
                                     "anchor": "if size >= cap:", "replacement": "if False:"}])
    reg, root = project(tmp_path, GOOD_TEST, stale)
    result = prove_control(root, stale, timeout=300)
    assert result["verdict"] == "NOT_PROVED"
    assert "HARNESS_FAILURE" in result["mutations"][0]
    assert result["mutations"][0]["detected"] is None


def test_an_anchor_matching_twice_is_also_a_harness_failure(tmp_path):
    ambiguous = base_control(mutations=[{"id": "ambiguous", "file": "src/risk_limits.py",
                                         "anchor": "size", "replacement": "size"}])
    reg, root = project(tmp_path, GOOD_TEST, ambiguous)
    result = prove_control(root, ambiguous, timeout=300)
    assert "occurs" in result["mutations"][0]["HARNESS_FAILURE"]


def test_a_mutation_that_breaks_syntax_is_a_harness_failure_not_a_detection(tmp_path):
    """THE FALSE-PROOF GUARD, measured on this repo's own registry 2026-09-21.

    A YAML plain scalar folded a three-line replacement into one line, so the planted code was invalid
    Python. The falsifier duly FAILED -- on an import error -- and `prove` recorded the control PROVED.
    A mutation must be valid code that behaves WRONGLY; code that does not parse tests the interpreter,
    not the control. Same standing as a stale anchor: never a silent pass.
    """
    broken = base_control(mutations=[{"id": "folded_replacement", "file": "src/risk_limits.py",
                                      "anchor": "    if size > cap:",
                                      "replacement": "    if size > cap: import os return False"}])
    reg, root = project(tmp_path, GOOD_TEST, broken)
    result = prove_control(root, broken, timeout=300)
    assert result["verdict"] == "NOT_PROVED"
    assert "syntactically invalid" in result["mutations"][0]["HARNESS_FAILURE"]
    assert result["mutations"][0]["detected"] is None


def test_a_multiline_replacement_that_parses_is_applied_normally(tmp_path):
    """The guard must not punish a LEGITIMATE multi-line mutation -- the fix for the fold is real newlines."""
    multi = base_control(mutations=[{"id": "multiline_ok", "file": "src/risk_limits.py",
                                     "anchor": "    if size > cap:",
                                     "replacement": "    if False:\n        pass\n    if False:"}])
    reg, root = project(tmp_path, GOOD_TEST, multi)
    result = prove_control(root, multi, timeout=300)
    assert result["mutations"][0].get("HARNESS_FAILURE") is None
    assert result["mutations"][0]["detected"] is True


def test_a_control_with_no_falsifier_cannot_be_proved(tmp_path):
    c = base_control(); c.pop("falsified_by")
    reg, root = project(tmp_path, GOOD_TEST, c)
    assert prove_control(root, c, timeout=300)["verdict"] == "NO_FALSIFIER"


def test_a_control_with_no_mutation_is_not_proved_either(tmp_path):
    c = base_control(mutations=[])
    reg, root = project(tmp_path, GOOD_TEST, c)
    assert prove_control(root, c, timeout=300)["verdict"] == "NO_MUTATIONS"


def test_the_source_tree_is_never_written_to(tmp_path):
    """The sandbox IS the safety property: an in-place harness in this repo corrupted line endings once."""
    reg, root = project(tmp_path, GOOD_TEST, base_control())
    before = (root / "src" / "risk_limits.py").read_bytes()
    prove_control(root, base_control(), timeout=300)
    assert (root / "src" / "risk_limits.py").read_bytes() == before


def test_cli_exit_codes_and_report(tmp_path):
    reg, root = project(tmp_path, GOOD_TEST, base_control())
    out = tmp_path / "proof.json"
    assert main(["--registry", str(reg), "--root", str(root), "--control", "max_position_cap",
                 "--json", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["VERDICT"] == "PROVED" and len(report["sha256"]) == 64

    reg2, root2 = project(tmp_path / "lazy", LAZY_TEST, base_control())
    assert main(["--registry", str(reg2), "--root", str(root2), "--control", "max_position_cap"]) == 1


def test_all_selects_only_controls_that_declare_mutations(tmp_path):
    reg, root = project(tmp_path, GOOD_TEST, base_control())
    doc = yaml.safe_load(reg.read_text(encoding="utf-8"))
    doc["controls"].append({"name": "no_mutations_here", "kind": "token", "asserts": "existence"})
    reg.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert main(["--registry", str(reg), "--root", str(root), "--all"]) == 0
