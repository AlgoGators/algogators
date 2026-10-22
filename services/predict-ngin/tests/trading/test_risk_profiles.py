import pytest

from trading.risk_profiles import list_risk_profiles, get_risk_profile


def test_list_risk_profiles_sorted() -> None:
    profiles = list_risk_profiles()
    assert profiles == sorted(profiles)
    assert "default" in profiles


def test_get_risk_profile_returns_copy() -> None:
    profile = get_risk_profile("default")
    assert profile.name == "default"

    profile.limits.max_position_size = 1
    fresh = get_risk_profile("default")
    assert fresh.limits.max_position_size != 1


def test_get_risk_profile_unknown() -> None:
    with pytest.raises(ValueError):
        get_risk_profile("missing")
