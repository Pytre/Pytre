"""
Tests for Pytre/sql_query/loader.py
"""

import pytest
from pathlib import Path
from typing import cast
from unittest.mock import Mock

from Pytre_Core.context import UserContext
from Pytre_Core.remotes import Remote
from Pytre_Core.remotes import RemoteServer
from Pytre_Core.remotes import RemoteAddress
from Pytre_Core.user_prefs import DisplayFormat
from Pytre_Core.sql_query.loader import (
    get_queries,
    filter_queries,
    orphan_queries,
    load_files,
    build_queries_from_parsers,
    build_queries_from_remote,
)


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def mock_settings():
    """Mock Settings for AppContext"""
    return Mock()


@pytest.fixture
def mock_user():
    """Mock current user for SqlParam/SqlParameters"""
    mock = Mock()
    return mock


@pytest.fixture
def mock_user_prefs():
    """Mock UserPrefs for CmdBuilder and UserContext.display_format"""
    prefs = Mock()
    prefs.display_format = DisplayFormat()
    return prefs


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
    ctx.display_format = mock_user_prefs.display_format
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
    user = Mock(admin=False, groups=["all"])

    result = filter_queries(servers_obj, [], "UNKNOWN", user)

    assert result == []


@pytest.mark.unit
def test_filter_queries_denies_user_without_matching_server_group():
    servers_obj = Mock()
    server = Mock(grp_authorized=["restricted"])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=False, groups=["all"])

    result = filter_queries(servers_obj, [], "SRV1", user)

    assert result == []


@pytest.mark.unit
def test_filter_queries_admin_bypasses_group_checks():
    servers_obj = Mock()
    server = Mock(grp_authorized=["restricted"])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=True, groups=["all"])

    query = Mock(servers_id=["SRV1"], grp_authorized=["restricted"], hide=0)

    result = filter_queries(servers_obj, [query], "SRV1", user)

    assert result == [query]


@pytest.mark.unit
def test_filter_queries_skips_query_not_for_server():
    servers_obj = Mock()
    server = Mock(grp_authorized=[])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=False, groups=["all"])

    query = Mock(servers_id=["OTHER_SERVER"], grp_authorized=[], hide=0)

    result = filter_queries(servers_obj, [query], "SRV1", user)

    assert result == []


@pytest.mark.unit
def test_filter_queries_respects_hide_level_for_non_admin():
    servers_obj = Mock()
    server = Mock(grp_authorized=[])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=False, groups=["all"])

    hidden_query = Mock(servers_id=["SRV1"], grp_authorized=[], hide=1)

    result = filter_queries(servers_obj, [hidden_query], "SRV1", user)

    assert result == []


@pytest.mark.unit
def test_filter_queries_admin_sees_hide_level_1_but_not_2():
    servers_obj = Mock()
    server = Mock(grp_authorized=[])
    servers_obj.servers_dict = {"SRV1": server}
    user = Mock(admin=True, groups=["all"])

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

    queries, errors = build_queries_from_parsers(mock_app_ctx, mock_user_ctx, parsers)

    assert errors == []
    assert [q.name for q in queries] == ["AQUERY", "BQUERY"]


@pytest.mark.unit
def test_build_queries_collects_errors_without_blocking(mock_app_ctx, mock_user_ctx, tmp_path):
    (tmp_path / "good.sql").write_text("/*\ncode: GOOD\n*/\nSELECT 1")
    (tmp_path / "bad.sql").write_text("/*\ncode: BAD\n*/\nDECLARE\n@!X as int = 1  -- Desc|ENTRY\n;\nSELECT 1")
    parsers, _ = load_files(tmp_path)

    queries, errors = build_queries_from_parsers(mock_app_ctx, mock_user_ctx, parsers)

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
    user_a.user_info = Mock(return_value="USER_A")
    user_ctx_a = Mock(user=user_a, user_prefs=Mock(), display_format=DisplayFormat())

    user_b = Mock(attribs_cust={"x3_id": "USER_B"})
    user_b.user_info = Mock(return_value="USER_B")
    user_ctx_b = Mock(user=user_b, user_prefs=Mock(), display_format=DisplayFormat())

    queries_a, _ = build_queries_from_parsers(mock_app_ctx, user_ctx_a, parsers)
    queries_b, _ = build_queries_from_parsers(mock_app_ctx, user_ctx_b, parsers)

    assert queries_a[0].parameters.params["@USER"].display_value == "USER_A"
    assert queries_b[0].parameters.params["@USER"].display_value == "USER_B"


# ================================================================================
# build_queries_from_remote_server Tests
# ================================================================================


@pytest.fixture
def remote_server() -> RemoteServer:
    address = RemoteAddress(url="http://example.com", port=8000, username="alice")
    return RemoteServer(id="REMOTE_SRV", description="Remote Server", remote=Remote(None, address))


@pytest.mark.unit
def test_build_queries_from_remote_empty_when_no_serialized_queries(mock_app_ctx, mock_user_ctx, remote_server):
    queries, errors = build_queries_from_remote(mock_app_ctx, mock_user_ctx, remote_server)

    assert queries == []
    assert errors == []


_USER_INFO_QUERY = """/* Infos requête
Code : USER_INFO_QUERY
Hide : 0
*/

DECLARE
@dept as nvarchar(50) = ''  -- Department|USER_INFO(dept)
;

SELECT 1
"""


def _user_ctx_knowing_dept(mock_user_prefs, dept: str) -> UserContext:
    """Real UserContext (so with_remote_user_infos() really runs) whose local user answers `dept` to USER_INFO"""
    local_user = Mock()
    local_user.user_info = Mock(return_value=dept)
    ctx = UserContext(Mock(), user_override=local_user)
    ctx._base = Mock(user_prefs=mock_user_prefs)  # prefs are provided by the base context
    return ctx


def _user_info_mock(ctx: UserContext) -> Mock:
    """The Mock behind ctx.current_user.user_info, to assert on how the local user was consulted"""
    return cast(Mock, ctx.user).user_info


def _serialize_user_info_query_into(remote_server, mock_app_ctx, builder_ctx, tmp_path):
    """Builds the query with `builder_ctx` (the "server", whose user knows dept) and serializes it into remote_server"""
    (tmp_path / "user_info_query.sql").write_text(_USER_INFO_QUERY, encoding="utf-8")
    server_queries, load_errors = get_queries(mock_app_ctx, builder_ctx, tmp_path)
    assert load_errors == []
    remote_server.serialized_queries = [q.serialize() for q in server_queries]


@pytest.mark.unit
def test_build_queries_from_remote_resolves_user_info_with_the_remote_user(
    mock_app_ctx, mock_user_prefs, remote_server, tmp_path
):
    """USER_INFO(dept) must be resolved with the user the remote knows (its user_infos), not the local one."""
    _serialize_user_info_query_into(
        remote_server, mock_app_ctx, _user_ctx_knowing_dept(mock_user_prefs, "IT"), tmp_path
    )
    remote_server.remote.user_infos = {"username": "alice", "attribs_cust": {"dept": "IT"}}
    local_ctx = _user_ctx_knowing_dept(mock_user_prefs, "")  # the local user has no "dept"

    queries, errors = build_queries_from_remote(mock_app_ctx, local_ctx, remote_server)

    assert errors == []
    assert queries[0].parameters.params["@dept"].display_value == "IT"
    _user_info_mock(local_ctx).assert_not_called()


@pytest.mark.unit
def test_build_queries_from_remote_collects_and_sorts(mock_app_ctx, mock_user_ctx, remote_server, tmp_path):
    for name, code in [("b", "BQUERY"), ("a", "AQUERY")]:
        (tmp_path / f"{name}.sql").write_text(f"/*\ncode: {code}\n*/\nSELECT 1")
    local_queries, load_errors = get_queries(mock_app_ctx, mock_user_ctx, tmp_path)
    assert load_errors == []
    remote_server.serialized_queries = [q.serialize() for q in local_queries]

    queries, errors = build_queries_from_remote(mock_app_ctx, mock_user_ctx, remote_server)

    assert errors == []
    assert [q.name for q in queries] == ["AQUERY", "BQUERY"]
    assert all(q.remote_server is remote_server for q in queries)


@pytest.mark.unit
def test_build_queries_from_remote_collects_errors_without_blocking(
    mock_app_ctx, mock_user_ctx, remote_server, tmp_path
):
    (tmp_path / "good.sql").write_text("/*\ncode: GOOD\n*/\nSELECT 1")
    local_queries, load_errors = get_queries(mock_app_ctx, mock_user_ctx, tmp_path)
    assert load_errors == []
    good_data = local_queries[0].serialize()
    bad_data = {"name": "BAD"}  # missing required keys - KeyError while deserializing
    remote_server.serialized_queries = [good_data, bad_data]

    queries, errors = build_queries_from_remote(mock_app_ctx, mock_user_ctx, remote_server)

    assert len(queries) == 1
    assert queries[0].name == "GOOD"
    assert len(errors) == 1
    assert remote_server.id_on_remote in errors[0]
