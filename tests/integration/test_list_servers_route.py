"""
Integration test for the GET /servers route.
Uses the shared `client` fixture (dedicated test KeePass database) - runs by default, no
personal setup or docker container needed.
"""

import pytest

from tests.integration.conftest import TEST_SERVER_ID

pytestmark = pytest.mark.integration


def test_list_servers_never_exposes_credentials(client, auth_headers):
    response = client.get("/servers", headers=auth_headers)

    assert response.status_code == 200
    servers = response.json()
    assert isinstance(servers, list)
    assert any(s["id"] == TEST_SERVER_ID for s in servers)
    for server in servers:
        assert set(server.keys()) == {"id", "description"}
