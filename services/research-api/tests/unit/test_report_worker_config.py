from research_api.infrastructure.reports.worker import portfolio_ids


def test_portfolio_ids_keep_their_case():
    # the registry holds QT_CONSERVATIVE_PORTFOLIO; lower-casing it made the worker skip the book
    assert portfolio_ids({"REPORT_PORTFOLIOS": "QT_CONSERVATIVE_PORTFOLIO"}) == (
        "QT_CONSERVATIVE_PORTFOLIO",
    )


def test_portfolio_ids_split_on_commas_and_semicolons_and_ignore_blanks():
    env = {"REPORT_PORTFOLIOS": " A_ONE ; B_TWO,, "}
    assert portfolio_ids(env) == ("A_ONE", "B_TWO")


def test_no_portfolios_when_unset():
    assert portfolio_ids({}) == ()
