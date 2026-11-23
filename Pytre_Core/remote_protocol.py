"""Format of the data exchanged between Client and Server"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from enum import StrEnum
from typing import Any


JOB_WAIT_SECS = 20  # longest wait of a /jobs response before the server answers "not yet"
JOB_ABANDON_SECS = 120  # a job nothing was asked for since so long is cancelled by the server


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


# ------------------------------------------------------------------------------------------
# JSON responses of /jobs: an execution started by a call, its result read by numbered batches
# ------------------------------------------------------------------------------------------
def error_payload(error: BaseException, prefix: str = "") -> dict[str, str]:
    """How `error` is reported to the client, its message preceded by `prefix`"""
    return {"type": type(error).__name__, "message": f"{prefix}{error}"}


class JobStatus(StrEnum):
    """`status` of each JSON response of /jobs"""

    RUNNING = "running"  # the execution isn't over yet
    READY = "ready"  # the execution is over, its batches can be read
    PENDING = "pending"  # the batch asked for isn't ready yet
    OK = "ok"  # the batch asked for is in the response
    ERROR = "error"  # the execution or the reading failed, see JobKey.ERROR
    CANCELLED = "cancelled"  # stopped before its end


class JobKey(StrEnum):
    """Keys of the JSON responses of /jobs"""

    JOB_ID = "job_id"
    STATUS = "status"  # a JobStatus, in every response
    ERROR = "error"  # with JobStatus.ERROR: {"type", "message"}, see error_payload()
    BATCH = "batch"  # number of the batch, from 1
    COLUMNS = "columns"  # names of the columns, in the first batch only
    ROWS = "rows"
    LAST = "last"  # whether no batch follows


@dataclass(frozen=True)
class Batch:
    """Batch `n` of a result, numbered from 1, as sent by /jobs/{id}/batches/{n}. Its rows hold the SQL values, the
    columns come with the first batch only"""

    n: int
    rows: Sequence[Sequence[Any]]
    last: bool  # whether no batch follows
    columns: Sequence[str] | None = None

    def encode(self) -> bytes:
        """JSON response carrying this batch, encoded once to be sent again as is if asked again.
        Raises TypeError for a value JSON can't encode."""
        body: dict[str, Any] = {
            JobKey.STATUS: JobStatus.OK,
            JobKey.BATCH: self.n,
            JobKey.ROWS: [[encode_row_value(value) for value in row] for row in self.rows],
            JobKey.LAST: self.last,
        }
        if self.columns is not None:
            body[JobKey.COLUMNS] = list(self.columns)

        return json.dumps(body, separators=(",", ":")).encode()

    @classmethod
    def decode(cls, body: dict[str, Any]) -> Batch:
        """Reverses encode(), from the decoded JSON of a JobStatus.OK response.
        Raises KeyError or TypeError for a malformed one."""
        return cls(
            n=int(body[JobKey.BATCH]),
            rows=[tuple(decode_row_value(value) for value in row) for row in body[JobKey.ROWS]],
            last=bool(body[JobKey.LAST]),
            columns=body.get(JobKey.COLUMNS),
        )


# ------------------------------------------------------------------------------------------
# SSO sign-in errors
# ------------------------------------------------------------------------------------------
class SsoErrorCode(StrEnum):
    """Why a SSO sign-in failed, sent by Pytre_Server in the `error` query parameter of its redirects to the login
    page of the web administration, or to the loopback listener of the client, instead of a message: a free text
    could be chosen by anyone crafting a link.
    Each side shows its own message for a code, the detail stays in the server logs."""

    DENIED = "denied"  # refused by the identity provider: consent denied, account blocked there...
    EXPIRED = "expired"  # unknown, expired or already used sign-in state
    UNAVAILABLE = "unavailable"  # SSO not configured, or identity provider unreachable
    FAILED = "failed"  # any other failure: code exchange, invalid ID token, account not linked...
    NOT_ADMIN = "not_admin"  # a valid account without admin rights, web administration only
