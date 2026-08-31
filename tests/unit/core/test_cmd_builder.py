"""
Tests for Pytre/sql_query/cmd_builder.py
"""

import pytest
from pathlib import Path
from unittest.mock import Mock

from Pytre_Core.sql_query.cmd_builder import CmdBuilder
from Pytre_Core.sql_query.parameters import SqlParameters


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def mock_settings():
    """Mock Settings providing valid defaults for SqlParam/CmdBuilder/QueryExecute"""
    mock = Mock()
    mock.date_format = "%d/%m/%Y"
    mock.decimal_separator = ","
    mock.field_separator = ";"
    mock.extract_encoding = "utf-8-sig"
    return mock


@pytest.fixture
def mock_user():
    """Mock current user for SqlParam/SqlParameters"""
    mock = Mock()
    return mock


@pytest.fixture
def mock_user_prefs():
    """Mock UserPrefs for CmdBuilder"""
    return Mock()


# ================================================================================
# CmdBuilder Tests
# ================================================================================


@pytest.mark.unit
def test_cmd_builder_initialization(mock_settings, mock_user, mock_user_prefs):
    """CmdBuilder should initialize with raw command and parameters"""
    raw_cmd = "SELECT @param1, @param2"
    params = SqlParameters(
        mock_settings, mock_user, "DECLARE @param1 as int = 10\nDECLARE @param2 as varchar(50) = 'test'"
    )

    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    assert builder.raw_cmd == raw_cmd
    assert builder.parameters is params


@pytest.mark.unit
def test_cmd_builder_build_command(mock_settings, mock_user, mock_user_prefs):
    """CmdBuilder should build command with parameter substitution"""
    raw_cmd = "SELECT @param1 as val1, @param2 as val2"
    sql_content = """
DECLARE
@param1 as int = 10,  -- Param 1
@param2 as varchar(50) = 'test';  -- Param 2

SELECT @param1, @param2
"""
    params = SqlParameters(mock_settings, mock_user, sql_content)

    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)
    cmd_template, cmd_parameters = builder.build_command()

    # Template should use %(var)s format
    assert "%(@param1)s" in cmd_template
    assert "%(@param2)s" in cmd_template

    # Parameters should be in dict
    assert "@param1" in cmd_parameters
    assert "@param2" in cmd_parameters
    assert cmd_parameters["@param1"] == 10
    assert cmd_parameters["@param2"] == "test"


@pytest.mark.unit
def test_cmd_builder_build_debug_command(mock_settings, mock_user, mock_user_prefs):
    """CmdBuilder should build debug command with replaced values"""
    raw_cmd = "SELECT @param1, @param2"
    sql_content = """
DECLARE
@param1 as int = 42,  -- Param 1
@param2 as varchar(50) = 'hello';  -- Param 2

SELECT @param1, @param2
"""
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    debug_cmd = builder.build_debug_command()

    # Should replace parameters with actual values
    assert "@param1" not in debug_cmd
    assert "@param2" not in debug_cmd
    assert "42" in debug_cmd
    assert "'hello'" in debug_cmd


@pytest.mark.unit
def test_cmd_builder_build_debug_with_numeric_param(mock_settings, mock_user, mock_user_prefs):
    """CmdBuilder should handle numeric parameters in debug command"""
    raw_cmd = "SELECT @num, @text"
    sql_content = """
DECLARE
@num as int = 123,
@text as varchar(50) = 'test value';  -- Text

SELECT @num, @text
"""
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    debug_cmd = builder.build_debug_command()

    # Should replace both parameters
    assert "123" in debug_cmd
    assert "'test value'" in debug_cmd or "test value" in debug_cmd


# ================================================================================
# CmdBuilder.build_exec_config Tests
# ================================================================================


@pytest.mark.unit
def test_cmd_builder_build_exec_config_with_file_output(mock_settings, mock_user, mock_user_prefs, tmp_path):
    from Pytre_Core.user_prefs import PrefsEnum

    mock_user_prefs.get = Mock(return_value=tmp_path)
    mock_settings.extract_encoding = "utf-8-sig"

    sql_content = "DECLARE\n@p as int = 10  -- Param\n;\nSELECT @p"
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, "SELECT @p", params)

    config = builder.build_exec_local_cfg("SRV1", "MY_QUERY", file_output=True)

    assert config.query_id == "MY_QUERY"
    assert config.server_id == "SRV1"
    assert config.extract_file.parent == tmp_path
    assert config.extract_file.name.startswith("MY_QUERY_")
    assert config.extract_encoding == "utf-8-sig"
    assert "@p" in config.params_metadata


@pytest.mark.unit
def test_cmd_builder_build_exec_config_without_file_output(mock_settings, mock_user, mock_user_prefs):
    sql_content = "DECLARE\n@p as int = 10  -- Param\n;\nSELECT @p"
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, "SELECT @p", params)

    config = builder.build_exec_local_cfg("SRV1", "MY_QUERY", file_output=False)

    assert config.extract_file == Path()


# ================================================================================
# CmdBuilder inlined ("@!") parameter Tests
# ================================================================================


@pytest.mark.unit
def test_cmd_builder_build_command_replaces_inlined_param_directly(mock_settings, mock_user, mock_user_prefs):
    """Inlined (@!) params should be substituted directly into cmd_template, not passed as a bind parameter"""
    raw_cmd = "SELECT * FROM @!TABLE WHERE id = @id"
    sql_content = """DECLARE
@!TABLE as int = 1,  -- Table|LIST(1: Orders, 2: Invoices)
@id as int = 5  -- Id
;
SELECT * FROM @!TABLE WHERE id = @id"""
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    cmd_template, cmd_parameters = builder.build_command()

    assert "@!TABLE" not in cmd_template
    assert "1" in cmd_template
    assert "@!TABLE" not in cmd_parameters
    assert cmd_parameters["@id"] == 5


@pytest.mark.unit
def test_cmd_builder_build_debug_command_replaces_inlined_param_directly(mock_settings, mock_user, mock_user_prefs):
    """build_debug_command should also substitute inlined params directly, unquoted"""
    raw_cmd = "SELECT * FROM @!TABLE"
    sql_content = "DECLARE\n@!TABLE as int = 1  -- Table|LIST(1: Orders, 2: Invoices)\n;\nSELECT * FROM @!TABLE"
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    debug_cmd = builder.build_debug_command()

    assert "@!TABLE" not in debug_cmd
    assert "1" in debug_cmd


# ================================================================================
# CmdBuilder.params_pos_for_debug Tests
# ================================================================================


@pytest.mark.unit
def test_params_pos_for_debug_maps_string_param_position(mock_settings, mock_user, mock_user_prefs):
    """params_pos_for_debug should report the position/length of a quoted string param after substitution"""
    raw_cmd = "SELECT @name"
    sql_content = "DECLARE\n@name as varchar(50) = 'bob'  -- Name\n;\nSELECT @name"
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    positions = builder.params_pos_for_debug()

    # "@name" starts at index 7 in raw_cmd, replaced by the quoted string "'bob'" (5 chars)
    start = raw_cmd.index("@name")
    assert positions[start] == len("'bob'")


@pytest.mark.unit
def test_params_pos_for_debug_skips_unknown_variables(mock_settings, mock_user, mock_user_prefs):
    """params_pos_for_debug should ignore @-prefixed tokens that aren't declared parameters"""
    raw_cmd = "SELECT @unknown, @name"
    sql_content = "DECLARE\n@name as int = 5  -- Name\n;\nSELECT @unknown, @name"
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    positions = builder.params_pos_for_debug()

    assert len(positions) == 1
    start = raw_cmd.index("@name")
    assert positions[start] == len("5")


@pytest.mark.unit
def test_params_pos_for_debug_only_inlined_filters_regular_params(mock_settings, mock_user, mock_user_prefs):
    """only_inlined=True should skip regular (non @!) parameters entirely"""
    raw_cmd = "SELECT @!TABLE, @id"
    sql_content = """DECLARE
@!TABLE as int = 1,  -- Table|LIST(1: Orders, 2: Invoices)
@id as int = 5  -- Id
;
SELECT @!TABLE, @id"""
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    positions = builder.params_pos_for_debug(only_inlined=True)

    start = raw_cmd.index("@!TABLE")
    assert list(positions.keys()) == [start]
    assert positions[start] == len("1")  # inlined value is not quoted


@pytest.mark.unit
def test_params_pos_for_debug_accumulates_offset_across_params(mock_settings, mock_user, mock_user_prefs):
    """Later params' reported position should account for the length delta introduced by earlier substitutions"""
    raw_cmd = "SELECT @first, @second"
    sql_content = """DECLARE
@first as varchar(50) = 'aaaaaaaaaa',  -- First
@second as int = 7  -- Second
;
SELECT @first, @second"""
    params = SqlParameters(mock_settings, mock_user, sql_content)
    builder = CmdBuilder(mock_settings, mock_user_prefs, raw_cmd, params)

    positions = builder.params_pos_for_debug()

    assert len(positions) == 2
    # @second's reported start should be shifted by the extra length @first's substitution added
    first_start = raw_cmd.index("@first")
    first_len = len("'aaaaaaaaaa'")
    expected_second_start = raw_cmd.index("@second") + (first_len - len("@first"))
    assert expected_second_start in positions
    assert positions[expected_second_start] == len("7")
