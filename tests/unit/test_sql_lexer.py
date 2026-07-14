"""
Tests for Pytre/sql_query/sql_lexer.py

Basic tokenization tests for SQL lexer.
"""

import pytest
from Pytre_Core.sql_query.sql_lexer import SqlLexer, Token, TokenType


# ================================================================================
# Token Class Tests
# ================================================================================


@pytest.mark.unit
def test_token_creation():
    """Token should initialize with correct attributes"""
    token = Token(start=0, length=6, type=TokenType.KEYWORD, value="SELECT")

    assert token.pos == 0
    assert token.length == 6
    assert token.type == TokenType.KEYWORD
    assert token.value == "SELECT"


@pytest.mark.unit
def test_token_repr():
    """Token __repr__ should return dict representation"""
    token = Token(start=0, length=6, type=TokenType.KEYWORD, value="SELECT")

    repr_str = repr(token)

    assert "start" in repr_str
    assert "length" in repr_str
    assert "KEYWORD" in repr_str


@pytest.mark.unit
def test_token_str():
    """Token __str__ should return formatted string"""
    token = Token(start=0, length=6, type=TokenType.KEYWORD, value="SELECT")

    str_result = str(token)

    assert "KEYWORD" in str_result
    assert "SELECT" in str_result


# ================================================================================
# SqlLexer Tokenization Tests
# ================================================================================


@pytest.mark.unit
def test_lexer_tokenize_simple_select():
    """Lexer should tokenize simple SELECT statement"""
    sql = "SELECT * FROM table"

    lexer = SqlLexer(sql)

    # Should have tokens
    assert len(lexer.tokens) > 0

    # Should identify SELECT as keyword
    keywords = lexer.tokens_get(TokenType.KEYWORD)
    keyword_values = [t.value.upper() for t in keywords]

    assert "SELECT" in keyword_values
    assert "FROM" in keyword_values


@pytest.mark.unit
def test_lexer_tokenize_empty_string():
    """Lexer should handle empty string"""
    lexer = SqlLexer("")

    assert len(lexer.tokens) == 0


@pytest.mark.unit
def test_lexer_tokenize_parameter():
    """Lexer should identify SQL parameters"""
    sql = "WHERE id = @param1"

    lexer = SqlLexer(sql)

    # Should identify @param1 as parameter
    parameters = lexer.tokens_get(TokenType.PARAMETER)

    assert len(parameters) > 0
    param_values = [t.value for t in parameters]
    assert "@param1" in param_values


@pytest.mark.unit
def test_lexer_tokenize_comment():
    """Lexer should identify SQL comments"""
    sql = "SELECT * -- This is a comment\nFROM table"

    lexer = SqlLexer(sql)

    # Should identify comment
    comments = lexer.tokens_get(TokenType.COMMENT)

    assert len(comments) > 0


@pytest.mark.unit
def test_lexer_tokenize_number():
    """Lexer should identify numbers"""
    sql = "SELECT 123, 45.67"

    lexer = SqlLexer(sql)

    # Should identify numbers
    numbers = lexer.tokens_get(TokenType.NUMBER)

    assert len(numbers) >= 2


@pytest.mark.unit
def test_lexer_tokenize_identifier():
    """Lexer should identify identifiers (column/table names)"""
    sql = "SELECT column_name FROM my_table"

    lexer = SqlLexer(sql)

    # Should identify identifiers
    identifiers = lexer.tokens_get(TokenType.IDENTIFIER)

    assert len(identifiers) > 0


@pytest.mark.unit
def test_lexer_tokens_get_all():
    """tokens_get() without type should return all tokens"""
    sql = "SELECT * FROM table"

    lexer = SqlLexer(sql)

    all_tokens = list(lexer.tokens_get())

    assert len(all_tokens) > 0


@pytest.mark.unit
def test_lexer_tokens_get_filtered():
    """tokens_get() with type should filter tokens"""
    sql = "SELECT * FROM table WHERE id = @param"

    lexer = SqlLexer(sql)

    keywords = lexer.tokens_get(TokenType.KEYWORD)
    parameters = lexer.tokens_get(TokenType.PARAMETER)

    # Should have different counts
    assert len(keywords) > 0
    assert len(parameters) > 0
    assert len(keywords) != len(parameters)


# ================================================================================
# Edge Cases
# ================================================================================


@pytest.mark.unit
def test_lexer_tokenize_multiline_sql():
    """Lexer should handle multi-line SQL"""
    sql = """SELECT
    col1,
    col2
FROM table
WHERE id = 1"""

    lexer = SqlLexer(sql)

    # Should tokenize successfully
    assert len(lexer.tokens) > 0

    # Should identify keywords
    keywords = lexer.tokens_get(TokenType.KEYWORD)
    assert len(keywords) >= 3  # SELECT, FROM, WHERE


@pytest.mark.unit
def test_lexer_tokenize_nested_comments():
    """Lexer should handle nested comments"""
    sql = "/* Outer /* Inner */ comment */ SELECT *"

    lexer = SqlLexer(sql)

    # Should identify comment
    comments = lexer.tokens_get(TokenType.COMMENT)
    assert len(comments) > 0


@pytest.mark.unit
def test_lexer_tokenize_string_literals():
    """Lexer should handle string literals"""
    sql = "SELECT 'test string' FROM table"

    lexer = SqlLexer(sql)

    # Should identify text
    texts = lexer.tokens_get(TokenType.TEXT)
    assert len(texts) > 0


@pytest.mark.unit
def test_lexer_tokenize_operators():
    """Lexer should identify operators"""
    sql = "WHERE a = b AND c > d OR e < f"

    lexer = SqlLexer(sql)

    # Should identify operators
    operators = lexer.tokens_get(TokenType.OPERATOR)
    assert len(operators) > 0


@pytest.mark.unit
def test_lexer_tokenize_delimiters():
    """Lexer should identify delimiters"""
    sql = "SELECT col1, col2; SELECT col3;"

    lexer = SqlLexer(sql)

    # Should identify delimiters
    delimiters = lexer.tokens_get(TokenType.DELIMITER)
    assert len(delimiters) > 0
