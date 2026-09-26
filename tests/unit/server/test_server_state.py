"""
Tests for Pytre_Server/server_state.py
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from Pytre_Server.server_state import (
    LOGIN_THROTTLE_WINDOW_MINUTES,
    MAX_LOGIN_ATTEMPTS,
    ServerState,
    _DriverEntry,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server_state() -> ServerState:
    return ServerState(app_ctx=Mock(), query_catalog=Mock(), chunk_size=2000, max_concurrency_per_user=4)


@pytest.mark.unit
def test_get_user_semaphore_creates_lazily(server_state):
    assert "alice" not in server_state.resources.user_semaphores

    semaphore = server_state.resources._get_user_semaphore(server_state.max_concurrency_per_user, "alice")

    assert "alice" in server_state.resources.user_semaphores
    assert isinstance(semaphore, asyncio.Semaphore)


@pytest.mark.unit
def test_get_user_semaphore_returns_same_instance_for_same_user(server_state):
    first = server_state.resources._get_user_semaphore(server_state.max_concurrency_per_user, "alice")
    second = server_state.resources._get_user_semaphore(server_state.max_concurrency_per_user, "alice")

    assert first is second


@pytest.mark.unit
def test_get_user_semaphore_different_users_get_different_semaphores(server_state):
    alice = server_state.resources._get_user_semaphore(server_state.max_concurrency_per_user, "alice")
    bob = server_state.resources._get_user_semaphore(server_state.max_concurrency_per_user, "bob")

    assert alice is not bob


@pytest.mark.unit
def test_ignore_hash_until_defaults_to_empty(server_state):
    """ignore_hash_until starts empty - an empty dict means /execute checks the hash for
    every query."""
    assert server_state.resources.ignore_hash_until == {}


# ================================================================================
# Login throttle
# ================================================================================


@pytest.mark.unit
def test_is_login_throttled_false_when_no_attempts(server_state):
    assert server_state.resources.is_login_throttled("alice") is False


@pytest.mark.unit
def test_is_login_throttled_true_after_max_attempts(server_state):
    for _ in range(MAX_LOGIN_ATTEMPTS):
        server_state.resources.throttle_record_failing("alice")

    assert server_state.resources.is_login_throttled("alice") is True


@pytest.mark.unit
def test_is_login_throttled_false_below_max_attempts(server_state):
    for _ in range(MAX_LOGIN_ATTEMPTS - 1):
        server_state.resources.throttle_record_failing("alice")

    assert server_state.resources.is_login_throttled("alice") is False


@pytest.mark.unit
def test_throttle_normalizes_username_case(server_state):
    """A brute-force attempt shouldn't be able to dodge the limit just by varying the
    username's case across attempts."""
    for _ in range(MAX_LOGIN_ATTEMPTS):
        server_state.resources.throttle_record_failing("Alice")

    assert server_state.resources.is_login_throttled("ALICE") is True


@pytest.mark.unit
def test_throttle_reset_failings_clears_attempts(server_state):
    for _ in range(MAX_LOGIN_ATTEMPTS):
        server_state.resources.throttle_record_failing("alice")

    server_state.resources.throttle_reset_failings("alice")

    assert server_state.resources.is_login_throttled("alice") is False


@pytest.mark.unit
def test_is_login_throttled_prunes_attempts_outside_the_window(server_state):
    old_time = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_THROTTLE_WINDOW_MINUTES + 1)
    server_state.resources.throttle_failed_login["alice"] = [old_time] * MAX_LOGIN_ATTEMPTS

    assert server_state.resources.is_login_throttled("alice") is False
    assert "alice" not in server_state.resources.throttle_failed_login


@pytest.mark.unit
def test_throttle_record_failing_logs_once_when_threshold_is_reached(server_state, caplog):
    with caplog.at_level(logging.INFO):
        for _ in range(MAX_LOGIN_ATTEMPTS - 1):
            server_state.resources.throttle_record_failing("alice")
        assert not any("throttled" in r.message for r in caplog.records)

        server_state.resources.throttle_record_failing("alice")  # the exact crossing attempt

    assert sum("throttled" in r.message for r in caplog.records) == 1


@pytest.mark.unit
def test_throttle_record_failing_does_not_relog_past_the_threshold(server_state, caplog):
    """Regression guard: logging inside is_login_throttled() (>=) instead of here (==) would fire
    again on every call once past the threshold, even though in normal use the login routes never
    call throttle_record_failing() again once a user is already throttled."""
    with caplog.at_level(logging.INFO):
        for _ in range(MAX_LOGIN_ATTEMPTS):
            server_state.resources.throttle_record_failing("alice")
        server_state.resources.throttle_record_failing("alice")  # simulates a misuse past the threshold

    assert sum("throttled" in r.message for r in caplog.records) == 1


@pytest.mark.unit
def test_different_usernames_have_independent_throttles(server_state):
    for _ in range(MAX_LOGIN_ATTEMPTS):
        server_state.resources.throttle_record_failing("alice")


# ================================================================================
# Driver caching (use_driver / _get_driver_entry / reset_connections / shutdown_drivers)
# ================================================================================


def _make_fake_driver_factory(monkeypatch) -> list[Mock]:
    """Monkeypatches get_driver() so _get_driver_entry() never touches a real connection.
    Returns the list of fake driver instances created, in construction order.
    Driver construction details (trust_certificate forwarding, host resolution...) are
    fastmssql-specific and tested against get_driver()/FastMssqlDriver directly in
    tests/unit/core/test_query_drivers_async.py, not here."""
    created: list[Mock] = []

    async def fake_get_driver(server):
        driver = Mock()
        driver.connect = AsyncMock()
        driver.disconnect = AsyncMock()
        driver.execute = AsyncMock()
        created.append(driver)
        return driver

    monkeypatch.setattr("Pytre_Server.server_state.get_driver", fake_get_driver)
    return created


def _make_server(server_id: str, host: str = "host") -> Mock:
    return Mock(id=server_id, host=host)


@pytest.mark.unit
def test_driver_entry_defaults_inflight_to_zero():
    entry = _DriverEntry(driver=Mock())

    assert entry.inflight == 0


@pytest.mark.anyio
async def test_get_driver_entry_connects_and_caches(server_state, monkeypatch):
    created = _make_fake_driver_factory(monkeypatch)
    server = _make_server("srv1")

    entry = await server_state.resources._get_driver_entry(server)

    assert entry.driver is created[0]
    created[0].connect.assert_awaited_once()
    assert server_state.resources.driver_entries["srv1"] is entry


@pytest.mark.anyio
async def test_get_driver_entry_reuses_cached_entry(server_state, monkeypatch):
    created = _make_fake_driver_factory(monkeypatch)
    server = _make_server("srv1")

    first = await server_state.resources._get_driver_entry(server)
    second = await server_state.resources._get_driver_entry(server)

    assert first is second
    assert len(created) == 1


@pytest.mark.anyio
async def test_get_driver_entry_different_servers_get_different_entries(server_state, monkeypatch):
    _make_fake_driver_factory(monkeypatch)
    server_a = _make_server("a", host="host-a")
    server_b = _make_server("b", host="host-b")

    entry_a = await server_state.resources._get_driver_entry(server_a)
    entry_b = await server_state.resources._get_driver_entry(server_b)

    assert entry_a is not entry_b


@pytest.mark.anyio
async def test_use_driver_marks_entry_in_flight_for_the_duration(server_state, monkeypatch):
    _make_fake_driver_factory(monkeypatch)
    server = _make_server("srv1")

    async with server_state.resources.use_driver(server) as driver:
        entry = server_state.resources.driver_entries["srv1"]
        assert entry.inflight == 1
        assert driver is entry.driver

    assert entry.inflight == 0


@pytest.mark.anyio
async def test_use_driver_decrements_inflight_even_if_the_caller_raises(server_state, monkeypatch):
    _make_fake_driver_factory(monkeypatch)
    server = _make_server("srv1")

    with pytest.raises(ValueError):
        async with server_state.resources.use_driver(server):
            raise ValueError("boom")

    assert server_state.resources.driver_entries["srv1"].inflight == 0


@pytest.mark.anyio
async def test_use_driver_nested_calls_accumulate_and_unwind(server_state, monkeypatch):
    """Simulates two concurrent /execute calls sharing the same pooled driver."""
    _make_fake_driver_factory(monkeypatch)
    server = _make_server("srv1")

    async with server_state.resources.use_driver(server):
        entry = server_state.resources.driver_entries["srv1"]
        assert entry.inflight == 1
        async with server_state.resources.use_driver(server):
            assert entry.inflight == 2
        assert entry.inflight == 1

    assert entry.inflight == 0


@pytest.mark.anyio
async def test_reset_connections_scoped_detaches_only_the_target_server(server_state, monkeypatch):
    _make_fake_driver_factory(monkeypatch)
    server_a = _make_server("a", host="host-a")
    server_b = _make_server("b", host="host-b")
    await server_state.resources._get_driver_entry(server_a)
    await server_state.resources._get_driver_entry(server_b)

    reset_count = await server_state.resources.reset_connections("a")

    assert reset_count == 1
    assert "a" not in server_state.resources.driver_entries
    assert "b" in server_state.resources.driver_entries


@pytest.mark.anyio
async def test_reset_connections_full_clears_every_driver(server_state, monkeypatch):
    _make_fake_driver_factory(monkeypatch)
    server_a = _make_server("a", host="host-a")
    server_b = _make_server("b", host="host-b")
    await server_state.resources._get_driver_entry(server_a)
    await server_state.resources._get_driver_entry(server_b)

    reset_count = await server_state.resources.reset_connections()

    assert reset_count == 2
    assert server_state.resources.driver_entries == {}


@pytest.mark.anyio
async def test_reset_connections_unknown_server_id_is_a_noop(server_state):
    reset_count = await server_state.resources.reset_connections("does-not-exist")

    assert reset_count == 0
    assert server_state.resources._disconnect_tasks == set()


@pytest.mark.anyio
async def test_reset_connections_schedules_a_disconnect_task_that_completes_once_awaited(server_state, monkeypatch):
    created = _make_fake_driver_factory(monkeypatch)
    server = _make_server("a", host="host-a")
    await server_state.resources._get_driver_entry(server)

    await server_state.resources.reset_connections("a")
    [task] = server_state.resources._disconnect_tasks
    await task

    created[0].disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_disconnect_when_idle_disconnects_immediately_if_already_idle(server_state, monkeypatch):
    driver = Mock()
    driver.disconnect = AsyncMock()
    entry = _DriverEntry(driver=driver, inflight=0)
    sleep_mock = AsyncMock()
    monkeypatch.setattr("Pytre_Server.server_state.asyncio.sleep", sleep_mock)

    await server_state.resources._disconnect_when_idle(entry)

    sleep_mock.assert_not_awaited()
    driver.disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_disconnect_when_idle_waits_until_inflight_reaches_zero(server_state, monkeypatch):
    driver = Mock()
    driver.disconnect = AsyncMock()
    entry = _DriverEntry(driver=driver, inflight=1)

    async def fake_sleep(seconds):
        entry.inflight = 0  # simulate the in-flight call finishing while the loop waits

    monkeypatch.setattr("Pytre_Server.server_state.asyncio.sleep", fake_sleep)

    await server_state.resources._disconnect_when_idle(entry)

    driver.disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_shutdown_drivers_disconnects_every_cached_driver(server_state, monkeypatch):
    created = _make_fake_driver_factory(monkeypatch)
    server_a = _make_server("a", host="host-a")
    server_b = _make_server("b", host="host-b")
    await server_state.resources._get_driver_entry(server_a)
    await server_state.resources._get_driver_entry(server_b)

    await server_state.resources.shutdown_drivers()

    created[0].disconnect.assert_awaited_once()
    created[1].disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_shutdown_drivers_cancels_pending_disconnect_tasks(server_state):
    pending_task = asyncio.create_task(asyncio.sleep(10))
    server_state.resources._disconnect_tasks.add(pending_task)

    await server_state.resources.shutdown_drivers()

    with pytest.raises(asyncio.CancelledError):
        await pending_task
    assert pending_task.cancelled()


# ================================================================================
# OIDC pending login (webui SSO)
# ================================================================================


@pytest.mark.unit
def test_oidc_start_pending_login_returns_a_usable_state_token(server_state):
    state_token = server_state.resources.oidc_start_pending_login("https://idp", "nonce", "verifier", "/admin")

    assert state_token
    assert state_token in server_state.resources.oidc_pending


@pytest.mark.unit
def test_oidc_start_pending_login_generates_distinct_tokens(server_state):
    first = server_state.resources.oidc_start_pending_login("https://idp", "n1", "v1", "/admin")
    second = server_state.resources.oidc_start_pending_login("https://idp", "n2", "v2", "/admin")

    assert first != second
    assert len(server_state.resources.oidc_pending) == 2


@pytest.mark.unit
def test_oidc_pop_pending_login_returns_the_stored_values(server_state):
    state_token = server_state.resources.oidc_start_pending_login("https://idp", "the-nonce", "the-verifier", "/x")

    entry = server_state.resources.oidc_pop_pending_login(state_token)

    assert entry is not None
    assert entry.provider_issuer == "https://idp"
    assert entry.nonce == "the-nonce"
    assert entry.code_verifier == "the-verifier"
    assert entry.next_path == "/x"


@pytest.mark.unit
def test_oidc_pop_pending_login_is_single_use(server_state):
    state_token = server_state.resources.oidc_start_pending_login("https://idp", "n", "v", "/admin")

    first = server_state.resources.oidc_pop_pending_login(state_token)
    second = server_state.resources.oidc_pop_pending_login(state_token)

    assert first is not None
    assert second is None
    assert state_token not in server_state.resources.oidc_pending


@pytest.mark.unit
def test_oidc_pop_pending_login_rejects_an_unknown_state(server_state):
    assert server_state.resources.oidc_pop_pending_login("never-issued") is None


@pytest.mark.unit
def test_oidc_pop_pending_login_rejects_an_expired_entry(server_state):
    """Regression guard: an expired entry must not be usable even though it's still physically in
    the dict until pruned - _oidc_prune_expired() runs lazily, not on a timer."""
    state_token = server_state.resources.oidc_start_pending_login("https://idp", "n", "v", "/admin")
    server_state.resources.oidc_pending[state_token].expires_at = time.monotonic() - 1

    entry = server_state.resources.oidc_pop_pending_login(state_token)

    assert entry is None
    assert state_token not in server_state.resources.oidc_pending  # consumed anyway, not left behind


@pytest.mark.unit
def test_oidc_peek_pending_login_returns_the_stored_values_without_consuming_it(server_state):
    state_token = server_state.resources.oidc_start_pending_login("https://idp", "the-nonce", "the-verifier", "/x")

    entry = server_state.resources.oidc_peek_pending_login(state_token)

    assert entry is not None
    assert entry.provider_issuer == "https://idp"
    assert state_token in server_state.resources.oidc_pending  # still there - peek doesn't consume


@pytest.mark.unit
def test_oidc_peek_pending_login_can_be_called_more_than_once(server_state):
    state_token = server_state.resources.oidc_start_pending_login("https://idp", "n", "v", "/admin")

    first = server_state.resources.oidc_peek_pending_login(state_token)
    second = server_state.resources.oidc_peek_pending_login(state_token)

    assert first is not None
    assert second is not None


@pytest.mark.unit
def test_oidc_peek_pending_login_rejects_an_unknown_state(server_state):
    assert server_state.resources.oidc_peek_pending_login("never-issued") is None


@pytest.mark.unit
def test_oidc_peek_pending_login_rejects_an_expired_entry(server_state):
    state_token = server_state.resources.oidc_start_pending_login("https://idp", "n", "v", "/admin")
    server_state.resources.oidc_pending[state_token].expires_at = time.monotonic() - 1

    assert server_state.resources.oidc_peek_pending_login(state_token) is None


@pytest.mark.unit
def test_oidc_peek_pending_login_does_not_prevent_the_real_pop_from_consuming_the_entry(server_state):
    """The admin SSO callback route peeks first (to build redirect_uri), then hands off to
    handle_callback(), which performs the real, consuming lookup - the peek must never interfere."""
    state_token = server_state.resources.oidc_start_pending_login("https://idp", "n", "v", "/admin")

    server_state.resources.oidc_peek_pending_login(state_token)
    popped = server_state.resources.oidc_pop_pending_login(state_token)

    assert popped is not None
    assert state_token not in server_state.resources.oidc_pending


@pytest.mark.unit
def test_oidc_start_pending_login_prunes_expired_entries(server_state):
    stale_token = server_state.resources.oidc_start_pending_login("https://idp", "n", "v", "/admin")
    server_state.resources.oidc_pending[stale_token].expires_at = time.monotonic() - 1

    server_state.resources.oidc_start_pending_login("https://idp", "n2", "v2", "/admin")

    assert stale_token not in server_state.resources.oidc_pending
    assert len(server_state.resources.oidc_pending) == 1


@pytest.mark.unit
def test_oidc_start_pending_login_evicts_the_oldest_once_at_capacity(server_state, monkeypatch):
    monkeypatch.setattr("Pytre_Server.server_state.OIDC_PENDING_MAX", 2)

    first = server_state.resources.oidc_start_pending_login("https://idp", "n1", "v1", "/admin")
    server_state.resources.oidc_start_pending_login("https://idp", "n2", "v2", "/admin")
    server_state.resources.oidc_start_pending_login("https://idp", "n3", "v3", "/admin")

    # OIDC_PENDING_MAX is imported by name in server_state.py's own module scope - monkeypatching
    # the module attribute (above) is what the SUT sees; a plain `from ... import OIDC_PENDING_MAX`
    # here would have frozen the original value of 200 instead, so the cap (2) is hardcoded below.
    assert len(server_state.resources.oidc_pending) == 2
    assert first not in server_state.resources.oidc_pending  # the oldest, evicted to make room


# ================================================================================
# Client code pending (Pytre_Client mediated-mode SSO - the loopback bridge, not the IdP leg) -
# exercises the same shared _PendingHelper logic as the OIDC pending tests above, through a
# second, independent dataclass (_ClientCodePending) - no separate direct test of _PendingHelper
# itself, the two call sites already prove its generic behavior.
# ================================================================================


@pytest.mark.unit
def test_client_code_start_pending_returns_a_usable_code(server_state):
    code = server_state.resources.client_code_start_pending("sso_user", "challenge")

    assert code
    assert code in server_state.resources.client_code_pending


@pytest.mark.unit
def test_client_code_start_pending_generates_distinct_codes(server_state):
    first = server_state.resources.client_code_start_pending("sso_user", "challenge-1")
    second = server_state.resources.client_code_start_pending("sso_user", "challenge-2")

    assert first != second
    assert len(server_state.resources.client_code_pending) == 2


@pytest.mark.unit
def test_client_code_pop_pending_returns_the_stored_values(server_state):
    code = server_state.resources.client_code_start_pending("sso_user", "the-challenge")

    entry = server_state.resources.client_code_pop_pending(code)

    assert entry is not None
    assert entry.username == "sso_user"
    assert entry.code_challenge == "the-challenge"


@pytest.mark.unit
def test_client_code_pop_pending_is_single_use(server_state):
    code = server_state.resources.client_code_start_pending("sso_user", "challenge")

    first = server_state.resources.client_code_pop_pending(code)
    second = server_state.resources.client_code_pop_pending(code)

    assert first is not None
    assert second is None
    assert code not in server_state.resources.client_code_pending


@pytest.mark.unit
def test_client_code_pop_pending_rejects_an_unknown_code(server_state):
    assert server_state.resources.client_code_pop_pending("never-issued") is None


@pytest.mark.unit
def test_client_code_pop_pending_rejects_an_expired_entry(server_state):
    """Regression guard: an expired entry must not be usable even though it's still physically in
    the dict until pruned - _PendingHelper._prune_expired() runs lazily, not on a timer."""
    code = server_state.resources.client_code_start_pending("sso_user", "challenge")
    server_state.resources.client_code_pending[code].expires_at = time.monotonic() - 1

    entry = server_state.resources.client_code_pop_pending(code)

    assert entry is None
    assert code not in server_state.resources.client_code_pending  # consumed anyway, not left behind


@pytest.mark.unit
def test_client_code_start_pending_prunes_expired_entries(server_state):
    stale_code = server_state.resources.client_code_start_pending("sso_user", "challenge")
    server_state.resources.client_code_pending[stale_code].expires_at = time.monotonic() - 1

    server_state.resources.client_code_start_pending("sso_user", "challenge-2")

    assert stale_code not in server_state.resources.client_code_pending
    assert len(server_state.resources.client_code_pending) == 1


@pytest.mark.unit
def test_client_code_start_pending_evicts_the_oldest_once_at_capacity(server_state, monkeypatch):
    monkeypatch.setattr("Pytre_Server.server_state.CLIENT_CODE_PENDING_MAX", 2)

    first = server_state.resources.client_code_start_pending("sso_user", "c1")
    server_state.resources.client_code_start_pending("sso_user", "c2")
    server_state.resources.client_code_start_pending("sso_user", "c3")

    assert len(server_state.resources.client_code_pending) == 2
    assert first not in server_state.resources.client_code_pending  # the oldest, evicted to make room
