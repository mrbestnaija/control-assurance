"""LAZY falsifier: it exercises the happy path only, so the control can be deleted and this still passes.

This is the single most common shape of a useless control test. It imports the function, calls it with an
input that was never going to be refused, and asserts the return value. Coverage counts the line. Nothing
here would notice if the refusal were removed entirely.
"""
from src.risk_limits import enforce_min_lot


def test_it_returns_the_size():
    assert enforce_min_lot(10.0, 1.0) == 10.0
