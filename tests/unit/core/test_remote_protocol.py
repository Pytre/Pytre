"""
Tests for Pytre_Core/remote_protocol.py
"""

import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from Pytre_Core.remote_protocol import (
    Batch,
    JobKey,
    JobStatus,
    decode_row_value,
    encode_row_value,
    error_payload,
)


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


# ================================================================================
# JSON responses of /jobs
# ================================================================================


@pytest.mark.unit
def test_job_keys_and_statuses_are_the_ones_on_the_wire():
    assert [key.value for key in JobKey] == ["job_id", "status", "error", "batch", "columns", "rows", "last"]
    assert [status.value for status in JobStatus] == ["running", "ready", "pending", "ok", "error", "cancelled"]


@pytest.mark.unit
def test_error_payload_reports_the_error_type_and_its_prefixed_message():
    assert error_payload(ValueError("boom"), "SQL error: ") == {"type": "ValueError", "message": "SQL error: boom"}


@pytest.mark.unit
def test_batch_encodes_a_compact_json_response():
    body = Batch(2, [(1, "a")], last=True).encode()

    assert body == b'{"status":"ok","batch":2,"rows":[[1,"a"]],"last":true}'


@pytest.mark.unit
def test_batch_carries_its_columns_only_when_given():
    assert json.loads(Batch(1, [], last=False, columns=["id"]).encode())[JobKey.COLUMNS] == ["id"]
    assert JobKey.COLUMNS not in json.loads(Batch(2, [], last=False).encode())


@pytest.mark.unit
def test_batch_round_trips_its_values():
    rows = [(datetime(2026, 1, 1, 10, 30), date(2026, 1, 1), Decimal("12.50"), "a", 3, None)]
    batch = Batch(1, rows, last=False, columns=["dt", "d", "dec", "s", "i", "n"])

    assert Batch.decode(json.loads(batch.encode())) == batch


@pytest.mark.unit
def test_batch_encode_raises_type_error_for_a_value_json_can_not_encode():
    with pytest.raises(TypeError):
        Batch(1, [(b"bytes",)], last=True).encode()


@pytest.mark.unit
def test_batch_decode_raises_key_error_for_a_response_without_rows():
    with pytest.raises(KeyError):
        Batch.decode({"status": "ok", "batch": 1, "last": True})
