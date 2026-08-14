"""
Tests for Pytre/sql_query/query.py
"""

import pytest
from unittest.mock import Mock

from Pytre_Core.remotes import Remote, RemoteServer
from Pytre_Core.sql_query.query import Query
from Pytre_Core.remotes import RemoteAddress


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
# Query.remote - serialize / deserialize
# ================================================================================


@pytest.fixture
def remote_server() -> RemoteServer:
    address = RemoteAddress(url="http://example.com", port=8000, username="alice")
    return RemoteServer(id="SRV1", description="Remote Server 1", remote=Remote(None, address))


@pytest.mark.unit
def test_query_serialize_includes_remote_when_set(mock_app_ctx, mock_user_ctx, sample_sql_file, remote_server):
    query = Query(mock_app_ctx, mock_user_ctx, sample_sql_file)
    query.remote = remote_server

    data = query.serialize()

    assert data["remote"] == remote_server.serialize()


@pytest.mark.unit
def test_query_serialize_remote_is_none_when_local(mock_app_ctx, mock_user_ctx, sample_sql_file):
    query = Query(mock_app_ctx, mock_user_ctx, sample_sql_file)

    data = query.serialize()

    assert data["remote"] is None


@pytest.mark.unit
def test_query_deserialize_restores_remote_from_data(mock_app_ctx, mock_user_ctx, sample_sql_file, remote_server):
    """The 'execution round-trip' use case (multiprocessing queue): the Query being
    serialized already has .remote set, so data["remote"] carries it correctly."""
    query1 = Query(mock_app_ctx, mock_user_ctx, sample_sql_file)
    query1.remote = remote_server
    data = query1.serialize()

    query2 = Query.deserialize(mock_app_ctx, mock_user_ctx, data)

    assert query2.remote is not None
    assert query2.remote.id_on_remote == remote_server.id_on_remote
    # description itself isn't compared: from_dict() always fills a blank description with
    # a "host:port" fallback by design (see RemoteAddress), so it won't match the original "".
    assert query2.remote.address.url == remote_server.address.url
    assert query2.remote.address.port == remote_server.address.port
    assert query2.remote.address.username == remote_server.address.username


@pytest.mark.unit
def test_query_deserialize_uses_explicit_remote_override(mock_app_ctx, mock_user_ctx, sample_sql_file, remote_server):
    """The 'display' use case: data['remote'] is None (Pytre_Server never sets it on its
    own Query objects), but the caller - who just fetched this query from that remote -
    passes it explicitly instead."""
    query1 = Query(mock_app_ctx, mock_user_ctx, sample_sql_file)
    data = query1.serialize()
    assert data["remote"] is None  # confirms this really is the "server never set it" case

    query2 = Query.deserialize(mock_app_ctx, mock_user_ctx, data, remote=remote_server)

    assert query2.remote is remote_server


@pytest.mark.unit
def test_query_deserialize_without_remote_stays_local(mock_app_ctx, mock_user_ctx, sample_sql_file):
    query1 = Query(mock_app_ctx, mock_user_ctx, sample_sql_file)
    data = query1.serialize()

    query2 = Query.deserialize(mock_app_ctx, mock_user_ctx, data)

    assert query2.remote is None


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
def test_query_set_display_values_updates_and_validates(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query.set_display_values should set display values and update computed values"""
    sql_file = tmp_path / "test.sql"
    content = """DECLARE
@param as int = 10  -- Parameter
;

SELECT @param
"""
    sql_file.write_text(content)

    query = Query(mock_app_ctx, mock_user_ctx, sql_file)
    query.set_display_values({"@param": "20"})

    assert query.parameters.params["@param"].display_value == "20"
    assert query.parameters.params["@param"].value_cmd == 20
    assert query.values_ok() is True


@pytest.mark.unit
def test_query_set_display_values_raises_keyerror_for_unknown_param(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query.set_display_values should raise KeyError for a param that does not exist"""
    sql_file = tmp_path / "test.sql"
    content = """DECLARE
@param as int = 10  -- Parameter
;

SELECT @param
"""
    sql_file.write_text(content)

    query = Query(mock_app_ctx, mock_user_ctx, sql_file)

    with pytest.raises(KeyError):
        query.set_display_values({"@unknown": "1"})


@pytest.mark.unit
def test_query_set_display_values_raises_valueerror_for_invalid_value(mock_app_ctx, mock_user_ctx, tmp_path):
    """Query.set_display_values should raise ValueError for an invalid mandatory value"""
    sql_file = tmp_path / "test.sql"
    content = """DECLARE
@param as nvarchar(50) = 'test'  -- Mandatory parameter
;

SELECT @param
"""
    sql_file.write_text(content)

    query = Query(mock_app_ctx, mock_user_ctx, sql_file)

    with pytest.raises(ValueError, match="obligatoire"):
        query.set_display_values({"@param": ""})


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
    assert config.query_id == sql_file.stem
    assert isinstance(config.cmd_template, str)