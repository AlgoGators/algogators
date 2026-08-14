"""HTTP tests for portfolio routes with use-case/repository dependencies stubbed.

Covers /portfolio/strategy/<id>, /portfolio/strategies, and the incubation
lifecycle POST routes (start/promote/retire): success responses, 404s,
validation errors, role enforcement, and 500 fallbacks.
"""

import pytest
from flask_jwt_extended import create_access_token, decode_token
from research_api.app import app
from research_api.application.portfolio.ports import IncubationError
from research_api.application.portfolio.use_cases import (
    StrategyDataNotFoundError,
    StrategyNotFoundError,
)


def _auth(client, role="admin"):
    """Set the JWT access cookie and return the CSRF token for POST requests."""
    claims = {} if role is None else {"role": role}
    with app.app_context():
        token = create_access_token(identity="1", additional_claims=claims)
        csrf = decode_token(token)["csrf"]
    client.set_cookie("access_token_cookie", token)
    return csrf


def _patch_deps(monkeypatch, registry=None, reader=None):
    import research_api.adapters.http.portfolio as portfolio_http

    monkeypatch.setattr(
        portfolio_http,
        "create_portfolio_dependencies",
        lambda: (registry if registry is not None else object(), reader),
    )


def _fake_use_case(monkeypatch, name, result=None, exc=None):
    """Replace a use-case class in the routes module with a canned fake."""
    import research_api.adapters.http.portfolio as portfolio_http

    class _UseCase:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, *args, **kwargs):
            if exc is not None:
                raise exc
            return result

    monkeypatch.setattr(portfolio_http, name, _UseCase)


class FakeRegistry:
    def __init__(self, configs):
        self.configs = configs

    def list(self, active_only=True):
        assert active_only is True
        return list(self.configs)

    def get(self, strategy_id):
        for cfg in self.configs:
            if cfg["id"] == strategy_id:
                return cfg
        return None


class SummaryReader:
    """Reader stub for ListStrategies; raises for ids listed in `broken`."""

    def __init__(self, rows_by_type, broken=()):
        self.rows_by_type = rows_by_type
        self.broken = set(broken)

    def fetch_summary_row(self, strategy_type, portfolio_id):
        if strategy_type in self.broken:
            raise RuntimeError("db exploded")
        return self.rows_by_type.get(strategy_type)


class RecordingReader:
    """Records incubation lifecycle calls; raises `exc` instead when set."""

    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def _record(self, name, kwargs):
        if self.exc is not None:
            raise self.exc
        self.calls.append((name, kwargs))

    def start_incubation(self, **kwargs):
        self._record("start_incubation", kwargs)

    def promote_to_live(self, **kwargs):
        self._record("promote_to_live", kwargs)

    def retire_strategy(self, **kwargs):
        self._record("retire_strategy", kwargs)


# ---------------------------------------------------------------------------
# GET /portfolio/strategy/<strategy_id>
# ---------------------------------------------------------------------------


def test_get_strategy_requires_auth(client):
    assert client.get("/portfolio/strategy/trendfollowing").status_code == 401


def test_get_strategy_returns_detail(client, monkeypatch):
    detail = {"id": "trendfollowing", "name": "Trend Following", "currentValue": 251000.0}
    _patch_deps(monkeypatch)
    _fake_use_case(monkeypatch, "GetStrategyDetail", result=detail)
    _auth(client)

    response = client.get("/portfolio/strategy/trendfollowing")

    assert response.status_code == 200
    assert response.get_json() == detail


def test_get_strategy_unknown_id_returns_404(client, monkeypatch):
    _patch_deps(monkeypatch)
    _fake_use_case(monkeypatch, "GetStrategyDetail", exc=StrategyNotFoundError("nope"))
    _auth(client)

    response = client.get("/portfolio/strategy/nope")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Strategy not found"}


def test_get_strategy_without_data_returns_404(client, monkeypatch):
    _patch_deps(monkeypatch)
    _fake_use_case(monkeypatch, "GetStrategyDetail", exc=StrategyDataNotFoundError("empty"))
    _auth(client)

    response = client.get("/portfolio/strategy/empty")

    assert response.status_code == 404
    assert response.get_json() == {"error": "No data found for strategy"}


def test_get_strategy_unexpected_error_returns_500(client, monkeypatch):
    _patch_deps(monkeypatch)
    _fake_use_case(monkeypatch, "GetStrategyDetail", exc=RuntimeError("boom"))
    _auth(client)

    response = client.get("/portfolio/strategy/trendfollowing")

    assert response.status_code == 500
    assert response.get_json() == {"error": "Failed to fetch strategy"}


# ---------------------------------------------------------------------------
# GET /portfolio/strategies
# ---------------------------------------------------------------------------


def test_get_all_strategies_returns_summaries(client, monkeypatch):
    cfg = {
        "id": "trendfollowing",
        "name": "Trend Following",
        "strategy_type": "LIVE_TREND_FOLLOWING",
        "portfolio_id": "MAIN",
        "initial_equity": 250000.0,
    }
    latest = {
        "current_portfolio_value": 275000.0,
        "volatility": 10.0,
        "total_annualized_return": 12.0,
    }
    registry = FakeRegistry([cfg])
    reader = SummaryReader({"LIVE_TREND_FOLLOWING": latest})
    _patch_deps(monkeypatch, registry=registry, reader=reader)
    _auth(client)

    response = client.get("/portfolio/strategies")

    assert response.status_code == 200
    data = response.get_json()
    assert len(data["strategies"]) == 1
    summary = data["strategies"][0]
    assert summary["id"] == "trendfollowing"
    assert summary["currentValue"] == 275000.0
    assert summary["returnPercent"] == pytest.approx(10.0)


def test_get_all_strategies_skips_broken_and_dataless_strategies(client, monkeypatch):
    good = {
        "id": "good",
        "name": "Good",
        "strategy_type": "GOOD",
        "portfolio_id": "P1",
        "initial_equity": 100.0,
    }
    broken = {**good, "id": "broken", "strategy_type": "BROKEN"}
    no_data = {**good, "id": "nodata", "strategy_type": "NODATA"}
    latest = {
        "current_portfolio_value": 110.0,
        "volatility": 5.0,
        "total_annualized_return": 7.0,
    }
    registry = FakeRegistry([good, broken, no_data])
    reader = SummaryReader({"GOOD": latest}, broken={"BROKEN"})
    _patch_deps(monkeypatch, registry=registry, reader=reader)
    _auth(client)

    response = client.get("/portfolio/strategies")

    assert response.status_code == 200
    data = response.get_json()
    assert [s["id"] for s in data["strategies"]] == ["good"]


def test_get_all_strategies_unexpected_error_returns_500(client, monkeypatch):
    _patch_deps(monkeypatch)
    _fake_use_case(monkeypatch, "ListStrategies", exc=RuntimeError("boom"))
    _auth(client)

    response = client.get("/portfolio/strategies")

    assert response.status_code == 500
    assert response.get_json() == {"error": "Failed to fetch strategies"}


# ---------------------------------------------------------------------------
# internal_only role enforcement
# ---------------------------------------------------------------------------


def test_incubation_list_refuses_external_role(client, monkeypatch):
    _patch_deps(monkeypatch)
    _auth(client, role="guest")

    response = client.get("/portfolio/incubation")

    assert response.status_code == 403
    assert response.get_json() == {"error": "Insufficient permissions"}


def test_incubation_list_refuses_token_without_role(client, monkeypatch):
    _patch_deps(monkeypatch)
    _auth(client, role=None)

    response = client.get("/portfolio/incubation")

    assert response.status_code == 403
    assert response.get_json() == {"error": "Insufficient permissions"}


def test_incubation_performance_refuses_external_role(client, monkeypatch):
    _patch_deps(monkeypatch)
    _auth(client, role="alumni")

    response = client.get("/portfolio/incubation/trendfollowing/performance")

    assert response.status_code == 403


def test_retire_refuses_external_role(client, monkeypatch):
    _patch_deps(monkeypatch)
    csrf = _auth(client, role="guest")

    response = client.post(
        "/portfolio/incubation/trendfollowing/retire",
        json={"reason": "done"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /portfolio/incubation and performance error paths
# ---------------------------------------------------------------------------


def test_incubation_list_unexpected_error_returns_500(client, monkeypatch):
    _patch_deps(monkeypatch)
    _fake_use_case(monkeypatch, "ListIncubatingStrategies", exc=RuntimeError("boom"))
    _auth(client)

    response = client.get("/portfolio/incubation")

    assert response.status_code == 500
    assert response.get_json() == {"error": "Failed to fetch incubating strategies"}


def test_incubation_performance_unexpected_error_returns_500(client, monkeypatch):
    _patch_deps(monkeypatch)
    _fake_use_case(monkeypatch, "GetIncubationPerformance", exc=RuntimeError("boom"))
    _auth(client)

    response = client.get("/portfolio/incubation/trendfollowing/performance")

    assert response.status_code == 500
    assert response.get_json() == {"error": "Failed to fetch incubation performance"}


# ---------------------------------------------------------------------------
# POST /portfolio/incubation/<id>/start
# ---------------------------------------------------------------------------


def test_start_incubation_rejects_missing_csrf_header(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json={"mock_capital": 250000, "reason": "trial"},
    )

    assert response.status_code == 401


def test_start_incubation_rejects_non_json_body(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        data="not json",
        content_type="application/json",
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object"}


def test_start_incubation_rejects_non_object_json_body(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json=[],
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object"}


def test_start_incubation_rejects_missing_mock_capital(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json={"reason": "trial"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Missing required field: mock_capital"}


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_start_incubation_rejects_missing_or_blank_reason(client, monkeypatch, reason):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)
    payload = {"mock_capital": 250000}
    if reason is not None:
        payload["reason"] = reason

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json=payload,
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Missing required field: reason"}


def test_start_incubation_rejects_non_numeric_mock_capital(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json={"mock_capital": "lots", "reason": "trial"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid mock_capital: must be a positive number"}


def test_start_incubation_rejects_non_positive_mock_capital(client, monkeypatch):
    reader = RecordingReader()
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json={"mock_capital": -5, "reason": "trial"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "mock_capital must be positive"}
    assert reader.calls == []


def test_start_incubation_success_records_call(client, monkeypatch):
    reader = RecordingReader()
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client, role="general_member")

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json={"mock_capital": "250000", "reason": "  mock trial  "},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 201
    assert response.get_json() == {"message": "Incubation started"}
    assert reader.calls == [
        (
            "start_incubation",
            {
                "strategy_id": "trendfollowing",
                "mock_capital": 250000.0,
                "reason": "mock trial",
                "user_id": "1",
            },
        )
    ]


def test_start_incubation_not_found_maps_to_404(client, monkeypatch):
    reader = RecordingReader(exc=IncubationError("Strategy not found"))
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/missing/start",
        json={"mock_capital": 1000, "reason": "trial"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "Strategy not found"}


def test_start_incubation_lifecycle_violation_maps_to_400(client, monkeypatch):
    reader = RecordingReader(exc=IncubationError("Strategy is already incubating"))
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json={"mock_capital": 1000, "reason": "trial"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Strategy is already incubating"}


def test_start_incubation_unexpected_error_returns_500(client, monkeypatch):
    reader = RecordingReader(exc=RuntimeError("db down"))
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/start",
        json={"mock_capital": 1000, "reason": "trial"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Failed to start incubation"}


# ---------------------------------------------------------------------------
# POST /portfolio/incubation/<id>/promote
# ---------------------------------------------------------------------------


def test_promote_rejects_non_json_body(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/promote",
        data="not json",
        content_type="application/json",
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object"}


def test_promote_rejects_blank_reason(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/promote",
        json={"reason": "   "},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Missing required field: reason"}


def test_promote_success_records_call(client, monkeypatch):
    reader = RecordingReader()
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/promote",
        json={"reason": "passed incubation"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Strategy promoted to live"}
    assert reader.calls == [
        (
            "promote_to_live",
            {
                "strategy_id": "trendfollowing",
                "reason": "passed incubation",
                "user_id": "1",
            },
        )
    ]


def test_promote_not_found_maps_to_404(client, monkeypatch):
    reader = RecordingReader(exc=IncubationError("No incubating strategy not found for id"))
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/missing/promote",
        json={"reason": "promote it"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 404


def test_promote_lifecycle_violation_maps_to_400(client, monkeypatch):
    reader = RecordingReader(exc=IncubationError("Strategy is not incubating"))
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/promote",
        json={"reason": "promote it"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Strategy is not incubating"}


def test_promote_unexpected_error_returns_500(client, monkeypatch):
    reader = RecordingReader(exc=RuntimeError("db down"))
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/promote",
        json={"reason": "promote it"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Failed to promote strategy"}


# ---------------------------------------------------------------------------
# POST /portfolio/incubation/<id>/retire
# ---------------------------------------------------------------------------


def test_retire_rejects_non_json_body(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/retire",
        data="not json",
        content_type="application/json",
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object"}


def test_retire_rejects_missing_reason(client, monkeypatch):
    _patch_deps(monkeypatch, reader=RecordingReader())
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/retire",
        json={},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Missing required field: reason"}


def test_retire_success_records_call(client, monkeypatch):
    reader = RecordingReader()
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/retire",
        json={"reason": "underperformed"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Strategy retired"}
    assert reader.calls == [
        (
            "retire_strategy",
            {
                "strategy_id": "trendfollowing",
                "reason": "underperformed",
                "user_id": "1",
            },
        )
    ]


def test_retire_not_found_maps_to_404(client, monkeypatch):
    reader = RecordingReader(exc=IncubationError("Strategy not found"))
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/missing/retire",
        json={"reason": "retire it"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "Strategy not found"}


def test_retire_unexpected_error_returns_500(client, monkeypatch):
    reader = RecordingReader(exc=RuntimeError("db down"))
    _patch_deps(monkeypatch, reader=reader)
    csrf = _auth(client)

    response = client.post(
        "/portfolio/incubation/trendfollowing/retire",
        json={"reason": "retire it"},
        headers={"X-CSRF-TOKEN": csrf},
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Failed to retire strategy"}
