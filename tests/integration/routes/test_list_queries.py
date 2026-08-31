"""
Integration test for the GET /queries route.
Uses the shared `client` fixture (dedicated test KeePass database) - runs by default, no
personal setup or docker container needed.
"""

import pytest

from tests.integration.conftest import TEST_SERVER_ID

pytestmark = pytest.mark.integration


def test_list_queries_returns_filtered_catalog(client, auth_headers):
    response = client.get("/queries", params={"server_id": TEST_SERVER_ID}, headers=auth_headers)

    assert response.status_code == 200
    queries = response.json()
    assert isinstance(queries, list)
    for query in queries:
        assert "name" in query
        assert "cmd_template" in query
        assert TEST_SERVER_ID in query["servers_id"]
