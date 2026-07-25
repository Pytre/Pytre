"""
Tests for Pytre_Server/app.py
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from Pytre_Server.app import LOGIN_THROTTLE_WINDOW_MINUTES, MAX_LOGIN_ATTEMPTS, ServerState


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
    old_time = datetime.now() - timedelta(minutes=LOGIN_THROTTLE_WINDOW_MINUTES + 1)
    server_state.resources.throttle_failed_login["alice"] = [old_time] * MAX_LOGIN_ATTEMPTS

    assert server_state.resources.is_login_throttled("alice") is False
    assert "alice" not in server_state.resources.throttle_failed_login


@pytest.mark.unit
def test_different_usernames_have_independent_throttles(server_state):
    for _ in range(MAX_LOGIN_ATTEMPTS):
        server_state.resources.throttle_record_failing("alice")

    assert server_state.resources.is_login_throttled("bob") is False
