from research_core import package_name


def test_package_name() -> None:
    assert package_name() == "research-core"
