"""
Tests for Pytre_Server/execution/params.py
"""

import pytest

from Pytre_Server.execution.template_converter import to_positional_style


@pytest.mark.unit
def test_single_param():
    template, params = to_positional_style("SELECT * FROM t WHERE id = %(@ID)s", {"@ID": 42})

    assert template == "SELECT * FROM t WHERE id = @P1"
    assert params == [42]


@pytest.mark.unit
def test_multiple_distinct_params_keep_first_appearance_order():
    template, params = to_positional_style(
        "SELECT * FROM t WHERE name = %(@Name)s AND id = %(@ID)s",
        {"@ID": 42, "@Name": "foo"},
    )

    assert template == "SELECT * FROM t WHERE name = @P1 AND id = @P2"
    assert params == ["foo", 42]


@pytest.mark.unit
def test_repeated_param_reuses_same_positional_index():
    template, params = to_positional_style(
        "SELECT * FROM t WHERE a = %(@ID)s OR b = %(@ID)s",
        {"@ID": 7},
    )

    assert template == "SELECT * FROM t WHERE a = @P1 OR b = @P1"
    assert params == [7]


@pytest.mark.unit
def test_no_params():
    template, params = to_positional_style("SELECT * FROM t", {})

    assert template == "SELECT * FROM t"
    assert params == []


@pytest.mark.unit
def test_ignores_unused_dict_entries():
    template, params = to_positional_style("SELECT * FROM t WHERE id = %(@ID)s", {"@ID": 1, "@Unused": 99})

    assert template == "SELECT * FROM t WHERE id = @P1"
    assert params == [1]
