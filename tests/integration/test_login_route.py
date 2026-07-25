"""
Integration test for the POST /login route.
Uses the shared `client` fixture (dedicated test KeePass database) - runs by default, no
personal setup or docker container needed.
"""

import pytest

from tests.integration.conftest import TEST_USER_PASSWORD, TEST_USERNAME

pytestmark = pytest.mark.integration


def test_login_returns_a_token_on_correct_credentials(client):
    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})

    assert response.status_code == 200
    assert response.json()["token"]


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
