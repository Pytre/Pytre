"""
Integration test for the GET /servers route.
Uses the shared `client` fixture (dedicated test KeePass database) - runs by default, no
personal setup or docker container needed.
"""

import pytest

from Pytre_Core.context import AppContext
from Pytre_Server.auth_token import TokenType, create_token
from tests.integration.conftest import TEST_DB_PASSWORD, TEST_SERVER_ID, TEST_USERNAME

pytestmark = pytest.mark.integration


def test_list_servers_never_exposes_credentials(client, auth_headers):
    response = client.get("/servers", headers=auth_headers)

    assert response.status_code == 200
    servers = response.json()["servers"]
    assert isinstance(servers, list)
    assert any(s["id"] == TEST_SERVER_ID for s in servers)
    for server in servers:
        assert set(server.keys()) == {"id", "description"}


def test_list_servers_includes_requesting_users_own_info(client, auth_headers):
    """user_infos lets a remote Pytre_Client resolve USER_INFO(...) hidden parameters against
    this remote's own user data instead of the local one - see Query.deserialize()."""
    response = client.get("/servers", headers=auth_headers)

    assert response.status_code == 200
    user_infos = response.json()["user_infos"]
    assert user_infos["username"] == TEST_USERNAME


def test_list_servers_rejects_token_for_a_nonexistent_user(client):
    """require_access_token (shared by /servers, /queries, /execute) delegates to
    username_from_token(), which must reject a well-formed, correctly signed token whose
    subject doesn't correspond to any real account - e.g. a deleted user, or a forged token."""
    app_ctx = AppContext(master_key=TEST_DB_PASSWORD)
    forged_token = create_token("no-such-user", app_ctx.settings.jwt_secret, TokenType.ACCESS)
    headers = {"Authorization": f"Bearer {forged_token}"}

    response = client.get("/servers", headers=headers)

    assert response.status_code == 401
