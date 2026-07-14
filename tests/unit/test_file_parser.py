"""
Tests for Pytre/sql_query/file_parser.py
"""

import pytest
from pathlib import Path

from Pytre_Core.sql_query.file_parser import FileParser


# ================================================================================
# Fixtures
# ================================================================================


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
# FileParser Tests
# ================================================================================


@pytest.mark.unit
def test_file_parser_reads_file(sample_sql_file):
    """FileParser should read SQL file content"""
    parser = FileParser(sample_sql_file, encoding="utf-8")

    assert parser.filename == sample_sql_file
    assert "TEST_QUERY" in parser.content
    assert "SELECT" in parser.content
    assert "@param1" in parser.content


@pytest.mark.unit
def test_file_parser_reads_nonexistent_file():
    """FileParser should raise error for nonexistent file"""
    nonexistent = Path("/nonexistent/file.sql")

    # Should raise FileNotFoundError
    with pytest.raises(FileNotFoundError):
        parser = FileParser(nonexistent, encoding="utf-8")


@pytest.mark.unit
def test_file_parser_extract_header_infos(sample_sql_file):
    """FileParser should extract header info from SQL comments"""
    parser = FileParser(sample_sql_file, encoding="utf-8")

    infos = parser.extract_header_infos()

    assert infos.get("code") == "TEST_QUERY"
    assert infos.get("description") == "Test Query for Unit Tests"
    assert infos.get("hide") == "0"
    # Servers are converted to lowercase
    servers = infos.get("servers", [])
    assert "test_server" in servers or "TEST_SERVER" in servers


@pytest.mark.unit
def test_file_parser_extract_raw_command(sample_sql_file):
    """FileParser should extract SQL command without DECLARE block"""
    parser = FileParser(sample_sql_file, encoding="utf-8")

    raw_cmd = parser.extract_raw_command()

    # Should NOT contain DECLARE block itself
    assert "DECLARE" not in raw_cmd

    # Should contain SELECT statement
    assert "SELECT" in raw_cmd
    assert "FROM test_table" in raw_cmd

    # Note: Parameters may still be in WHERE clause, that's expected


# ================================================================================
# Edge Cases
# ================================================================================


@pytest.mark.unit
def test_file_parser_handles_empty_file(tmp_path):
    """FileParser should handle empty file"""
    empty_file = tmp_path / "empty.sql"
    empty_file.write_text("", encoding="utf-8")

    parser = FileParser(empty_file, encoding="utf-8")

    assert parser.content == ""
    assert parser.extract_header_infos() == {}
    assert parser.extract_raw_command() == ""


# ================================================================================
# FileParser Encoding Tests
# ================================================================================


@pytest.mark.unit
def test_file_parser_handles_utf8_encoding(tmp_path):
    """FileParser should handle UTF-8 encoding"""
    sql_file = tmp_path / "utf8.sql"
    content = "SELECT 'été' AS saison"  # French characters
    sql_file.write_text(content, encoding="utf-8")

    parser = FileParser(sql_file, encoding="utf-8")

    assert "été" in parser.content
    assert "saison" in parser.content


@pytest.mark.unit
def test_file_parser_fallback_encoding(tmp_path):
    """FileParser should try fallback encodings"""
    sql_file = tmp_path / "cp1252.sql"
    # Write with cp1252 encoding
    content = "SELECT 'test' FROM table"
    sql_file.write_bytes(content.encode("cp1252"))

    # Request UTF-8 but should fallback to cp1252
    parser = FileParser(sql_file, encoding="utf-8")

    assert "test" in parser.content


@pytest.mark.unit
def test_file_parser_empty_path():
    """FileParser should handle empty Path() without error"""
    parser = FileParser(Path(), encoding="utf-8")

    assert parser.content == ""
    assert parser.filename == Path()


@pytest.mark.unit
def test_file_parser_extract_header_no_comments():
    """FileParser should return empty dict when no header comments"""
    parser = FileParser()
    parser.content = "SELECT * FROM table"

    infos = parser.extract_header_infos()

    assert infos == {}


@pytest.mark.unit
def test_file_parser_extract_header_multiple_servers(tmp_path):
    """FileParser should parse multiple servers as list"""
    sql_file = tmp_path / "multi.sql"
    content = """/*
Servers : SERVER1, SERVER2, SERVER3
*/
SELECT * FROM table"""
    sql_file.write_text(content, encoding="utf-8")

    parser = FileParser(sql_file, encoding="utf-8")
    infos = parser.extract_header_infos()

    servers = infos.get("servers", [])
    assert isinstance(servers, list)
    assert len(servers) == 3


@pytest.mark.unit
def test_file_parser_extract_raw_command_no_declare():
    """FileParser should return full content when no DECLARE"""
    parser = FileParser()
    parser.content = "SELECT * FROM table WHERE id = 1"

    raw_cmd = parser.extract_raw_command()

    # Should return everything
    assert raw_cmd == parser.content


# ================================================================================
# FileParser Additional Tests
# ================================================================================


@pytest.mark.unit
def test_file_parser_extract_header_with_servers(tmp_path):
    """FileParser should parse servers from header"""
    sql_file = tmp_path / "test.sql"
    content = """/*
code: TEST_QUERY
description: Test description
servers: SERVER1, SERVER2, SERVER3
*/

SELECT 1
"""
    sql_file.write_text(content)

    parser = FileParser(sql_file)
    infos = parser.extract_header_infos()

    assert infos["code"] == "TEST_QUERY"
    assert infos["description"] == "Test description"
    assert "servers" in infos
    assert isinstance(infos["servers"], list)
    assert "server1" in infos["servers"]  # Should be lowercase
    assert "server2" in infos["servers"]


@pytest.mark.unit
def test_file_parser_extract_header_with_grp_authorized(tmp_path):
    """FileParser should parse grp_authorized from header"""
    sql_file = tmp_path / "test.sql"
    content = """/*
code: TEST
grp_authorized: admin, user, guest
*/

SELECT 1
"""
    sql_file.write_text(content)

    parser = FileParser(sql_file)
    infos = parser.extract_header_infos()

    assert "grp_authorized" in infos
    assert isinstance(infos["grp_authorized"], list)
    assert "admin" in infos["grp_authorized"]
    assert "user" in infos["grp_authorized"]
    assert "guest" in infos["grp_authorized"]
