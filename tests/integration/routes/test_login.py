"""
Integration test for the POST /login route.
Uses the shared `client` fixture (dedicated test KeePass database) - runs by default, no
personal setup or docker container needed.
"""

from unittest.mock import Mock

import pytest

from Pytre_Core.users import verify_password
from Pytre_Server.login_throttle import USER_IP_MAX_ATTEMPTS
from tests.integration.conftest import TEST_USER_PASSWORD, TEST_USERNAME
from tests.integration.routes.conftest import (
    IP_THRESHOLD,
    client_from,
    event_loop_is_running,
    every_password_slot_taken,
    fail_from_one_address,
    lower_ip_threshold,
)

pytestmark = pytest.mark.integration


def test_login_returns_a_refresh_and_access_token_on_correct_credentials(client):
    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 200
    assert response.json()["refresh_token"]
    assert response.json()["access_token"]


def test_login_rejects_wrong_password(client):
    response = client.post("/login", json={"username": TEST_USERNAME, "password": "wrong-password"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_login_rejects_unknown_username(client):
    response = client.post("/login", json={"username": "no-such-user", "password": "anything"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_login_throttles_after_too_many_failures(client):
    for _ in range(5):
        client.post("/login", json={"username": TEST_USERNAME, "password": "wrong-password"})

    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 429


def test_login_is_refused_without_counting_a_failure_when_every_password_slot_is_taken(client, monkeypatch):
    resources = client.app.state.server.resources

    with every_password_slot_taken(resources, monkeypatch):
        response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 429
    assert response.json()["detail"] == "Too many attempts, try again later"
    assert resources.login_throttle.user.failed_login == {}
    assert resources.login_throttle.ip.failed_login == {}


def test_login_works_again_once_password_slots_are_freed(client, monkeypatch):
    with every_password_slot_taken(client.app.state.server.resources, monkeypatch):
        client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 200


def test_login_checks_the_password_outside_the_event_loop(client, monkeypatch):
    """An Argon2 check run on the event loop would freeze every other request while it lasts."""
    loop_running_during_check: list[bool] = []

    def spy(password: str, password_hash: str) -> bool:
        loop_running_during_check.append(event_loop_is_running())
        return verify_password(password, password_hash)

    monkeypatch.setattr("Pytre_Server.server_state.verify_password", spy)

    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 200
    assert loop_running_during_check == [False]


# ================================================================================
# Throttling per client address
# ================================================================================


def test_login_throttles_an_address_failing_with_many_usernames(client):
    """Changing username at each attempt must not dodge the limit - even the right credentials are then refused."""
    lower_ip_threshold(client)
    fail_from_one_address(client, IP_THRESHOLD)

    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 429
    assert response.json()["detail"] == "Too many attempts, try again later"


def test_login_address_throttle_spares_other_addresses(client):
    lower_ip_threshold(client)
    fail_from_one_address(client, IP_THRESHOLD)
    response = client_from(client, "10.0.0.2").post(
        "/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD}
    )

    assert response.status_code == 200


def test_login_refused_for_its_address_runs_no_password_check(client, monkeypatch):
    """Once an address is throttled, its attempts cost the server nothing."""
    lower_ip_threshold(client)
    fail_from_one_address(client, IP_THRESHOLD)
    spy = Mock(side_effect=verify_password)
    monkeypatch.setattr("Pytre_Server.server_state.verify_password", spy)

    client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    spy.assert_not_called()


def test_login_address_count_is_shared_with_admin_login(client):
    lower_ip_threshold(client)
    for i in range(IP_THRESHOLD):
        client.post("/admin/login", data={"username": f"no-such-user-{i}", "password": "wrong-password"})

    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 429


# ================================================================================
# Abusive account lockout
# ================================================================================

CORRECT_CREDENTIALS = {"username": TEST_USERNAME, "password": TEST_USER_PASSWORD}
WRONG_CREDENTIALS = {"username": TEST_USERNAME, "password": "wrong-password"}


def test_login_failures_from_an_address_do_not_lock_the_account_out_of_other_addresses(client):
    """An attacker failing on purpose only throttles the account from their own address."""
    for _ in range(USER_IP_MAX_ATTEMPTS):
        client.post("/login", json=WRONG_CREDENTIALS)

    attacker_response = client.post("/login", json=CORRECT_CREDENTIALS)
    victim_response = client_from(client, "10.0.0.7").post("/login", json=CORRECT_CREDENTIALS)

    assert attacker_response.status_code == 429
    assert victim_response.status_code == 200


def test_login_account_throttled_from_every_address_stays_usable_from_a_known_address(client):
    """The count from every address is lowered below the one from one address, so a few failures from a single
    address are enough to reach it."""
    client.app.state.server.resources.login_throttle.user.max_attempts = 3
    victim = client_from(client, "10.0.0.7")
    assert victim.post("/login", json=CORRECT_CREDENTIALS).status_code == 200  # the victim's usual address

    for _ in range(3):
        client.post("/login", json=WRONG_CREDENTIALS)

    assert client_from(client, "10.0.0.8").post("/login", json=CORRECT_CREDENTIALS).status_code == 429
    assert victim.post("/login", json=CORRECT_CREDENTIALS).status_code == 200


def test_login_success_makes_its_address_known(client):
    client_from(client, "10.0.0.7").post("/login", json=CORRECT_CREDENTIALS)

    known_addresses = client.app.state.server.resources.login_throttle.known_addresses
    assert known_addresses.is_known(TEST_USERNAME.lower(), "10.0.0.7") is True


def test_login_failure_does_not_make_its_address_known(client):
    client_from(client, "10.0.0.7").post("/login", json=WRONG_CREDENTIALS)

    known_addresses = client.app.state.server.resources.login_throttle.known_addresses
    assert known_addresses.is_known(TEST_USERNAME.lower(), "10.0.0.7") is False
