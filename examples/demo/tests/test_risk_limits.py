"""HONEST falsifier: it asserts the refusal actually happens, so breaking the control makes it fail."""
import pytest

from src.risk_limits import enforce_max_position


def test_cap_refuses_an_oversized_position():
    with pytest.raises(ValueError):
        enforce_max_position(150.0, 100.0)


def test_cap_allows_a_position_within_it():
    assert enforce_max_position(50.0, 100.0) == 50.0
