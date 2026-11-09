"""
Tests for Pytre_Server/server_state.py
"""

import asyncio
import gc
import logging
import threading
import time
from unittest.mock import AsyncMock, Mock

import pytest

from Pytre_Core.users import hash_password
from Pytre_Server.server_state import (
    PASSWORD_MAX_CONCURRENT_CHECKS,
    PasswordChecksSaturated,
    ServerResources,
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
# Capped password checks (verify_password_capped)
# ================================================================================

SHORT_WAIT_SECS = 0.05  # replaces PASSWORD_CHECK_WAIT_SECS in the tests expecting a refusal, to keep them fast


async def _take_every_password_slot(resources: ServerResources) -> None:
    for _ in range(PASSWORD_MAX_CONCURRENT_CHECKS):
        await resources.password_semaphore.acquire()  # never waits: the slots are free


def _free_every_password_slot(resources: ServerResources, count: int = PASSWORD_MAX_CONCURRENT_CHECKS) -> None:
    for _ in range(count):
        resources.password_semaphore.release()


async def _free_password_slots(resources: ServerResources) -> int:
    """Number of password check slots currently free - takes them all, then gives them back."""
    taken = 0
    while not resources.password_semaphore.locked():
        await resources.password_semaphore.acquire()
        taken += 1
    _free_every_password_slot(resources, taken)
    return taken


async def _wait_for_free_password_slots(resources: ServerResources, expected: int, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while await _free_password_slots(resources) != expected:
            await asyncio.sleep(0.01)


class _BlockingCheck:
    """Stands in for verify_password(): every call blocks its worker thread until release() - lets a test hold
    checks "running" for as long as it needs."""

    def __init__(self, result: bool = True, exc: BaseException | None = None) -> None:
        self.result = result
        self.exc = exc
        self.running = threading.Semaphore(0)  # released once per call that started
        self._released = threading.Event()

    def __call__(self, _password: str, _password_hash: str) -> bool:
        self.running.release()
        self._released.wait(timeout=5)
        if self.exc is not None:
            raise self.exc
        return self.result

    async def wait_running(self, count: int = 1) -> None:
        for _ in range(count):
            assert await asyncio.to_thread(self.running.acquire, timeout=5)

    def release(self) -> None:
        self._released.set()


@pytest.mark.anyio
async def test_verify_password_capped_accepts_the_right_password(server_state):
    assert await server_state.resources.verify_password_capped("s3cret", hash_password("s3cret")) is True


@pytest.mark.anyio
async def test_verify_password_capped_rejects_a_wrong_password(server_state):
    assert await server_state.resources.verify_password_capped("wrong", hash_password("s3cret")) is False


@pytest.mark.anyio
async def test_verify_password_capped_checks_outside_the_event_loop(server_state, monkeypatch):
    loop_running_during_check: list[bool] = []

    def spy(_password: str, _password_hash: str) -> bool:
        try:
            asyncio.get_running_loop()
            loop_running_during_check.append(True)
        except RuntimeError:
            loop_running_during_check.append(False)
        return True

    monkeypatch.setattr("Pytre_Server.server_state.verify_password", spy)

    await server_state.resources.verify_password_capped("s3cret", "some-hash")

    assert loop_running_during_check == [False]


@pytest.mark.anyio
async def test_verify_password_capped_refuses_without_checking_when_no_slot_is_freed_in_time(
    server_state, monkeypatch
):
    resources = server_state.resources
    fake_verify = Mock(return_value=True)
    monkeypatch.setattr("Pytre_Server.server_state.verify_password", fake_verify)
    monkeypatch.setattr("Pytre_Server.server_state.PASSWORD_CHECK_WAIT_SECS", SHORT_WAIT_SECS)

    await _take_every_password_slot(resources)
    try:
        with pytest.raises(PasswordChecksSaturated):
            await resources.verify_password_capped("s3cret", "some-hash")
    finally:
        _free_every_password_slot(resources)

    fake_verify.assert_not_called()


@pytest.mark.anyio
async def test_verify_password_capped_waits_for_a_slot_freed_in_time(server_state, monkeypatch):
    """The wait spares a refusal to a login arriving while the other checks finish."""
    resources = server_state.resources
    monkeypatch.setattr("Pytre_Server.server_state.verify_password", Mock(return_value=True))

    await _take_every_password_slot(resources)
    asyncio.get_running_loop().call_later(0.05, resources.password_semaphore.release)  # one check over, soon
    try:
        assert await resources.verify_password_capped("s3cret", "some-hash") is True
    finally:
        _free_every_password_slot(resources, PASSWORD_MAX_CONCURRENT_CHECKS - 1)


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", [True, False], ids=["success", "failure"])
async def test_verify_password_capped_frees_its_slot_once_done(server_state, monkeypatch, outcome):
    monkeypatch.setattr("Pytre_Server.server_state.verify_password", Mock(return_value=outcome))

    assert await server_state.resources.verify_password_capped("s3cret", "some-hash") is outcome

    assert await _free_password_slots(server_state.resources) == PASSWORD_MAX_CONCURRENT_CHECKS


@pytest.mark.anyio
async def test_verify_password_capped_frees_its_slot_when_the_check_raises(server_state, monkeypatch):
    monkeypatch.setattr("Pytre_Server.server_state.verify_password", Mock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        await server_state.resources.verify_password_capped("s3cret", "some-hash")

    assert await _free_password_slots(server_state.resources) == PASSWORD_MAX_CONCURRENT_CHECKS


@pytest.mark.anyio
async def test_verify_password_capped_never_runs_more_checks_at_once_than_the_cap(server_state, monkeypatch):
    """Checks still running hold their slot: one more call waits, then is refused."""
    resources = server_state.resources
    blocking_check = _BlockingCheck()
    monkeypatch.setattr("Pytre_Server.server_state.verify_password", blocking_check)
    monkeypatch.setattr("Pytre_Server.server_state.PASSWORD_CHECK_WAIT_SECS", SHORT_WAIT_SECS)

    calls = [
        asyncio.create_task(resources.verify_password_capped("s3cret", "some-hash"))
        for _ in range(PASSWORD_MAX_CONCURRENT_CHECKS)
    ]
    try:
        await blocking_check.wait_running(PASSWORD_MAX_CONCURRENT_CHECKS)

        with pytest.raises(PasswordChecksSaturated):
            await resources.verify_password_capped("s3cret", "some-hash")
    finally:
        blocking_check.release()

    assert await asyncio.gather(*calls) == [True] * PASSWORD_MAX_CONCURRENT_CHECKS
    assert await _free_password_slots(resources) == PASSWORD_MAX_CONCURRENT_CHECKS


@pytest.mark.anyio
async def test_verify_password_capped_keeps_the_slot_of_a_cancelled_caller_until_the_check_is_over(
    server_state, monkeypatch
):
    """A client disconnecting can't free a slot while its check still runs - the cap would be exceeded."""
    resources = server_state.resources
    blocking_check = _BlockingCheck()
    monkeypatch.setattr("Pytre_Server.server_state.verify_password", blocking_check)

    caller = asyncio.create_task(resources.verify_password_capped("s3cret", "some-hash"))
    await blocking_check.wait_running()

    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert await _free_password_slots(resources) == PASSWORD_MAX_CONCURRENT_CHECKS - 1  # still held

    blocking_check.release()
    await _wait_for_free_password_slots(resources, PASSWORD_MAX_CONCURRENT_CHECKS)


@pytest.mark.anyio
async def test_verify_password_capped_retrieves_the_error_of_a_check_nobody_waits_for(server_state, monkeypatch):
    """A check raising after its caller was cancelled must not log "Task exception was never retrieved"."""
    resources = server_state.resources
    blocking_check = _BlockingCheck(exc=RuntimeError("boom"))
    monkeypatch.setattr("Pytre_Server.server_state.verify_password", blocking_check)

    loop = asyncio.get_running_loop()
    loop_errors: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
    try:
        caller = asyncio.create_task(resources.verify_password_capped("s3cret", "some-hash"))
        await blocking_check.wait_running()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        blocking_check.release()
        await _wait_for_free_password_slots(resources, PASSWORD_MAX_CONCURRENT_CHECKS)

        del caller
        gc.collect()  # an unretrieved exception is only logged once its task is garbage collected
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert loop_errors == []


# ================================================================================
# Driver caching (use_driver / _get_driver_entry / reset_connections / shutdown_drivers)
# ================================================================================


def _make_fake_driver_factory(monkeypatch) -> list[Mock]:
    """Monkeypatches get_driver() so _get_driver_entry() never touches a real connection.
    Returns the list of fake driver instances created, in construction order.
    Driver construction details (trust_certificate forwarding, host resolution...) are
    fastmssql-specific and tested against get_driver()/FastMssqlDriver directly in
    tests/unit/core/test_query_drivers_server.py, not here."""
    created: list[Mock] = []

    async def fake_get_driver(server, pool_size):
        driver = Mock(pool_size=pool_size)
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


@pytest.mark.unit
def test_resources_db_pool_size_defaults_to_30():
    assert ServerResources().db_pool_size == 30


@pytest.mark.anyio
async def test_get_driver_entry_builds_the_driver_with_db_pool_size(monkeypatch):
    created = _make_fake_driver_factory(monkeypatch)
    resources = ServerResources(db_pool_size=7)

    await resources._get_driver_entry(_make_server("srv1"))

    assert created[0].pool_size == 7


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
