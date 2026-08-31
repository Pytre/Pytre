"""
Tests for Pytre_Server/query_catalog.py
"""

import pytest

from Pytre_Server.query_catalog import QueryCatalog


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
