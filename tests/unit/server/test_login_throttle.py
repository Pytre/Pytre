"""
Tests for Pytre_Server/login_throttle.py
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from Pytre_Server.login_throttle import (
    IP_MAX_ATTEMPTS,
    LOGIN_THROTTLE_WINDOW,
    USER_MAX_ATTEMPTS,
    LoginThrottle,
    _FailureCounter,
)

pytestmark = pytest.mark.unit

HOST = "10.0.0.1"
OTHER_HOST = "10.0.0.2"


@pytest.fixture
def throttle() -> LoginThrottle:
    return LoginThrottle()


def _expired() -> datetime:
    return datetime.now(timezone.utc) - LOGIN_THROTTLE_WINDOW - timedelta(minutes=1)


# ================================================================================
# Per username
# ================================================================================


def test_is_throttled_false_when_no_attempts(throttle):
    assert throttle.is_throttled("alice", HOST) is False


def test_is_throttled_true_after_max_attempts(throttle):
    for _ in range(USER_MAX_ATTEMPTS):
        throttle.record_failure("alice", HOST)

    assert throttle.is_throttled("alice", HOST) is True


def test_is_throttled_false_below_max_attempts(throttle):
    for _ in range(USER_MAX_ATTEMPTS - 1):
        throttle.record_failure("alice", HOST)

    assert throttle.is_throttled("alice", HOST) is False


def test_username_throttle_applies_from_any_address(throttle):
    for _ in range(USER_MAX_ATTEMPTS):
        throttle.record_failure("alice", HOST)

    assert throttle.is_throttled("alice", OTHER_HOST) is True


def test_username_throttle_ignores_the_case(throttle):
    """A brute-force attempt shouldn't be able to dodge the limit just by varying the
    username's case across attempts."""
    for _ in range(USER_MAX_ATTEMPTS):
        throttle.record_failure("Alice", HOST)

    assert throttle.is_throttled("ALICE", HOST) is True


def test_record_success_forgets_the_failures_of_the_username(throttle):
    for _ in range(USER_MAX_ATTEMPTS):
        throttle.record_failure("alice", HOST)

    throttle.record_success("alice", HOST)

    assert throttle.is_throttled("alice", HOST) is False


def test_username_failures_outside_the_window_are_pruned(throttle):
    throttle.user.failed_login["alice"] = [_expired()] * USER_MAX_ATTEMPTS

    assert throttle.is_throttled("alice", HOST) is False
    assert "alice" not in throttle.user.failed_login


def test_username_throttle_logs_once_when_threshold_is_reached(throttle, caplog):
    with caplog.at_level(logging.INFO):
        for _ in range(USER_MAX_ATTEMPTS - 1):
            throttle.record_failure("alice", HOST)
        assert not any("throttled" in r.message for r in caplog.records)

        throttle.record_failure("alice", HOST)  # the exact crossing attempt

    assert sum("throttled" in r.message for r in caplog.records) == 1


def test_username_throttle_does_not_relog_past_the_threshold(throttle, caplog):
    """Regression guard: logging inside is_throttled() (>=) instead of record_failure() (==) would fire
    again on every call once past the threshold, even though in normal use the login routes never
    call record_failure() again once a user is already throttled."""
    with caplog.at_level(logging.INFO):
        for _ in range(USER_MAX_ATTEMPTS):
            throttle.record_failure("alice", HOST)
        throttle.record_failure("alice", HOST)  # simulates a misuse past the threshold

    assert sum("throttled" in r.message for r in caplog.records) == 1


def test_different_usernames_have_independent_throttles(throttle):
    for _ in range(USER_MAX_ATTEMPTS):
        throttle.record_failure("alice", HOST)

    assert throttle.is_throttled("bob", HOST) is False


def test_stalest_username_is_forgotten_beyond_max_tracked(throttle):
    throttle.user.max_tracked = 3

    for username in ("alice", "bob", "carol", "dave"):
        throttle.record_failure(username, HOST)

    assert list(throttle.user.failed_login) == ["bob", "carol", "dave"]


# ================================================================================
# Per client address
# ================================================================================


def _fail_with_distinct_usernames(throttle: LoginThrottle, host: str | None, count: int) -> None:
    """`count` failed logins from `host`, each for another username - never enough to throttle a username."""
    for i in range(count):
        throttle.record_failure(f"user{i}", host)


def test_address_is_throttled_after_max_failures_whatever_the_usernames(throttle):
    _fail_with_distinct_usernames(throttle, HOST, IP_MAX_ATTEMPTS)

    assert throttle.is_throttled("someone-else", HOST) is True


def test_address_is_not_throttled_below_max_failures(throttle):
    _fail_with_distinct_usernames(throttle, HOST, IP_MAX_ATTEMPTS - 1)

    assert throttle.is_throttled("someone-else", HOST) is False


def test_address_throttle_does_not_affect_other_addresses(throttle):
    _fail_with_distinct_usernames(throttle, HOST, IP_MAX_ATTEMPTS)

    assert throttle.is_throttled("someone-else", OTHER_HOST) is False


def test_successful_login_does_not_reset_the_address_count(throttle):
    """A valid account must not let an attacker restart the count of their address."""
    _fail_with_distinct_usernames(throttle, HOST, IP_MAX_ATTEMPTS)

    throttle.record_success("user0", HOST)

    assert throttle.is_throttled("user0", HOST) is True


def test_address_failures_outside_the_window_are_pruned(throttle):
    throttle.ip.failed_login[HOST] = [_expired()] * IP_MAX_ATTEMPTS

    assert throttle.is_throttled("someone-else", HOST) is False
    assert HOST not in throttle.ip.failed_login


def test_ipv6_addresses_of_one_64_network_share_their_count(throttle):
    """An attacker usually gets a whole /64: changing address within it must not restart the count."""
    for i in range(IP_MAX_ATTEMPTS):
        throttle.record_failure(f"user{i}", f"2001:db8:1:2::{i + 1:x}")

    assert throttle.is_throttled("someone-else", "2001:db8:1:2:ffff::1") is True
    assert throttle.is_throttled("someone-else", "2001:db8:1:3::1") is False


@pytest.mark.parametrize(
    ("host", "key"),
    [
        ("10.0.0.5", "10.0.0.5"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("::ffff:10.0.0.5", "10.0.0.5"),
        ("fe80::1%eth0", "fe80::/64"),
        ("testclient", "testclient"),
        ("", "unknown"),
        (None, "unknown"),
    ],
    ids=["ipv4", "ipv6", "ipv4-mapped", "ipv6-scoped", "not-an-address", "empty", "none"],
)
def test_ip_key(host, key):
    assert LoginThrottle._ip_key(host) == key


def test_address_throttle_logs_once_as_a_warning_naming_the_address(throttle, caplog):
    with caplog.at_level(logging.INFO):
        _fail_with_distinct_usernames(throttle, HOST, IP_MAX_ATTEMPTS + 1)

    address_records = [r for r in caplog.records if f"Address '{HOST}'" in r.message]
    assert len(address_records) == 1
    assert address_records[0].levelno == logging.WARNING


def test_stalest_address_is_forgotten_beyond_max_tracked(throttle):
    throttle.ip.max_tracked = 3

    for host in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        throttle.record_failure("alice", host)
    throttle.record_failure("alice", "10.0.0.1")  # failed again: no longer the stalest
    throttle.record_failure("alice", "10.0.0.4")

    assert list(throttle.ip.failed_login) == ["10.0.0.3", "10.0.0.1", "10.0.0.4"]


def test_checking_an_address_does_not_spare_it_from_being_forgotten(throttle):
    """Only a failure counts as activity: an attacker can't keep an address tracked (or untracked) by just asking."""
    throttle.ip.max_tracked = 3

    for host in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        throttle.record_failure("alice", host)
    throttle.is_throttled("alice", "10.0.0.1")
    throttle.record_failure("alice", "10.0.0.4")

    assert "10.0.0.1" not in throttle.ip.failed_login


# ================================================================================
# _FailureCounter
# ================================================================================


def _counter(max_attempts: int = 3, max_tracked: int = 3) -> _FailureCounter:
    return _FailureCounter("Key", max_attempts=max_attempts, max_tracked=max_tracked)


def test_failure_counter_never_tracks_more_keys_than_max_tracked():
    """Even by one: a new key is stored first, then the stalest ones are forgotten."""
    counter = _counter(max_tracked=3)

    for i in range(10):
        counter.record_failing(f"key{i}")
        assert len(counter.failed_login) <= 3

    assert list(counter.failed_login) == ["key7", "key8", "key9"]


def test_failure_counter_removes_a_key_once_its_failures_have_expired():
    counter = _counter()
    counter.failed_login["key"] = [datetime.now(timezone.utc) - counter.window - timedelta(minutes=1)]

    assert counter.is_throttled("key") is False
    assert "key" not in counter.failed_login


def test_failure_counter_keeps_only_the_failures_inside_the_window():
    counter = _counter(max_attempts=3)
    now = datetime.now(timezone.utc)
    counter.failed_login["key"] = [now - counter.window - timedelta(minutes=1), now, now]

    assert counter.is_throttled("key") is False  # 2 recent failures out of 3
    assert len(counter.failed_login["key"]) == 2


def test_failure_counter_forget_failings_unknown_key_is_a_noop():
    counter = _counter()

    counter.forget_failings("unknown")

    assert counter.failed_login == {}


def test_failure_counter_logs_with_its_label_and_level(caplog):
    counter = _FailureCounter("Address", max_attempts=2, max_tracked=3, log_level=logging.WARNING)

    with caplog.at_level(logging.INFO):
        counter.record_failing("10.0.0.1")
        counter.record_failing("10.0.0.1")

    records = [r for r in caplog.records if "throttled" in r.message]
    assert [r.message for r in records] == ["Address '10.0.0.1' has been throttled, too many failed login attempts"]
    assert records[0].levelno == logging.WARNING
