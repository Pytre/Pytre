"""
Tests for Pytre_Server/query_catalog.py
"""

from unittest.mock import Mock

import pytest

from Pytre_Core.user_prefs import DisplayFormat
from Pytre_Server.query_catalog import QueryCatalog


@pytest.fixture
def mock_app_ctx():
    """Mock AppContext - enough for build_queries_from_parsers() (via with_build_errors()) to run."""
    ctx = Mock()
    ctx.servers.servers_dict = {}
    return ctx


@pytest.fixture
def mock_user_ctx():
    """Mock UserContext - enough for build_queries_from_parsers() (via with_build_errors()) to run."""
    ctx = Mock()
    ctx.current_user = Mock()
    ctx.user_prefs = Mock()
    ctx.display_format = DisplayFormat()
    return ctx


@pytest.mark.unit
def test_load_populates_by_file_id_from_filename_stem(tmp_path):
    (tmp_path / "a.sql").write_text("/*\ncode: QUERY_A\n*/\nSELECT 1")
    (tmp_path / "b.sql").write_text("/*\ncode: QUERY_B\n*/\nSELECT 2")

    catalog = QueryCatalog.load(tmp_path)

    assert set(catalog.by_file_id.keys()) == {"a", "b"}
    assert catalog.by_file_id["a"].filename.name == "a.sql"
    assert catalog.by_file_id["b"].filename.name == "b.sql"


@pytest.mark.unit
def test_load_by_file_id_ignores_the_code_header(tmp_path):
    """by_file_id keys on the filename stem regardless of the code: header - unlike Query.name,
    it never depends on free-text content, so it can't collide between two different files."""
    (tmp_path / "no_header.sql").write_text("SELECT 1")
    (tmp_path / "with_header.sql").write_text("/*\ncode: SOMETHING_ELSE\n*/\nSELECT 2")

    catalog = QueryCatalog.load(tmp_path)

    assert "no_header" in catalog.by_file_id
    assert catalog.by_file_id["no_header"].filename.name == "no_header.sql"
    assert "with_header" in catalog.by_file_id
    assert catalog.by_file_id["with_header"].filename.name == "with_header.sql"
    assert "SOMETHING_ELSE" not in catalog.by_file_id


@pytest.mark.unit
def test_load_by_file_id_matches_parsers_list(tmp_path):
    (tmp_path / "a.sql").write_text("/*\ncode: QUERY_A\n*/\nSELECT 1")

    catalog = QueryCatalog.load(tmp_path)

    assert len(catalog.by_file_id) == len(catalog.parsers)
    assert catalog.by_file_id["a"] in catalog.parsers


@pytest.mark.unit
def test_load_by_file_id_does_not_collide_on_duplicate_code_header(tmp_path):
    """Regression: two files declaring the same code: header used to collide in by_name (one
    silently overwrote the other) - by_file_id keys on the filename instead, which the
    filesystem already guarantees unique within a folder."""
    (tmp_path / "a.sql").write_text("/*\ncode: SAME_CODE\n*/\nSELECT 1")
    (tmp_path / "b.sql").write_text("/*\ncode: SAME_CODE\n*/\nSELECT 2")

    catalog = QueryCatalog.load(tmp_path)

    assert len(catalog.by_file_id) == 2
    assert catalog.by_file_id["a"].filename.name == "a.sql"
    assert catalog.by_file_id["b"].filename.name == "b.sql"


# ================================================================================
# with_build_errors
# ================================================================================


@pytest.mark.unit
def test_with_build_errors_is_empty_for_valid_queries(mock_app_ctx, mock_user_ctx, tmp_path):
    (tmp_path / "good.sql").write_text("/*\ncode: GOOD\n*/\nSELECT 1")
    catalog = QueryCatalog.load(tmp_path)

    validated = catalog.with_build_errors(mock_app_ctx, mock_user_ctx)

    assert validated.build_errors == []


@pytest.mark.unit
def test_with_build_errors_reports_a_structurally_broken_query(mock_app_ctx, mock_user_ctx, tmp_path):
    """A query whose file parses fine (FileParser succeeds) but fails to build into a Query (here:
    an invalid parameter name) used to be invisible everywhere - caught here, not just at
    load_files() time."""
    (tmp_path / "bad.sql").write_text("/*\ncode: BAD\n*/\nDECLARE\n@!X as int = 1  -- Desc|ENTRY\n;\nSELECT 1")
    catalog = QueryCatalog.load(tmp_path)

    validated = catalog.with_build_errors(mock_app_ctx, mock_user_ctx)

    assert len(validated.build_errors) == 1
    assert "bad.sql" in validated.build_errors[0]


@pytest.mark.unit
def test_with_build_errors_does_not_affect_load_errors(mock_app_ctx, mock_user_ctx, tmp_path):
    """build_errors and load_errors are independent lists - a build-time failure must not get
    mixed into or overwrite the (already empty here) load-time error list."""
    (tmp_path / "bad.sql").write_text("/*\ncode: BAD\n*/\nDECLARE\n@!X as int = 1  -- Desc|ENTRY\n;\nSELECT 1")
    catalog = QueryCatalog.load(tmp_path)

    validated = catalog.with_build_errors(mock_app_ctx, mock_user_ctx)

    assert validated.load_errors == []
    assert len(validated.build_errors) == 1


@pytest.mark.unit
def test_with_build_errors_returns_a_new_instance_leaving_the_original_untouched():
    """QueryCatalog stays frozen/immutable - with_build_errors() must not mutate self (see the
    class docstring: in-flight requests must keep seeing a consistent snapshot)."""
    catalog = QueryCatalog(parsers=[], by_file_id={}, load_errors=[])

    validated = catalog.with_build_errors(Mock(), Mock())

    assert validated is not catalog
    assert catalog.build_errors == []


@pytest.mark.unit
def test_with_build_errors_keeps_parsers_and_load_errors_identity(mock_app_ctx, mock_user_ctx, tmp_path):
    """replace() must only touch build_errors - parsers/by_file_id/load_errors carry over as the
    same objects, no unnecessary rebuilding."""
    (tmp_path / "a.sql").write_text("/*\ncode: A\n*/\nSELECT 1")
    catalog = QueryCatalog.load(tmp_path)

    validated = catalog.with_build_errors(mock_app_ctx, mock_user_ctx)

    assert validated.parsers is catalog.parsers
    assert validated.by_file_id is catalog.by_file_id
    assert validated.load_errors is catalog.load_errors
