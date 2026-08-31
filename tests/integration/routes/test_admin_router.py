"""
Integration tests for the /admin routes (Pytre_Server/routes/admin_router.py).

/admin (the home page) is behind the admin auth gate, so it uses `admin_client` (loopback
bypass, see conftest.py) to reach it without a login step. The static assets
(/admin/static/logo.png, /admin/favicon.ico) stay ungated on purpose - they must render on the
login page itself - and are tested via the plain `client` fixture accordingly.
"""

import pytest

pytestmark = pytest.mark.integration


def test_admin_home_returns_ok(admin_client):
    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert "Users management" in response.text
    assert "Servers management" in response.text
    assert "Queries management" in response.text
    assert "General settings" in response.text
    assert '<img src="/admin/static/logo.png"' in response.text


def test_admin_logo_is_served(client):
    response = client.get("/admin/static/logo.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_admin_favicon_is_served(client):
    response = client.get("/admin/favicon.ico")

    assert response.status_code == 200
