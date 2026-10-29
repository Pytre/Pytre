"""
Tests for Pytre_Core/remote_protocol.py
"""

import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from Pytre_Core.remote_protocol import StreamKey, decode_row_value, encode_row_value, stream_error, stream_line


# ================================================================================
# NDJSON lines of /execute
# ================================================================================


@pytest.mark.unit
def test_stream_keys_are_the_ones_on_the_wire():
    """Clients and servers of other versions rely on these exact values."""
    assert {key.name: str(key) for key in StreamKey} == {
        "COLUMNS": "__columns__",
        "ROW": "__row__",
        "PING": "__ping__",
        "ERROR": "__error__",
    }


@pytest.mark.unit
def test_stream_keys_match_the_keys_of_a_decoded_line():
    record = json.loads('{"__ping__": true}')

    assert StreamKey.PING in record
    assert record[StreamKey.PING] is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "key, payload, expected",
    [
        (StreamKey.COLUMNS, ("id", "label"), '{"__columns__": ["id", "label"]}\n'),
        (StreamKey.ROW, (1, None, {"$date": "2026-01-01"}), '{"__row__": [1, null, {"$date": "2026-01-01"}]}\n'),
        (StreamKey.PING, True, '{"__ping__": true}\n'),
    ],
)
def test_stream_line_is_one_json_object_and_a_newline(key, payload, expected):
    assert stream_line(key, payload) == expected


@pytest.mark.unit
def test_stream_lines_concatenate_into_ndjson():
    lines = stream_line(StreamKey.COLUMNS, ["n"]) + stream_line(StreamKey.ROW, [1]) + stream_line(StreamKey.ROW, [2])

    assert [json.loads(line) for line in lines.splitlines()] == [
        {"__columns__": ["n"]},
        {"__row__": [1]},
        {"__row__": [2]},
    ]


@pytest.mark.unit
def test_stream_error_reports_the_error_type_and_message():
    line = stream_error(ValueError("boom"))

    assert line.endswith("\n")
    assert json.loads(line) == {"__error__": {"type": "ValueError", "message": "boom"}}


@pytest.mark.unit
def test_stream_error_prefixes_the_message_only():
    line = stream_error(ConnectionError("refused"), "Server unavailable: ")

    assert json.loads(line) == {"__error__": {"type": "ConnectionError", "message": "Server unavailable: refused"}}


# ================================================================================
# Row values encoding
# ================================================================================


@pytest.mark.unit
def test_encode_row_value_wraps_datetime_date_and_decimal():
    assert encode_row_value(datetime(2026, 1, 1, 10, 30)) == {"$datetime": "2026-01-01T10:30:00"}
    assert encode_row_value(date(2026, 1, 1)) == {"$date": "2026-01-01"}
    assert encode_row_value(Decimal("12.50")) == {"$decimal": "12.50"}


@pytest.mark.unit
def test_encode_row_value_passes_through_json_native_types():
    assert encode_row_value("hello") == "hello"
    assert encode_row_value(3) == 3
    assert encode_row_value(1.5) == 1.5
    assert encode_row_value(True) is True
    assert encode_row_value(None) is None


@pytest.mark.unit
def test_decode_row_value_unwraps_datetime_date_and_decimal():
    assert decode_row_value({"$datetime": "2026-01-01T10:30:00"}) == datetime(2026, 1, 1, 10, 30)
    assert decode_row_value({"$date": "2026-01-01"}) == date(2026, 1, 1)
    assert decode_row_value({"$decimal": "12.50"}) == Decimal("12.50")


@pytest.mark.unit
def test_decode_row_value_passes_through_unwrapped_values():
    assert decode_row_value("hello") == "hello"
    assert decode_row_value(3) == 3
    assert decode_row_value(None) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "value", [datetime(2026, 1, 1, 10, 30), date(2026, 1, 1), Decimal("12.50"), "hello", 3, 1.5, True, None]
)
def test_encode_decode_round_trip(value):
    assert decode_row_value(encode_row_value(value)) == value


@pytest.mark.unit
@pytest.mark.parametrize("value", [datetime(2026, 1, 1, 10, 30), date(2026, 1, 1), Decimal("12.50")])
def test_encoded_row_values_round_trip_through_a_stream_line(value):
    """What the server sends is what the client reads back."""
    record = json.loads(stream_line(StreamKey.ROW, (encode_row_value(value),)))

    assert decode_row_value(record[StreamKey.ROW][0]) == value
