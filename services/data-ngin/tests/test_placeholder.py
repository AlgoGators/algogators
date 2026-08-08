from data_ngin import service_name


def test_service_name() -> None:
    assert service_name() == "data-ngin"
