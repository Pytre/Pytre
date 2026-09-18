"""
Tests for Pytre_Server/routes/client.py's execute_query() helpers - the parts of /execute
testable without a full FastAPI TestClient round trip.
"""

from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from Pytre_Server.routes.client import ExecuteRequest, _apply_parameters


def _make_param(is_hidden: bool, display_value: str = "") -> Mock:
    param = Mock()
    param.is_hidden = is_hidden
    param.display_value = display_value
    return param


def _make_query(params: dict[str, Mock]) -> Mock:
    """Minimal stand-in for Query - _apply_parameters only touches .parameters.params and
    calls .set_display_values(...)."""
    query = Mock()
    query.parameters.params = params
    return query


def _make_request(parameters: dict[str, str]) -> ExecuteRequest:
    return ExecuteRequest(server_id="s", query_id="q", template_hash="h", parameters=parameters)


# ================================================================================
# _apply_parameters
# ================================================================================


@pytest.mark.unit
def test_apply_parameters_rejects_name_mismatch():
    query = _make_query({"@a": _make_param(is_hidden=False)})
    request = _make_request({"@b": "1"})

    with pytest.raises(HTTPException) as exc_info:
        _apply_parameters(query, request, is_admin=False)

    assert exc_info.value.status_code == 409


@pytest.mark.unit
def test_apply_parameters_rejects_missing_param():
    query = _make_query({"@a": _make_param(is_hidden=False), "@b": _make_param(is_hidden=False)})
    request = _make_request({"@a": "1"})

    with pytest.raises(HTTPException, match="missing.*@b"):
        _apply_parameters(query, request, is_admin=False)


@pytest.mark.unit
def test_apply_parameters_non_admin_cannot_override_hidden_param():
    """The core security fix: a non-admin's submitted value for a hidden parameter (e.g. @USER,
    used for row-level access control) is replaced with the server-computed default before
    being applied - regression for the privilege-escalation gap found in remotes.py review."""
    query = _make_query({"@USER": _make_param(is_hidden=True, display_value="real_user")})
    request = _make_request({"@USER": "someone_else"})

    _apply_parameters(query, request, is_admin=False)

    assert request.parameters["@USER"] == "real_user"
    query.set_display_values.assert_called_once_with({"@USER": "real_user"})


@pytest.mark.unit
def test_apply_parameters_admin_can_override_hidden_param():
    query = _make_query({"@USER": _make_param(is_hidden=True, display_value="real_user")})
    request = _make_request({"@USER": "someone_else"})

    _apply_parameters(query, request, is_admin=True)

    assert request.parameters["@USER"] == "someone_else"
    query.set_display_values.assert_called_once_with({"@USER": "someone_else"})


@pytest.mark.unit
def test_apply_parameters_leaves_non_hidden_param_untouched_for_non_admin():
    query = _make_query({"@filter": _make_param(is_hidden=False)})
    request = _make_request({"@filter": "2024-01-01"})

    _apply_parameters(query, request, is_admin=False)

    assert request.parameters["@filter"] == "2024-01-01"


@pytest.mark.unit
def test_apply_parameters_mix_of_hidden_and_visible_params_for_non_admin():
    query = _make_query(
        {
            "@USER": _make_param(is_hidden=True, display_value="real_user"),
            "@filter": _make_param(is_hidden=False),
        }
    )
    request = _make_request({"@USER": "someone_else", "@filter": "2024-01-01"})

    _apply_parameters(query, request, is_admin=False)

    assert request.parameters == {"@USER": "real_user", "@filter": "2024-01-01"}


@pytest.mark.unit
def test_apply_parameters_raises_400_on_invalid_value():
    query = _make_query({"@a": _make_param(is_hidden=False)})
    query.set_display_values.side_effect = ValueError("bad value")
    request = _make_request({"@a": "x"})

    with pytest.raises(HTTPException) as exc_info:
        _apply_parameters(query, request, is_admin=False)

    assert exc_info.value.status_code == 400
    assert "bad value" in exc_info.value.detail
