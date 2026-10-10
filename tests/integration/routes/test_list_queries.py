"""
Integration test for the GET /queries route.
Uses the shared `client` fixture (dedicated test KeePass database) - runs by default, no
personal setup or docker container needed.
"""

import json

import pytest

from tests.integration.conftest import TEST_DISPLAY_FORMAT, TEST_SERVER_ID

pytestmark = pytest.mark.integration


def test_list_queries_returns_filtered_catalog(client, auth_headers):
    params = {"server_id": TEST_SERVER_ID, "display_format": json.dumps(TEST_DISPLAY_FORMAT)}
    response = client.get("/queries", params=params, headers=auth_headers)

    assert response.status_code == 200
    queries = response.json()
    assert isinstance(queries, list)
    for query in queries:
        assert "name" in query
        assert "cmd_template" in query
        assert TEST_SERVER_ID in query["servers_id"]


def test_list_queries_renders_parameters_with_client_display_format(client, auth_headers):
    """Queries are built with the client's display format, which is returned with each serialized query
    so the client parses display_params with the format they were rendered with."""
    display_format = {"date_format": "%Y-%m-%d", "decimal_separator": "."}
    params = {"server_id": TEST_SERVER_ID, "display_format": json.dumps(display_format)}
    response = client.get("/queries", params=params, headers=auth_headers)

    assert response.status_code == 200
    queries = response.json()
    assert queries
    assert all(query["display_format"] == display_format for query in queries)


def test_list_queries_rejects_missing_display_format(client, auth_headers):
    response = client.get("/queries", params={"server_id": TEST_SERVER_ID}, headers=auth_headers)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "display_format",
    [
        json.dumps({"date_format": "%d/%m", "decimal_separator": ","}),  # no year
        json.dumps({"date_format": "%d/%m/%Y", "decimal_separator": ",,"}),
        "not json",
    ],
)
def test_list_queries_rejects_invalid_display_format(client, auth_headers, display_format):
    params = {"server_id": TEST_SERVER_ID, "display_format": display_format}
    response = client.get("/queries", params=params, headers=auth_headers)

    assert response.status_code == 422
