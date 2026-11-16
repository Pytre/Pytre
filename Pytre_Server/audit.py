"""
Audit log: the security events (sign-ins, changes made by administrators), each with its actor, the client address,
its target and what changed, to be able to know "who did what, when and from where".

Events are logged to the "Pytre.audit" logger, which propagates to "Pytre": they also reach the main log file, the
terminal and the web administration logs page, as text. setup_audit_file_logging() (logs.py) adds a file of these
events only, one JSON object per line, for a SIEM agent to collect.
"""

import json
import logging
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from Pytre_Core.users import AuthType, User
from Pytre_Core.utils import LOGGER_ROOT_NAME


Snapshot = dict[str, Any]  # the fields of a record the audit log follows, such as User.audit_fields() gives

AUDIT_LOGGER_NAME = f"{LOGGER_ROOT_NAME}.audit"
MAX_VALUE_LEN = 200  # characters kept of a value sent by the client
SECRET_CHANGED = "changed"  # shown for a changed secret, instead of its values


def iso_time(created: float) -> str:
    """Time of a log record in ISO 8601, local with its UTC offset and milliseconds: 2026-11-16T14:32:05.123+01:00.
    The format SIEM agents read without any parsing rule, shared by the log and audit files."""
    return datetime.fromtimestamp(created).astimezone().isoformat(timespec="milliseconds")


def shorten(value: object, max_len: int = MAX_VALUE_LEN) -> str:
    """`value` as text, cut to `max_len` characters and ending with "…" when cut, so a value sent by the client
    (a username...) can't bloat the logs. Line breaks are left to the log formatters."""
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def changes_between(before: dict[str, Any], after: dict[str, Any], secrets: Iterable[str] = ()) -> dict[str, Any]:
    """The fields whose value differs from `before` to `after`, as {field: [before, after]}, in the order of `before`
    then of the fields only in `after`. A field missing on one side is None there. Text values are shortened.
    A secret field only shows SECRET_CHANGED: its values are never logged."""
    secret_fields = set(secrets)
    fields = list(before) + [field for field in after if field not in before]

    diff: dict[str, Any] = {}
    for field in fields:
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        diff[field] = SECRET_CHANGED if field in secret_fields else [_shortened(old), _shortened(new)]
    return diff


def sign_in_failure(user: User, *, admin_only: bool = False) -> str:
    """Why a password sign-in of `user` failed, for the logs only: the client always gets the same generic error,
    so that it can't tell an unknown account from a wrong password."""
    if not user.exists:
        return "unknown account"
    if user.auth_type is AuthType.NONE:
        return "disabled account"
    if user.auth_type is not AuthType.BASIC:
        return f"{user.auth_type} account, without password"
    if admin_only and not user.admin:
        return "not an administrator"
    return "wrong password"


def audit(
    event: str,
    ip: str | None,
    message: str,
    *,
    actor: str | None = None,
    target: str | None = None,
    changes: dict[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    """Logs a security event. `event` names it for a SIEM ("admin.login", "user.update"...) and `message` says it in
    words, with the values sent by the client already passed through shorten(). The client address and the changes
    (see changes_between()) are appended to the message, and every field is also passed apart, for the JSON audit
    file."""
    address = shorten(ip) if ip else None
    text = f"{message} from {address or 'an unknown address'}"
    if changes:
        text = f"{text}: {_describe_changes(changes)}"

    fields = {
        "event": event,
        "actor": _shortened(actor),
        "ip": address,
        "target": _shortened(target),
        "changes": changes or None,
    }
    logging.getLogger(AUDIT_LOGGER_NAME).log(level, text, extra={"audit": fields})


@dataclass(frozen=True)
class AuditedRecords:
    """How the audit log tells the changes an admin makes to the records of one kind: users, servers..."""

    event: str  # prefix of the events: "user" gives "user.create", "user.update" and "user.delete"
    label: str  # starts the messages: "User 'bob' created by admin 'alice'"
    name_field: str  # field of the snapshots naming the record
    secrets: tuple[str, ...] = ()  # fields whose values are never logged, see changes_between()
    warns: Callable[[Snapshot | None, Snapshot], bool] = lambda before, after: False  # creation or update to warn of

    def log_change(
        self, ip: str | None, actor: str, before: Snapshot | None, after: Snapshot | None, how: str = ""
    ) -> str | None:
        """Logs the creation (no `before`), the update or the deletion (no `after`) of a record by the admin `actor`,
        from snapshots of its fields taken before and after: with what changed, for a creation the fields it set.
        An update changing nothing isn't logged. A deletion is a warning, as is what `warns` tells. `how` says how
        the change was made, when not through the record's own form. Returns the event logged, None if none."""
        by = f"by admin {actor!r}" + (f" ({how})" if how else "")

        if after is None:
            if before is None:
                return None
            name = before[self.name_field]
            event, verb, changes, level = f"{self.event}.delete", "deleted", None, logging.WARNING
        else:
            name = after[self.name_field]
            if before is None:
                event, verb = f"{self.event}.create", "created"
                changes = changes_between({}, {f: v for f, v in after.items() if self._is_set(v)}, self.secrets)
            else:
                event, verb = f"{self.event}.update", "updated"
                changes = changes_between(before, after, self.secrets)
                if not changes:
                    return None
            level = logging.WARNING if self.warns(before, after) else logging.INFO

        msg = f"{self.label} {shorten(name)!r} {verb} {by}"
        audit(event, ip, msg, actor=actor, target=name, changes=changes, level=level)
        return event

    @staticmethod
    def _is_set(value: Any) -> bool:
        """False for a field left empty, which a creation doesn't list: None, False, or an empty text or collection"""
        if value is None or value is False:
            return False
        return not (isinstance(value, (str, list, tuple, set, frozenset, dict)) and not value)

    def log_changes(
        self, ip: str | None, actor: str, before_all: dict[str, Snapshot], after_all: dict[str, Snapshot], how: str
    ) -> Counter[str]:
        """Logs each record a bulk action created, updated or deleted, see log_change(), from the snapshots of every
        record by uuid taken before and after it. Returns how many records each event logged."""
        counts: Counter[str] = Counter()
        for uuid in [*before_all, *(uuid for uuid in after_all if uuid not in before_all)]:
            event = self.log_change(ip, actor, before_all.get(uuid), after_all.get(uuid), how)
            if event:
                counts[event] += 1
        return counts


class AuditJsonFormatter(logging.Formatter):
    """One JSON object per audit event and per line: {"time", "level", "event", "actor", "ip", "target", "changes",
    "message"}. JSON escapes line breaks and every non-ASCII character, so no value can spill over another line."""

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "audit", None) or {}
        entry = {
            "time": iso_time(record.created),
            "level": record.levelname,
            "event": fields.get("event"),
            "actor": fields.get("actor"),
            "ip": fields.get("ip"),
            "target": fields.get("target"),
            "changes": fields.get("changes"),
            "message": record.getMessage(),
        }
        return json.dumps(entry, default=str)


def _shortened(value: Any) -> Any:
    """`value` with its texts shortened, inside lists and dicts too. Other values are returned as they are."""
    if isinstance(value, str):
        return shorten(value)
    if isinstance(value, (list, tuple)):
        return [_shortened(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_shortened(item) for item in value), key=str)  # a list: shortening must not merge two items
    if isinstance(value, dict):
        return {key: _shortened(item) for key, item in value.items()}
    return value


def _describe_changes(changes: dict[str, Any]) -> str:
    """Changes as text, such as 'admin false -> true, password changed'. Values are written as JSON, quotes included."""
    described = []
    for field, values in changes.items():
        if values == SECRET_CHANGED:
            described.append(f"{field} {SECRET_CHANGED}")
        else:
            old, new = (json.dumps(value, ensure_ascii=False, default=str) for value in values)
            described.append(f"{field} {old} -> {new}")
    return ", ".join(described)
