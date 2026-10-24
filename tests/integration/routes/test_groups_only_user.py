"""
Integration tests for a user excluded from the "all" group (User.no_all_group): the server must only
list and execute the queries shared with one of the user's own groups. The test query has no groups,
so it is open to every regular user but must be refused to such a user, both in the GET /queries list
and on POST /execute. Uses `isolated_client` - the test user is modified in a per-test database copy.
"""

import json

import pytest

from Pytre_Core.users import User
from tests.integration.conftest import TEST_DISPLAY_FORMAT, TEST_QUERY_ID, TEST_SERVER_ID, TEST_USERNAME
from tests.resources.build_test_keepass import TEST_USER_PASSWORD

pytestmark = pytest.mark.integration


def _login_as_regular_user(client, no_all_group: bool) -> dict[str, str]:
    """Makes the test user a non admin (admins see every query) with the given option, then logs in"""
    user = User(client.app.state.server.app_ctx.users, username=TEST_USERNAME)
    user.admin = False
    user.no_all_group = no_all_group
    user.save()

    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _listed_query_ids(client, headers: dict[str, str]) -> list[str]:
    params = {"server_id": TEST_SERVER_ID, "display_format": json.dumps(TEST_DISPLAY_FORMAT)}
    response = client.get("/queries", params=params, headers=headers)
    assert response.status_code == 200
    return [query["name"] for query in response.json()]


def test_regular_user_lists_the_query_without_groups(isolated_client):
    """Baseline for the test below: the server and the query are reachable by a regular user"""
    headers = _login_as_regular_user(isolated_client, no_all_group=False)

    assert TEST_QUERY_ID in _listed_query_ids(isolated_client, headers)


def test_groups_only_user_does_not_list_the_query_without_groups(isolated_client):
    headers = _login_as_regular_user(isolated_client, no_all_group=True)

    assert TEST_QUERY_ID not in _listed_query_ids(isolated_client, headers)


def test_groups_only_user_can_not_execute_the_query_without_groups(isolated_client):
    """Not listing it isn't enough: a client sending the query id directly must be refused too"""
    headers = _login_as_regular_user(isolated_client, no_all_group=True)

    response = isolated_client.post(
        "/execute",
        json={
            "server_id": TEST_SERVER_ID,
            "query_id": TEST_QUERY_ID,
            "template_hash": "x",
            "display_format": TEST_DISPLAY_FORMAT,
        },
        headers=headers,
    )

    assert response.status_code == 404
