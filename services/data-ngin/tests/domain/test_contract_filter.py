from data_ngin.domain.contract_filter import (
    is_outright_contract,
    parse_contract_symbol,
)


def test_filter_outrights_drops_calendar_spreads():
    # Outright futures
    assert is_outright_contract("ESZ5") is True
    assert is_outright_contract("CLV5") is True
    assert is_outright_contract("ZBU4") is True
    assert is_outright_contract("NQH6") is True
    assert is_outright_contract("6JU0") is True

    # Calendar spreads and inter-commodity spreads
    assert is_outright_contract("ES:FUT-202512") is False
    assert is_outright_contract("GE-GE") is False
    assert is_outright_contract("CLZ5-CLF6") is False
    assert is_outright_contract("6JU0-6JM0") is False
    assert is_outright_contract("HON0-HOQ0") is False
    assert is_outright_contract("ES:FUT_SPREAD") is False


def test_filter_outrights_checks_instrument_class():
    assert is_outright_contract("ESZ5", instrument_class="F") is True
    assert is_outright_contract("ESZ5", instrument_class="S") is False
    assert is_outright_contract("ESZ5", instrument_class="O") is False


def test_parse_contract_symbol():
    root, month_code, year = parse_contract_symbol("ESZ24")
    assert root == "ES"
    assert month_code == "Z"
    assert year == "24"

    root2, month_code2, year2 = parse_contract_symbol("CLV5")
    assert root2 == "CL"
    assert month_code2 == "V"
    assert year2 == "5"
