from research_api import app_name


def test_app_name() -> None:
    assert app_name() == "research-api"
