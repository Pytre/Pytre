"""
Tests for Pytre/sql_query/loader.py
"""

import pytest
from pathlib import Path
from unittest.mock import Mock

from Pytre_Core.sql_query.loader import get_queries, filter_queries, orphan_queries, load_files, build_queries


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


# ================================================================================
# get_queries / filter_queries / orphan_queries Tests
# ================================================================================


@pytest.mark.unit
def test_get_queries_raises_for_nonexistent_folder(mock_app_ctx, mock_user_ctx):
    with pytest.raises(ValueError, match="n'a pas été trouvé"):
        get_queries(mock_app_ctx, mock_user_ctx, Path("/nonexistent/folder"))


@pytest.mark.unit
def test_get_queries_collects_and_sorts(mock_app_ctx, mock_user_ctx, tmp_path):
    for name, code in [("b", "BQUERY"), ("a", "AQUERY")]:
        (tmp_path / f"{name}.sql").write_text(f"/*\ncode: {code}\n*/\nSELECT 1")

    queries, errors = get_queries(mock_app_ctx, mock_user_ctx, tmp_path)

    assert errors == []
    assert [q.name for q in queries] == ["AQUERY", "BQUERY"]


@pytest.mark.unit
def test_get_queries_collects_errors_without_blocking(mock_app_ctx, mock_user_ctx, tmp_path):
    (tmp_path / "good.sql").write_text("/*\ncode: GOOD\n*/\nSELECT 1")
    (tmp_path / "bad.sql").write_text("/*\ncode: BAD\n*/\nDECLARE\n@!X as int = 1  -- Desc|ENTRY\n;\nSELECT 1")

    queries, errors = get_queries(mock_app_ctx, mock_user_ctx, tmp_path)

    assert len(queries) == 1
    assert queries[0].name == "GOOD"
    assert len(errors) == 1
    assert "bad.sql" in errors[0]


@pytest.mark.unit
def test_filter_queries_returns_empty_if_server_unknown(mock_app_ctx):
    servers_obj = Mock()
    servers_obj.servers_dict = {}
    user = Mock(admin=False, grp_authorized=["all"])

    result = filter_queries(servers_obj, [], "UNKNOWN", user)

    assert result == []


@pytest.mark.unit
def test_filter_queries_denies_user_without_matching_server_group():
    servers_obj = Mock()
    server = Mock(grp_authorized=["restricted"])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=False, grp_authorized=["all"])

    result = filter_queries(servers_obj, [], "SRV1", user)

    assert result == []


@pytest.mark.unit
def test_filter_queries_admin_bypasses_group_checks():
    servers_obj = Mock()
    server = Mock(grp_authorized=["restricted"])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=True, grp_authorized=["all"])

    query = Mock(servers_id=["SRV1"], grp_authorized=["restricted"], hide=0)

    result = filter_queries(servers_obj, [query], "SRV1", user)

    assert result == [query]


@pytest.mark.unit
def test_filter_queries_skips_query_not_for_server():
    servers_obj = Mock()
    server = Mock(grp_authorized=[])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=False, grp_authorized=["all"])

    query = Mock(servers_id=["OTHER_SERVER"], grp_authorized=[], hide=0)

    result = filter_queries(servers_obj, [query], "SRV1", user)

    assert result == []


@pytest.mark.unit
def test_filter_queries_respects_hide_level_for_non_admin():
    servers_obj = Mock()
    server = Mock(grp_authorized=[])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=False, grp_authorized=["all"])

    hidden_query = Mock(servers_id=["SRV1"], grp_authorized=[], hide=1)

    result = filter_queries(servers_obj, [hidden_query], "SRV1", user)

    assert result == []


@pytest.mark.unit
def test_filter_queries_admin_sees_hide_level_1_but_not_2():
    servers_obj = Mock()
    server = Mock(grp_authorized=[])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=True, grp_authorized=["all"])

    hide1 = Mock(servers_id=["SRV1"], grp_authorized=[], hide=1)
    hide2 = Mock(servers_id=["SRV1"], grp_authorized=[], hide=2)

    result = filter_queries(servers_obj, [hide1, hide2], "SRV1", user)

    assert result == [hide1]


@pytest.mark.unit
def test_orphan_queries_raises_for_nonexistent_folder(mock_app_ctx, mock_user_ctx):
    with pytest.raises(ValueError, match="n'a pas été trouvé"):
        orphan_queries(mock_app_ctx, mock_user_ctx, Path("/nonexistent/folder"))


@pytest.mark.unit
def test_orphan_queries_identifies_files_with_no_matching_server(mock_app_ctx, mock_user_ctx, tmp_path):
    (tmp_path / "orphan.sql").write_text("/*\ncode: ORPHAN\nservers: NONEXISTENT_SERVER\n*/\nSELECT 1")

    orphans = orphan_queries(mock_app_ctx, mock_user_ctx, tmp_path)

    assert len(orphans) == 1
    assert orphans[0].name == "orphan.sql"


@pytest.mark.unit
def test_orphan_queries_ignores_files_with_matching_server(mock_app_ctx, mock_user_ctx, tmp_path):
    mock_app_ctx.servers.servers_dict = {"srv1": Mock()}
    (tmp_path / "ok.sql").write_text("/*\ncode: OK\nservers: srv1\n*/\nSELECT 1")

    orphans = orphan_queries(mock_app_ctx, mock_user_ctx, tmp_path)

    assert orphans == []


@pytest.mark.unit
def test_orphan_queries_ignores_unexpected_errors(mock_app_ctx, mock_user_ctx, tmp_path):
    """A file that can't even be parsed as a query should be logged and skipped, not raised"""
    weird = tmp_path / "weird.sql"
    weird.mkdir()  # a directory named *.sql - IsADirectoryError when FileParser tries to read it

    orphans = orphan_queries(mock_app_ctx, mock_user_ctx, tmp_path)

    assert orphans == []


@pytest.mark.unit
def test_get_queries_collects_unexpected_errors(mock_app_ctx, mock_user_ctx, tmp_path):
    """A file that can't even be parsed as a query should end up in errors, not raised"""
    weird = tmp_path / "weird.sql"
    weird.mkdir()

    queries, errors = get_queries(mock_app_ctx, mock_user_ctx, tmp_path)

    assert queries == []
    assert len(errors) == 1
    assert "weird.sql" in errors[0]


# ================================================================================
# load_files / build_queries Tests
# ================================================================================


@pytest.mark.unit
def test_load_files_raises_for_nonexistent_folder():
    with pytest.raises(ValueError, match="n'a pas été trouvé"):
        load_files(Path("/nonexistent/folder"))


@pytest.mark.unit
def test_load_files_collects_parsers(tmp_path):
    (tmp_path / "a.sql").write_text("/*\ncode: AQUERY\n*/\nSELECT 1")
    (tmp_path / "b.sql").write_text("/*\ncode: BQUERY\n*/\nSELECT 2")

    parsers, errors = load_files(tmp_path)

    assert errors == []
    assert {p.filename.name for p in parsers} == {"a.sql", "b.sql"}


@pytest.mark.unit
def test_load_files_collects_errors_without_blocking(tmp_path):
    (tmp_path / "good.sql").write_text("/*\ncode: GOOD\n*/\nSELECT 1")
    weird = tmp_path / "weird.sql"
    weird.mkdir()  # a directory named *.sql - FileParser fails to read it

    parsers, errors = load_files(tmp_path)

    assert len(parsers) == 1
    assert parsers[0].filename.name == "good.sql"
    assert len(errors) == 1
    assert "weird.sql" in errors[0]


@pytest.mark.unit
def test_build_queries_collects_and_sorts(mock_app_ctx, mock_user_ctx, tmp_path):
    for name, code in [("b", "BQUERY"), ("a", "AQUERY")]:
        (tmp_path / f"{name}.sql").write_text(f"/*\ncode: {code}\n*/\nSELECT 1")
    parsers, _ = load_files(tmp_path)

    queries, errors = build_queries(mock_app_ctx, mock_user_ctx, parsers)

    assert errors == []
    assert [q.name for q in queries] == ["AQUERY", "BQUERY"]


@pytest.mark.unit
def test_build_queries_collects_errors_without_blocking(mock_app_ctx, mock_user_ctx, tmp_path):
    (tmp_path / "good.sql").write_text("/*\ncode: GOOD\n*/\nSELECT 1")
    (tmp_path / "bad.sql").write_text("/*\ncode: BAD\n*/\nDECLARE\n@!X as int = 1  -- Desc|ENTRY\n;\nSELECT 1")
    parsers, _ = load_files(tmp_path)

    queries, errors = build_queries(mock_app_ctx, mock_user_ctx, parsers)

    assert len(queries) == 1
    assert queries[0].name == "GOOD"
    assert len(errors) == 1
    assert "bad.sql" in errors[0]


@pytest.mark.unit
def test_build_queries_computes_fresh_values_per_user_ctx(mock_app_ctx, tmp_path):
    """Reusing the same cached FileParser list across different user_ctx must not leak
    parameter state between them - the whole point of splitting load_files (cacheable,
    disk I/O only) from build_queries (per-request, per-user construction)."""
    (tmp_path / "q.sql").write_text(
        "/*\ncode: Q\n*/\nDECLARE\n@USER as nvarchar(20) = '' -- Desc|USER_INFO(x3_id)\n;\nSELECT @USER"
    )
    parsers, load_errors = load_files(tmp_path)
    assert load_errors == []

    user_a = Mock(attribs_cust={"x3_id": "USER_A"})
    user_a.users.attribs_std = []
    user_ctx_a = Mock(current_user=user_a, user_prefs=Mock())

    user_b = Mock(attribs_cust={"x3_id": "USER_B"})
    user_b.users.attribs_std = []
    user_ctx_b = Mock(current_user=user_b, user_prefs=Mock())

    queries_a, _ = build_queries(mock_app_ctx, user_ctx_a, parsers)
    queries_b, _ = build_queries(mock_app_ctx, user_ctx_b, parsers)

    assert queries_a[0].parameters.params["@USER"].display_value == "USER_A"
    assert queries_b[0].parameters.params["@USER"].display_value == "USER_B"
