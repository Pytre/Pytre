"""
Tests for Pytre_Server/audit.py - the audit events, their text in the logs and their JSON in the audit file.
"""

import json
import logging
import re
import time
from datetime import datetime

import pytest

from Pytre_Core.utils import LOGGER_ROOT_NAME
from Pytre_Server.audit import (
    AUDIT_LOGGER_NAME,
    MAX_VALUE_LEN,
    SECRET_CHANGED,
    AuditedRecords,
    AuditJsonFormatter,
    audit,
    changes_between,
    iso_time,
    shorten,
    sign_in_failure,
)
from Pytre_Core.users import AuthType, User
from tests.conftest import audit_fields, audit_records


pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("pristine_pytre_logger", "pristine_audit_logger")]  # see tests/conftest.py


def logged_audit(caplog, *args, **kwargs) -> logging.LogRecord:
    """The single record logged by audit(*args, **kwargs)"""
    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        audit(*args, **kwargs)

    records = audit_records(caplog)
    assert len(records) == 1
    return records[0]


# ================================================================================
# iso_time
# ================================================================================


def test_iso_time_writes_an_iso_date_with_milliseconds_and_the_utc_offset():
    created = datetime(2026, 11, 16, 14, 32, 5, 123456).timestamp()

    written = iso_time(created)

    assert re.fullmatch(r"2026-11-16T14:32:05\.123[+-]\d{2}:\d{2}", written)
    assert datetime.fromisoformat(written).timestamp() == pytest.approx(created, abs=0.001)


@pytest.mark.parametrize("month", [1, 7], ids=["winter", "summer"])
def test_iso_time_reads_back_as_the_same_instant_in_winter_and_summer(month):
    """On any system: with daylight saving time, a single offset for both dates would shift one of them by an hour.
    Proves something only where the local time zone has daylight saving time (not on a UTC machine)."""
    created = datetime(2026, month, 15, 12, 0, 0, 250000).timestamp()

    assert datetime.fromisoformat(iso_time(created)).timestamp() == created


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset() only exists on Unix")
def test_iso_time_writes_the_utc_offset_in_force_at_that_time(monkeypatch):
    """Around a change of daylight saving time, each date keeps its own offset - in a time zone with daylight saving
    time whatever the system's, e.g. on a UTC CI runner."""
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        if time.tzname != ("CET", "CEST"):  # without the time zone database, the system silently falls back to UTC
            pytest.skip("Europe/Paris time zone not installed")
        winter = iso_time(datetime(2026, 1, 15, 12, 0).timestamp())
        summer = iso_time(datetime(2026, 7, 15, 12, 0).timestamp())
    finally:
        monkeypatch.undo()
        time.tzset()

    assert winter == "2026-01-15T12:00:00.000+01:00"
    assert summer == "2026-07-15T12:00:00.000+02:00"


# ================================================================================
# shorten
# ================================================================================


def test_shorten_keeps_a_short_value():
    assert shorten("alice") == "alice"


def test_shorten_keeps_a_value_of_exactly_the_maximum_length():
    value = "a" * MAX_VALUE_LEN

    assert shorten(value) == value


def test_shorten_cuts_a_long_value_to_the_maximum_length_ending_with_an_ellipsis():
    shortened = shorten("a" * (MAX_VALUE_LEN + 50))

    assert len(shortened) == MAX_VALUE_LEN
    assert shortened == "a" * (MAX_VALUE_LEN - 1) + "…"


def test_shorten_takes_a_custom_maximum_length():
    assert shorten("abcdefghij", max_len=5) == "abcd…"


def test_shorten_turns_any_value_into_text():
    assert shorten(42) == "42"
    assert shorten(None) == "None"


def test_shorten_leaves_line_breaks_to_the_log_formatters():
    assert shorten("a\nb") == "a\nb"


# ================================================================================
# changes_between
# ================================================================================


def test_changes_between_is_empty_when_nothing_changed():
    fields = {"username": "alice", "admin": False, "groups": ["finance"]}

    assert changes_between(fields, dict(fields)) == {}


def test_changes_between_gives_the_values_before_and_after():
    before = {"username": "alice", "admin": False}
    after = {"username": "alice", "admin": True}

    assert changes_between(before, after) == {"admin": [False, True]}


def test_changes_between_compares_lists():
    before = {"groups": ["finance"]}
    after = {"groups": ["finance", "hr"]}

    assert changes_between(before, after) == {"groups": [["finance"], ["finance", "hr"]]}


def test_changes_between_gives_none_for_a_field_missing_on_one_side():
    before = {"removed": "x"}
    after = {"added": "y"}

    assert changes_between(before, after) == {"removed": ["x", None], "added": [None, "y"]}


def test_changes_between_keeps_the_order_of_before_then_of_the_new_fields():
    before = {"b": 1, "a": 1}
    after = {"new": 1, "a": 2, "b": 2}

    assert list(changes_between(before, after)) == ["b", "a", "new"]


def test_changes_between_never_gives_the_values_of_a_secret():
    before = {"password_hash": "$argon2id$old", "admin": False}
    after = {"password_hash": "$argon2id$new", "admin": False}

    diff = changes_between(before, after, secrets=["password_hash"])

    assert diff == {"password_hash": SECRET_CHANGED}
    assert "argon2" not in json.dumps(diff)


def test_changes_between_leaves_out_an_unchanged_secret():
    fields = {"password_hash": "$argon2id$same"}

    assert changes_between(fields, dict(fields), secrets=["password_hash"]) == {}


def test_changes_between_shortens_text_values_including_inside_lists_and_dicts():
    long = "x" * (MAX_VALUE_LEN + 10)
    short = shorten(long)

    diff = changes_between(
        {"name": "a", "groups": ["a"], "attribs": {"site": "a"}},
        {"name": long, "groups": [long], "attribs": {"site": long}},
    )

    assert diff == {
        "name": ["a", short],
        "groups": [["a"], [short]],
        "attribs": [{"site": "a"}, {"site": short}],
    }


@pytest.mark.parametrize("kind", [set, frozenset])
def test_changes_between_gives_a_set_as_a_sorted_list(kind):
    diff = changes_between({"groups": kind({"sales"})}, {"groups": kind({"hr", "finance", "sales"})})

    assert diff == {"groups": [["sales"], ["finance", "hr", "sales"]]}


def test_changes_between_ignores_a_set_whose_items_only_changed_order():
    """Unlike two lists, which differ as soon as their order does."""
    assert changes_between({"groups": {"finance", "hr"}}, {"groups": {"hr", "finance"}}) == {}
    assert changes_between({"groups": ["finance", "hr"]}, {"groups": ["hr", "finance"]}) != {}


def test_changes_between_keeps_two_long_set_items_that_only_differ_once_shortened():
    """Shortened, both items are the same text: a set would merge them into one."""
    first, second = "x" * MAX_VALUE_LEN + "1", "x" * MAX_VALUE_LEN + "2"

    diff = changes_between({"groups": set()}, {"groups": {first, second}})

    assert diff == {"groups": [[], [shorten(first), shorten(second)]]}


def test_changes_between_sorts_a_set_of_mixed_types_by_their_text():
    diff = changes_between({"ids": set()}, {"ids": {10, "9", 2}})

    assert diff == {"ids": [[], [10, 2, "9"]]}


# ================================================================================
# sign_in_failure
# ================================================================================


def _user(exists: bool = True, auth_type: AuthType = AuthType.BASIC, admin: bool = False) -> User:
    user = User(users=None, detect_user=False)
    user.exists = exists
    user.auth_type = auth_type
    user.admin = admin
    return user


@pytest.mark.parametrize(
    ("user", "admin_only", "reason"),
    [
        (_user(exists=False), False, "unknown account"),
        (_user(exists=False, admin=True), True, "unknown account"),
        (_user(auth_type=AuthType.NONE), False, "disabled account"),
        (_user(auth_type=AuthType.NONE, admin=True), True, "disabled account"),
        (_user(auth_type=AuthType.OIDC), False, "OIDC account, without password"),
        (_user(auth_type=AuthType.OIDC, admin=True), True, "OIDC account, without password"),
        (_user(), False, "wrong password"),
        (_user(), True, "not an administrator"),
        (_user(admin=True), False, "wrong password"),
        (_user(admin=True), True, "wrong password"),
    ],
    ids=[
        "unknown",
        "unknown-admin",
        "disabled",
        "disabled-admin",
        "oidc",
        "oidc-admin",
        "basic",
        "basic-not-admin",
        "basic-admin-client",
        "basic-admin",
    ],
)
def test_sign_in_failure_gives_the_reason_of_the_failure(user, admin_only, reason):
    assert sign_in_failure(user, admin_only=admin_only) == reason


# ================================================================================
# audit
# ================================================================================


def test_audit_logs_to_the_audit_logger_at_the_given_level(caplog):
    record = logged_audit(caplog, "admin.login", "10.0.0.5", "Failed admin login", level=logging.WARNING)

    assert record.name == AUDIT_LOGGER_NAME
    assert record.levelno == logging.WARNING


def test_audit_logs_at_info_by_default(caplog):
    record = logged_audit(caplog, "client.login", "10.0.0.5", "User logged in")

    assert record.levelno == logging.INFO


def test_audit_appends_the_client_address_to_the_message(caplog):
    record = logged_audit(caplog, "client.login", "10.0.0.5", "User 'alice' logged in")

    assert record.getMessage() == "User 'alice' logged in from 10.0.0.5"


def test_audit_says_when_the_client_address_is_unknown(caplog):
    record = logged_audit(caplog, "client.login", None, "User 'alice' logged in")

    assert record.getMessage() == "User 'alice' logged in from an unknown address"
    assert audit_fields(record)["ip"] is None


def test_audit_appends_the_changes_to_the_message(caplog):
    changes = changes_between(
        {"admin": False, "display_name": "Alice", "password_hash": "old"},
        {"admin": True, "display_name": "Alice B.", "password_hash": "new"},
        secrets=["password_hash"],
    )

    record = logged_audit(caplog, "user.update", "10.0.0.5", "User 'alice' saved", changes=changes)

    assert record.getMessage() == (
        "User 'alice' saved from 10.0.0.5: "
        'admin false -> true, display_name "Alice" -> "Alice B.", password_hash changed'
    )


def test_audit_writes_a_changed_set_as_a_sorted_json_list_in_the_message(caplog):
    changes = changes_between({"groups": {"sales"}}, {"groups": {"sales", "hr", "finance"}})

    record = logged_audit(caplog, "user.update", "10.0.0.5", "User 'alice' saved", changes=changes)

    assert record.getMessage().endswith(': groups ["sales"] -> ["finance", "hr", "sales"]')


def test_audit_writes_changed_values_as_json_so_a_line_break_stays_escaped(caplog):
    changes = changes_between({"display_name": "Alice"}, {"display_name": "Alice\n2026-11-16 - WARNING - forged"})

    record = logged_audit(caplog, "user.update", "10.0.0.5", "User 'alice' saved", changes=changes)

    assert "\n" not in record.getMessage()
    assert '"Alice\\n2026-11-16 - WARNING - forged"' in record.getMessage()


def test_audit_writes_non_ascii_changed_values_as_they_are(caplog):
    changes = changes_between({"display_name": "Helene"}, {"display_name": "Hélène"})

    record = logged_audit(caplog, "user.update", "10.0.0.5", "User 'helene' saved", changes=changes)

    assert record.getMessage().endswith('display_name "Helene" -> "Hélène"')


def test_audit_adds_nothing_to_the_message_without_changes(caplog):
    record = logged_audit(caplog, "user.update", "10.0.0.5", "User 'alice' saved", changes={})

    assert record.getMessage() == "User 'alice' saved from 10.0.0.5"
    assert audit_fields(record)["changes"] is None


def test_audit_passes_every_field_apart_for_the_audit_file(caplog):
    record = logged_audit(
        caplog,
        "user.update",
        "10.0.0.5",
        "User 'alice' saved",
        actor="bob",
        target="alice",
        changes={"admin": [False, True]},
    )

    assert audit_fields(record) == {
        "event": "user.update",
        "actor": "bob",
        "ip": "10.0.0.5",
        "target": "alice",
        "changes": {"admin": [False, True]},
    }


def test_audit_shortens_the_actor_the_target_and_the_address(caplog):
    long = "x" * (MAX_VALUE_LEN + 10)

    record = logged_audit(caplog, "client.login", long, "Failed login", actor=long, target=long)

    assert audit_fields(record)["actor"] == shorten(long)
    assert audit_fields(record)["target"] == shorten(long)
    assert audit_fields(record)["ip"] == shorten(long)
    assert record.getMessage() == f"Failed login from {shorten(long)}"


def test_audit_events_propagate_to_the_pytre_logger(caplog):
    """The events also reach the main log file, the terminal and the web administration logs page."""
    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        audit("client.login", "10.0.0.5", "User 'alice' logged in")

    assert logging.getLogger(AUDIT_LOGGER_NAME).propagate is True
    assert any(r.getMessage() == "User 'alice' logged in from 10.0.0.5" for r in caplog.records)


# ================================================================================
# AuditedRecords
# ================================================================================

THINGS = AuditedRecords(
    "thing",
    "Thing",
    "name",
    ("secret",),
    warns=lambda before, after: after["flag"] and not (before or {}).get("flag"),
)
IP = "10.0.0.1"


def _thing(**fields) -> dict:
    return {"name": "a", "flag": False, "secret": "s3cret", "size": 1, "tags": set(), **fields}


def logged_changes(caplog, method: str, *args, **kwargs):
    """What THINGS.<method>(*args, **kwargs) returned, and the audit records it logged"""
    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        result = getattr(THINGS, method)(*args, **kwargs)
    return result, audit_records(caplog)


def test_audited_records_log_a_creation_with_the_fields_it_sets(caplog):
    event, [record] = logged_changes(caplog, "log_change", IP, "alice", None, _thing(tags={"x"}, size=0))

    assert event == "thing.create"
    assert record.levelno == logging.INFO
    assert record.message == (
        f'Thing \'a\' created by admin \'alice\' from {IP}: name null -> "a", secret changed, size null -> 0, '
        'tags null -> ["x"]'
    )
    assert audit_fields(record)["actor"] == "alice"
    assert audit_fields(record)["target"] == "a"


def test_audited_records_log_an_update_with_what_changed_only(caplog):
    event, [record] = logged_changes(caplog, "log_change", IP, "alice", _thing(), _thing(size=2))

    assert event == "thing.update"
    assert record.levelno == logging.INFO
    assert record.message == f"Thing 'a' updated by admin 'alice' from {IP}: size 1 -> 2"
    assert audit_fields(record)["changes"] == {"size": [1, 2]}


def test_audited_records_log_nothing_for_an_update_changing_nothing(caplog):
    event, records = logged_changes(caplog, "log_change", IP, "alice", _thing(), _thing())

    assert event is None
    assert records == []


def test_audited_records_never_log_the_value_of_a_secret(caplog):
    _event, [record] = logged_changes(caplog, "log_change", IP, "alice", _thing(), _thing(secret="n3w"))

    assert audit_fields(record)["changes"] == {"secret": SECRET_CHANGED}
    assert "s3cret" not in record.message
    assert "n3w" not in record.message


def test_audited_records_log_a_deletion_as_a_warning_without_its_fields(caplog):
    event, [record] = logged_changes(caplog, "log_change", IP, "alice", _thing(), None)

    assert event == "thing.delete"
    assert record.levelno == logging.WARNING
    assert record.message == f"Thing 'a' deleted by admin 'alice' from {IP}"
    assert audit_fields(record)["changes"] is None


def test_audited_records_log_nothing_without_a_record_before_or_after(caplog):
    event, records = logged_changes(caplog, "log_change", IP, "alice", None, None)

    assert event is None
    assert records == []


@pytest.mark.parametrize(
    ("before", "after", "level"),
    [
        (None, _thing(flag=True), logging.WARNING),
        (_thing(), _thing(flag=True), logging.WARNING),
        (_thing(flag=True), _thing(flag=True, size=2), logging.INFO),
        (_thing(flag=True), _thing(flag=False), logging.INFO),
    ],
    ids=["created-flagged", "flagged", "already-flagged", "unflagged"],
)
def test_audited_records_log_what_warns_tells_as_a_warning(caplog, before, after, level):
    _event, [record] = logged_changes(caplog, "log_change", IP, "alice", before, after)

    assert record.levelno == level


def test_audited_records_say_how_the_change_was_made(caplog):
    _event, [record] = logged_changes(caplog, "log_change", IP, "alice", _thing(), _thing(size=2), "CSV import")

    assert record.message.startswith(f"Thing 'a' updated by admin 'alice' (CSV import) from {IP}: ")


def test_audited_records_shorten_a_long_name(caplog):
    name = "n" * (MAX_VALUE_LEN + 50)

    _event, [record] = logged_changes(caplog, "log_change", IP, "alice", _thing(name=name), None)

    assert record.message == f"Thing {shorten(name)!r} deleted by admin 'alice' from {IP}"
    assert audit_fields(record)["target"] == shorten(name)


def test_audited_records_log_each_record_a_bulk_action_changed(caplog):
    before_all = {"u1": _thing(name="a"), "u2": _thing(name="b"), "u3": _thing(name="c")}
    after_all = {"u1": _thing(name="a", size=2), "u3": _thing(name="c"), "u4": _thing(name="d")}

    counts, records = logged_changes(caplog, "log_changes", IP, "alice", before_all, after_all, "CSV import")

    assert [(audit_fields(r)["event"], audit_fields(r)["target"]) for r in records] == [
        ("thing.update", "a"),
        ("thing.delete", "b"),
        ("thing.create", "d"),
    ]  # those before first, in their order, then the new ones - the unchanged one left out
    assert all("(CSV import)" in r.message for r in records)
    assert counts == {"thing.update": 1, "thing.delete": 1, "thing.create": 1}


@pytest.mark.parametrize(
    ("value", "is_set"),
    [
        (None, False),
        (False, False),
        ("", False),
        ([], False),
        (set(), False),
        ({}, False),
        (0, True),
        (True, True),
        ("x", True),
        ({"x"}, True),
    ],
)
def test_audited_records_list_only_the_fields_a_creation_sets(value, is_set):
    assert AuditedRecords._is_set(value) is is_set


# ================================================================================
# AuditJsonFormatter
# ================================================================================


def formatted(caplog, *args, **kwargs) -> str:
    return AuditJsonFormatter().format(logged_audit(caplog, *args, **kwargs))


def test_audit_json_formatter_writes_one_json_object_with_every_field_in_order(caplog):
    line = formatted(
        caplog,
        "user.update",
        "10.0.0.5",
        "User 'alice' saved",
        actor="bob",
        target="alice",
        changes={"admin": [False, True]},
        level=logging.WARNING,
    )

    entry = json.loads(line)
    assert list(entry) == ["time", "level", "event", "actor", "ip", "target", "changes", "message"]
    assert {key: value for key, value in entry.items() if key != "time"} == {
        "level": "WARNING",
        "event": "user.update",
        "actor": "bob",
        "ip": "10.0.0.5",
        "target": "alice",
        "changes": {"admin": [False, True]},
        "message": "User 'alice' saved from 10.0.0.5: admin false -> true",
    }


def test_audit_json_formatter_writes_the_time_with_its_utc_offset(caplog):
    entry = json.loads(formatted(caplog, "client.login", "10.0.0.5", "User logged in"))

    time = datetime.fromisoformat(entry["time"])
    assert time.tzinfo is not None
    assert abs((datetime.now().astimezone() - time).total_seconds()) < 60


def test_audit_json_formatter_keeps_every_event_on_one_line(caplog):
    line = formatted(
        caplog,
        "client.login",
        "10.0.0.5",
        "Failed login for 'alice\r\n2026-11-16 - INFO - forged'",
        target="alice forged",
    )

    assert line.splitlines() == [line]
    assert json.loads(line)["target"] == "alice forged"


def test_audit_json_formatter_escapes_non_ascii_characters(caplog):
    line = formatted(caplog, "client.login", "10.0.0.5", "User 'hélène' logged in", target="hélène")

    assert line.isascii()
    assert json.loads(line)["target"] == "hélène"


def test_audit_json_formatter_gives_null_fields_for_a_record_not_logged_by_audit():
    record = logging.LogRecord(AUDIT_LOGGER_NAME, logging.INFO, __file__, 1, "plain message", None, None)

    entry = json.loads(AuditJsonFormatter().format(record))

    assert entry["message"] == "plain message"
    assert [entry[key] for key in ("event", "actor", "ip", "target", "changes")] == [None] * 5


def test_audit_json_formatter_writes_a_changed_set_as_a_json_array(caplog):
    """Not as the text of a Python set ("{'hr', 'finance'}"), which a SIEM couldn't read."""
    changes = changes_between({"groups": set()}, {"groups": {"hr", "finance"}})

    line = formatted(caplog, "user.update", "10.0.0.5", "User 'alice' saved", changes=changes)

    assert json.loads(line)["changes"] == {"groups": [[], ["finance", "hr"]]}


def test_audit_json_formatter_writes_values_json_does_not_know_as_text(caplog):
    when = datetime(2026, 11, 16, 10, 0)

    line = formatted(caplog, "settings.update", "127.0.0.1", "Settings changed", changes={"since": [None, when]})

    assert json.loads(line)["changes"] == {"since": [None, str(when)]}
