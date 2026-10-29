"""Format of the data exchanged between Client and Server"""

import json
from datetime import datetime, date
from decimal import Decimal
from enum import StrEnum
from typing import Any


# ------------------------------------------------------------------------------------------
# NDJSON lines of /execute
# ------------------------------------------------------------------------------------------
class StreamKey(StrEnum):
    """Key of each NDJSON line streamed by Pytre_Server's /execute - one key per line."""

    COLUMNS = "__columns__"
    ROW = "__row__"
    PING = "__ping__"
    ERROR = "__error__"


def stream_line(key: StreamKey, payload: Any) -> str:
    """One NDJSON line of /execute's response, newline included."""
    return json.dumps({key: payload}) + "\n"


def stream_error(error: BaseException, prefix: str = "") -> str:
    """NDJSON line reporting `error` to the client, its message preceded by `prefix`."""
    return stream_line(StreamKey.ERROR, {"type": type(error).__name__, "message": f"{prefix}{error}"})


# ------------------------------------------------------------------------------------------
# Row values encoding
# ------------------------------------------------------------------------------------------
def encode_row_value(value: Any) -> Any:
    """JSON-safe encoding of a raw SQL value"""

    if isinstance(value, datetime):  # no native date type
        return {"$datetime": value.isoformat()}
    elif isinstance(value, date):  # no native date type
        return {"$date": value.isoformat()}
    elif isinstance(value, Decimal):  # JSON number would lose Decimal's exact precision
        return {"$decimal": str(value)}

    return value


def decode_row_value(value: Any) -> Any:
    """Reverses encode_row_value()"""
    if not isinstance(value, dict):
        return value

    if "$datetime" in value:
        return datetime.fromisoformat(value["$datetime"])
    elif "$date" in value:
        return date.fromisoformat(value["$date"])
    elif "$decimal" in value:
        return Decimal(value["$decimal"])

    return value
