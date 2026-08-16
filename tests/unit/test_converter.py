"""
Tests for Pytre/sql_query/converter.py

Tests for SQL type conversions (to_cmd, to_display, from_result)
"""

import pytest
from datetime import date, datetime
from decimal import Decimal

from Pytre_Core.sql_query.converter import SqlTypeConverter


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def converter():
    """Default converter with French settings"""
    return SqlTypeConverter(date_display="%d/%m/%Y", decimal_separator=",")


@pytest.fixture
def converter_us():
    """US-style converter"""
    return SqlTypeConverter(date_display="%m/%d/%Y", decimal_separator=".")


# ================================================================================
# to_cmd() - Bit Type Conversion
# ================================================================================


@pytest.mark.unit
def test_to_cmd_bit_valid_values(converter):
    """Bit type should accept 0 and 1"""
    assert converter.to_cmd("bit", "0") == 0
    assert converter.to_cmd("bit", "1") == 1


@pytest.mark.unit
def test_to_cmd_bit_invalid_value_raises_error(converter):
    """Bit type should reject values other than 0 or 1"""
    with pytest.raises(ValueError, match="ne peut être que 0 ou 1"):
        converter.to_cmd("bit", "2")


@pytest.mark.unit
def test_to_cmd_bit_non_numeric_raises_error(converter):
    """Bit type should reject non-numeric strings"""
    with pytest.raises(ValueError, match="n'est pas un nombre entier valide"):
        converter.to_cmd("bit", "true")


# ================================================================================
# to_cmd() - Integer Type Conversion
# ================================================================================


@pytest.mark.unit
def test_to_cmd_int_valid_values(converter):
    """Int type should accept valid integers"""
    assert converter.to_cmd("int", "42") == 42
    assert converter.to_cmd("int", "-10") == -10
    assert converter.to_cmd("int", "0") == 0


@pytest.mark.unit
def test_to_cmd_int_invalid_raises_error(converter):
    """Int type should reject non-integer strings"""
    with pytest.raises(ValueError, match="n'est pas un nombre entier valide"):
        converter.to_cmd("int", "3.14")


# ================================================================================
# to_cmd() - Date Type Conversion
# ================================================================================


@pytest.mark.unit
def test_to_cmd_date_french_format(converter):
    """Date should convert from dd/mm/yyyy to yyyy-mm-dd"""
    result = converter.to_cmd("date", "25/12/2025")
    assert result == "2025-12-25"


@pytest.mark.unit
def test_to_cmd_date_compact_format(converter):
    """Date should accept ddmmyyyy format"""
    result = converter.to_cmd("date", "25122025")
    assert result == "2025-12-25"


@pytest.mark.unit
def test_to_cmd_date_short_year(converter):
    """Date should accept dd/mm/yy format"""
    result = converter.to_cmd("date", "25/12/25")
    assert result == "2025-12-25"


@pytest.mark.unit
def test_to_cmd_date_invalid_raises_error(converter):
    """Invalid date should raise ValueError"""
    with pytest.raises(ValueError, match="n'est pas une date valide"):
        converter.to_cmd("date", "32/13/2025")


# ================================================================================
# to_cmd() - DateTime Type Conversion
# ================================================================================


@pytest.mark.unit
def test_to_cmd_datetime_french_format(converter):
    """DateTime should convert from dd/mm/yyyy hh:mm:ss to yyyy-mm-dd hh:mm:ss"""
    result = converter.to_cmd("datetime", "25/12/2025 14:30:00")
    assert result == "2025-12-25 14:30:00"


@pytest.mark.unit
def test_to_cmd_datetime_invalid_raises_error(converter):
    """Invalid datetime should raise ValueError"""
    with pytest.raises(ValueError, match="n'est pas une date valide"):
        converter.to_cmd("datetime", "invalid")


# ================================================================================
# to_cmd() - String Type Conversion (nvarchar, varchar, text)
# ================================================================================


@pytest.mark.unit
def test_to_cmd_nvarchar_valid(converter):
    """nvarchar should accept strings within max length"""
    result = converter.to_cmd("nvarchar", "Hello", type_args=["50"])
    assert result == "Hello"


@pytest.mark.unit
def test_to_cmd_nvarchar_max(converter):
    """nvarchar(max) should accept long strings (up to 255 default)"""
    # When type_args is ["max"], max_size is 255 (see line 113 in sql_converter.py)
    long_string = "A" * 255
    result = converter.to_cmd("nvarchar", long_string, type_args=["max"])
    assert result == long_string


@pytest.mark.unit
def test_to_cmd_nvarchar_too_long_raises_error(converter):
    """nvarchar should reject strings exceeding max length"""
    with pytest.raises(ValueError, match="a plus de caractères qu'autorisés"):
        converter.to_cmd("nvarchar", "Hello World", type_args=["5"])


@pytest.mark.unit
def test_to_cmd_varchar_valid(converter):
    """varchar should work like nvarchar"""
    result = converter.to_cmd("varchar", "Test", type_args=["10"])
    assert result == "Test"


# ================================================================================
# to_cmd() - Char Type Conversion (fixed length)
# ================================================================================


@pytest.mark.unit
def test_to_cmd_char_exact_length(converter):
    """char should accept string of exact length"""
    result = converter.to_cmd("char", "ABC", type_args=["3"])
    assert result == "ABC"


@pytest.mark.unit
def test_to_cmd_char_wrong_length_raises_error(converter):
    """char should reject string of wrong length"""
    with pytest.raises(ValueError, match="n'est pas de la bonne taille"):
        converter.to_cmd("char", "AB", type_args=["3"])


@pytest.mark.unit
def test_to_cmd_char_no_size_raises_error(converter):
    """char without size parameter should raise error"""
    with pytest.raises(ValueError, match="Taille non déclarée"):
        converter.to_cmd("char", "ABC")


# ================================================================================
# to_cmd() - Empty String (Null Values)
# ================================================================================


@pytest.mark.unit
def test_to_cmd_empty_string_int_returns_zero(converter):
    """Empty string for int should return 0"""
    result = converter.to_cmd("int", "")
    assert result == 0


@pytest.mark.unit
def test_to_cmd_empty_string_default_returns_space(converter):
    """Empty string for other types should return space"""
    result = converter.to_cmd("nvarchar", "")
    assert result == " "


# ================================================================================
# to_cmd() - Unknown Types (passthrough)
# ================================================================================


@pytest.mark.unit
def test_to_cmd_unknown_type_passthrough(converter):
    """Unknown types should pass through unchanged"""
    result = converter.to_cmd("unknown_type", "some value")
    assert result == "some value"


# ================================================================================
# to_display() - Date and DateTime Display
# ================================================================================


@pytest.mark.unit
def test_to_display_date_french_format(converter):
    """Date should convert from yyyy-mm-dd to dd/mm/yyyy"""
    result = converter.to_display("date", "2025-12-25")
    assert result == "25/12/2025"


@pytest.mark.unit
def test_to_display_date_us_format(converter_us):
    """Date should respect US format mm/dd/yyyy"""
    result = converter_us.to_display("date", "2025-12-25")
    assert result == "12/25/2025"


@pytest.mark.unit
def test_to_display_datetime_french_format(converter):
    """DateTime should convert to dd/mm/yyyy hh:mm:ss"""
    result = converter.to_display("datetime", "2025-12-25 14:30:00")
    assert result == "25/12/2025 14:30:00"


@pytest.mark.unit
def test_to_display_empty_date_returns_empty(converter):
    """Empty date should return empty string"""
    result = converter.to_display("date", "")
    assert result == ""


# ================================================================================
# to_display() - Unknown Types
# ================================================================================


@pytest.mark.unit
def test_to_display_unknown_type_passthrough(converter):
    """Unknown types should pass through unchanged"""
    result = converter.to_display("unknown", "test")
    assert result == "test"


# ================================================================================
# from_result() - String Results
# ================================================================================


@pytest.mark.unit
def test_from_result_string(converter):
    """String results should pass through"""
    result = converter.from_result("Hello World")
    assert result == "Hello World"


@pytest.mark.unit
def test_from_result_string_single_space_becomes_empty(converter):
    """Single space string should become empty"""
    result = converter.from_result(" ")
    assert result == ""


# ================================================================================
# from_result() - Numeric Results
# ================================================================================


@pytest.mark.unit
def test_from_result_int(converter):
    """Integer should convert to string"""
    result = converter.from_result(42)
    assert result == "42"


@pytest.mark.unit
def test_from_result_float(converter):
    """Float should convert to string with decimal separator"""
    result = converter.from_result(3.14)
    assert result == "3,14"


@pytest.mark.unit
def test_from_result_float_trailing_zeros_removed(converter):
    """Trailing zeros should be removed from floats"""
    result = converter.from_result(3.10)
    assert result == "3,1"


@pytest.mark.unit
def test_from_result_float_us_decimal(converter_us):
    """US converter should use dot as decimal separator"""
    result = converter_us.from_result(3.14)
    assert result == "3.14"


@pytest.mark.unit
def test_from_result_decimal(converter):
    """Decimal should convert with proper separator"""
    result = converter.from_result(Decimal("123.45"))
    assert result == "123,45"


@pytest.mark.unit
def test_from_result_scientific_notation_becomes_empty(converter):
    """Scientific notation 0E-XX should become empty"""
    result = converter.from_result(Decimal("0E-10"))
    assert result == ""


# ================================================================================
# from_result() - Boolean Results
# ================================================================================


@pytest.mark.unit
def test_from_result_bool_true(converter):
    """True should convert to 'Vrai'"""
    result = converter.from_result(True)
    assert result == "Vrai"


@pytest.mark.unit
def test_from_result_bool_false(converter):
    """False should convert to 'Faux'"""
    result = converter.from_result(False)
    assert result == "Faux"


# ================================================================================
# from_result() - DateTime Results
# ================================================================================


@pytest.mark.unit
def test_from_result_datetime_with_time(converter):
    """DateTime with time should include time in result"""
    dt = datetime(2025, 12, 25, 14, 30, 0)
    result = converter.from_result(dt)
    assert result == "25/12/2025 14:30:00"


@pytest.mark.unit
def test_from_result_datetime_midnight_only_date(converter):
    """DateTime at midnight should only show date"""
    dt = datetime(2025, 12, 25, 0, 0, 0)
    result = converter.from_result(dt)
    assert result == "25/12/2025"


@pytest.mark.unit
def test_from_result_datetime_sql_min_becomes_empty(converter):
    """SQL Server min datetime (1753-01-01) should become empty"""
    dt = datetime(1753, 1, 1, 0, 0, 0)
    result = converter.from_result(dt)
    assert result == ""


@pytest.mark.unit
def test_from_result_date(converter):
    """A plain date (not datetime) should use date_display_format"""
    d = date(2025, 12, 25)
    result = converter.from_result(d)
    assert result == "25/12/2025"


@pytest.mark.unit
def test_from_result_date_us_format(converter_us):
    """A plain date should respect the configured date_display_format"""
    d = date(2025, 12, 25)
    result = converter_us.from_result(d)
    assert result == "12/25/2025"


@pytest.mark.unit
def test_from_result_date_sql_min_becomes_empty(converter):
    """SQL Server min date (1753-01-01) should become empty, same rule as datetime"""
    d = date(1753, 1, 1)
    result = converter.from_result(d)
    assert result == ""


@pytest.mark.unit
def test_from_result_date_is_not_confused_with_datetime(converter):
    """type() dispatch is exact - a plain date must never hit the datetime handler
    (which would fail: date has no .hour/.minute/.second)"""
    d = date(2025, 12, 25)
    dt = datetime(2025, 12, 25, 14, 30, 0)

    assert converter.from_result(d) == "25/12/2025"
    assert converter.from_result(dt) == "25/12/2025 14:30:00"


# ================================================================================
# from_result() - None Results
# ================================================================================


@pytest.mark.unit
def test_from_result_none(converter):
    """None should convert to empty string"""
    result = converter.from_result(None)
    assert result == ""


# ================================================================================
# from_result() - Unknown Types
# ================================================================================


@pytest.mark.unit
def test_from_result_unknown_type(converter):
    """Unknown types should convert to string"""
    class CustomClass:
        def __str__(self):
            return "custom"

    result = converter.from_result(CustomClass())
    assert result == "custom"


# ================================================================================
# Edge Cases
# ================================================================================


@pytest.mark.unit
def test_converter_custom_formats():
    """Converter should accept custom date formats"""
    custom = SqlTypeConverter(date_display="%Y-%m-%d", decimal_separator=".")

    # Date display should use custom format
    result = custom.to_display("date", "2025-12-25")
    assert result == "2025-12-25"

    # Decimal should use dot
    result = custom.from_result(3.14)
    assert result == "3.14"


@pytest.mark.unit
def test_to_cmd_date_tries_multiple_formats(converter):
    """Date conversion should try multiple formats"""
    # Should accept both dd/mm/yyyy and ddmmyyyy
    assert converter.to_cmd("date", "25/12/2025") == "2025-12-25"
    assert converter.to_cmd("date", "25122025") == "2025-12-25"
    assert converter.to_cmd("date", "25/12/25") == "2025-12-25"
