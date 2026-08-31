"""
Tests for Pytre_Server/query_catalog.py
"""

import pytest

from Pytre_Server.query_catalog import QueryCatalog


@pytest.mark.unit
def test_load_populates_by_name_from_the_code_header(tmp_path):
    (tmp_path / "a.sql").write_text("/*\ncode: QUERY_A\n*/\nSELECT 1")
    (tmp_path / "b.sql").write_text("/*\ncode: QUERY_B\n*/\nSELECT 2")

    catalog = QueryCatalog.load(tmp_path)

    assert set(catalog.by_name.keys()) == {"QUERY_A", "QUERY_B"}
    assert catalog.by_name["QUERY_A"].filename.name == "a.sql"
    assert catalog.by_name["QUERY_B"].filename.name == "b.sql"


@pytest.mark.unit
def test_load_falls_back_to_filename_stem_when_no_code_header(tmp_path):
    (tmp_path / "no_header.sql").write_text("SELECT 1")

    catalog = QueryCatalog.load(tmp_path)

    assert "no_header" in catalog.by_name
    assert catalog.by_name["no_header"].filename.name == "no_header.sql"


@pytest.mark.unit
def test_load_by_name_matches_parsers_list(tmp_path):
    (tmp_path / "a.sql").write_text("/*\ncode: QUERY_A\n*/\nSELECT 1")

    catalog = QueryCatalog.load(tmp_path)

    assert len(catalog.by_name) == len(catalog.parsers)
    assert catalog.by_name["QUERY_A"] in catalog.parsers
