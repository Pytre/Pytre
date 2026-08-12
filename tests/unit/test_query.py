"""
Tests for Pytre/sql_query/query.py
"""

import pytest
from unittest.mock import Mock, patch

from Pytre_Core.sql_query.query import Query


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
    """Mock current user for SqlParam/SqlParameters (used by the user_info() dynamic function)"""
    mock = Mock()
    mock.users = Mock()
    mock.users.attribs_std = []
    mock.attribs_cust = {}
    return mock


@pytest.fixture
def mock_user_prefs():
    """Mock UserPrefs for CmdBuilder"""
    return Mock()


@pytest.fixture
def mock_app_ctx(mock_settings):
    """Mock AppContext for Query/QueryExecute"""
    ctx = Mock()
    ctx.settings = mock_settings
    ctx.servers = Mock()
    ctx.servers.servers_dict = {}
    return ctx


@pytest.fixture
def mock_user_ctx(mock_user, mock_user_prefs):
    """Mock UserContext for Query/QueryExecute"""
    ctx = Mock()
    ctx.current_user = mock_user
    ctx.user_prefs = mock_user_prefs
    return ctx


@pytest.fixture
def sample_sql_file(tmp_path):
    """Create a sample SQL query file"""
    sql_file = tmp_path / "test_query.sql"
    content = """/* Infos requête
Code : TEST_QUERY
Description : Test Query for Unit Tests
Hide : 0
Servers : TEST_SERVER
*/

DECLARE
@param1 as int = 100,        -- Integer parameter
@param2 as nvarchar(50) = 'test',  -- String parameter
@param3 as date = '2025-01-01'     -- Date parameter
;

SELECT
    col1,
    col2
FROM test_table
WHERE id = @param1
    AND name = @param2
    AND date_field = @param3
"""
    sql_file.write_text(content, encoding="utf-8")
    return sql_file


# ================================================================================
# Query Class Tests (Basic Initialization)
# ================================================================================


@pytest.mark.unit
def test_query_initialization(mock_app_ctx, mock_user_ctx, sample_sql_file):
    """Query should initialize from SQL file"""
    query = Query(mock_app_ctx, mock_user_ctx, sample_sql_file)

    assert query.filename == sample_sql_file
    assert query.name == "TEST_QUERY"
    assert query.description == "Test Query for Unit Tests"
    assert query.hide == 0


@pytest.mark.unit
def test_query_hide_level(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query should parse hide level correctly"""
    sql_file = tmp_path / "hidden.sql"
    content = """/* Infos requête
Code : HIDDEN
Hide : 2
*/
SELECT * FROM table"""
    sql_file.write_text(content, encoding="utf-8")

    query = Query(mock_app_ctx, mock_user_ctx, sql_file)

    assert query.hide == 2
    assert "(**)" in query.description  # Stars in description


@pytest.mark.unit
def test_query_serialize_deserialize(mock_app_ctx, mock_user_ctx, sample_sql_file):
    """Query should serialize and deserialize correctly"""
    query1 = Query(mock_app_ctx, mock_user_ctx, sample_sql_file)

    # Serialize
    data = query1.serialize()

    # Deserialize
    query2 = Query.deserialize(mock_app_ctx, mock_user_ctx, data)

    assert query2.name == query1.name
    assert query2.description == query1.description
    assert query2.filename == query1.filename


# ================================================================================
# Query Class Tests
# ================================================================================


@pytest.mark.unit
def test_query_initialization_with_file(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query should initialize from SQL file"""
    sql_file = tmp_path / "test_query.sql"
    content = """
/*
code : TEST_QUERY
description : Test query description
*/

DECLARE @param as int = 10  -- Test param

SELECT @param as value
"""
    sql_file.write_text(content)

    query = Query(mock_app_ctx, mock_user_ctx, source=sql_file)

    assert query.filename == sql_file
    assert query.name == "TEST_QUERY"
    assert query.description == "Test query description"
    assert query.file_content == content


@pytest.mark.unit
def test_query_set_hide_level_valid():
    """Query should parse hide level from infos"""
    query = Query.__new__(Query)
    query.infos = {"hide": "2"}

    result = query._set_hide_level()

    assert result == 2


@pytest.mark.unit
def test_query_set_hide_level_invalid():
    """Query should default to 0 for invalid hide level"""
    query = Query.__new__(Query)
    query.infos = {"hide": "invalid"}

    result = query._set_hide_level()

    assert result == 0


@pytest.mark.unit
def test_query_set_hide_level_missing():
    """Query should default to 0 for missing hide level"""
    query = Query.__new__(Query)
    query.infos = {}

    result = query._set_hide_level()

    assert result == 0


@pytest.mark.unit
def test_query_set_description_no_hide():
    """Query should return description without hide prefix"""
    query = Query.__new__(Query)
    query.infos = {"description": "Test description"}
    query.hide = 0

    result = query._set_description()

    assert result == "Test description"


@pytest.mark.unit
def test_query_set_description_with_hide():
    """Query should prepend asterisks to description when hide level set"""
    query = Query.__new__(Query)
    query.infos = {"description": "Test description"}
    query.hide = 3

    result = query._set_description()

    assert result == "(***) Test description"


# ================================================================================
# Query Wrapper Methods Tests
# ================================================================================


@pytest.mark.unit
def test_query_get_cmd_for_debug(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query should return debug command"""
    sql_file = tmp_path / "test.sql"
    content = """/*
code: TEST
*/

DECLARE
@param as int = 42  -- Parameter
;

SELECT @param
"""
    sql_file.write_text(content)

    query = Query(mock_app_ctx, mock_user_ctx, sql_file)
    result = query.get_cmd_for_debug()

    assert isinstance(result, str)
    assert "SELECT" in result
    assert "42" in result


@pytest.mark.unit
def test_query_update_values(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query should update parameter values"""
    sql_file = tmp_path / "test.sql"
    content = """DECLARE
@param as int = 10  -- Parameter
;

SELECT @param
"""
    sql_file.write_text(content)

    query = Query(mock_app_ctx, mock_user_ctx, sql_file)

    # Change parameter value
    query.parameters.params["@param"].display_value = "20"

    # Update
    result = query.update_values()

    assert result is True
    assert query.parameters.params["@param"].value_cmd == 20


@pytest.mark.unit
def test_query_values_ok(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query should validate parameter values"""
    sql_file = tmp_path / "test.sql"
    content = """DECLARE
@param as int = 10  -- Parameter
;

SELECT @param
"""
    sql_file.write_text(content)

    query = Query(mock_app_ctx, mock_user_ctx, sql_file)

    # Validation should pass with default values
    result = query.values_ok()

    assert result is True


@pytest.mark.unit
def test_query_get_exec_config_with_server_id(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query should generate execution config with specified server"""
    sql_file = tmp_path / "test.sql"
    content = """DECLARE
@param as int = 10  -- Parameter
;

SELECT @param
"""
    sql_file.write_text(content)

    query = Query(mock_app_ctx, mock_user_ctx, sql_file)
    config = query.get_exec_config(server_id="TEST_SERVER", file_output=False)

    assert config.server_id == "TEST_SERVER"
    assert config.query_name == sql_file.stem
    assert isinstance(config.cmd_template, str)


# ================================================================================
# Query.execute() Tests
# ================================================================================


@pytest.mark.unit
def test_query_execute_returns_error_if_values_invalid(mock_app_ctx, mock_user_ctx, tmp_path):
    sql_file = tmp_path / "test.sql"
    sql_file.write_text("SELECT 1")
    query = Query(mock_app_ctx, mock_user_ctx, sql_file)
    query.query_execute.queue = Mock()

    with patch.object(query, "update_values"), patch.object(query, "values_ok", return_value=False):
        result = query.execute(file_output=False)

    assert result == (-1, None)
    assert query.query_execute.queue.put.called


@pytest.mark.unit
def test_query_execute_delegates_to_query_execute(mock_app_ctx, mock_user_ctx, tmp_path):
    sql_file = tmp_path / "test.sql"
    sql_file.write_text("SELECT 1")
    query = Query(mock_app_ctx, mock_user_ctx, sql_file)
    query.query_execute.execute = Mock(return_value=(3, tmp_path / "out.csv"))

    result = query.execute(server_id="SRV1", file_output=False)

    assert result == (3, tmp_path / "out.csv")


@pytest.mark.unit
def test_query_execute_handles_operational_error(mock_app_ctx, mock_user_ctx, tmp_path):
    from Pytre_Core.servers import ServerConnectionError

    sql_file = tmp_path / "test.sql"
    sql_file.write_text("SELECT 1")
    query = Query(mock_app_ctx, mock_user_ctx, sql_file)
    query.query_execute.execute = Mock(side_effect=ServerConnectionError("connection failed"))
    query.query_execute.queue = Mock()

    result = query.execute(file_output=False)

    assert result == (-1, None)
    assert query.query_execute.queue.put.called


@pytest.mark.unit
def test_query_execute_handles_unexpected_error(mock_app_ctx, mock_user_ctx, tmp_path):
    sql_file = tmp_path / "test.sql"
    sql_file.write_text("SELECT 1")
    query = Query(mock_app_ctx, mock_user_ctx, sql_file)
    query.query_execute.execute = Mock(side_effect=ValueError("boom"))
    query.query_execute.queue = Mock()

    result = query.execute(file_output=False)

    assert result == (-1, None)
    query.query_execute.queue.put.assert_any_call(("msg_output", "Erreur inattendue : boom"))
