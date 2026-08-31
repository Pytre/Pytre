"""
Integration test for the GET / route - redirects to the admin home page.
Uses the shared `client` fixture - runs by default, no personal setup or docker needed.
"""

import pytest

pytestmark = pytest.mark.integration


def test_root_redirects_to_admin(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (301, 302, 307, 308)
    assert response.headers["location"] == "/admin"
