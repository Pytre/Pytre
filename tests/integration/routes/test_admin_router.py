"""
Integration tests for the /admin routes (Pytre_Server/routes/admin_router.py).

/admin (the home page) is behind the admin auth gate, so it uses `admin_client` (loopback
bypass, see conftest.py) to reach it without a login step. The static assets
(/admin/static/logo.png, /admin/favicon.ico) stay ungated on purpose - they must render on the
login page itself - and are tested via the plain `client` fixture accordingly.
"""

import re

import pytest

from Pytre_Core.about import APP_STATUS, APP_VERSION

pytestmark = pytest.mark.integration


def test_admin_home_returns_ok(admin_client):
    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert "Users" in response.text
    assert "Servers" in response.text
    assert "Queries" in response.text
    assert "SSO providers" in response.text
    assert "General settings" in response.text
    assert '<img src="/admin/static/logo.png"' in response.text


def test_admin_logo_is_served(client):
    response = client.get("/admin/static/logo.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_admin_favicon_is_served(client):
    response = client.get("/admin/favicon.ico")

    assert response.status_code == 200


# One page per route module: each used to build its own Jinja2Templates, so a global registered on
# one of them was missing on the others (Jinja renders an unknown variable as empty, no error).
@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/users",
        "/admin/servers",
        "/admin/queries",
        "/admin/settings",
        "/admin/master-key",
        "/admin/logs",
    ],
)
def test_sidebar_shows_the_app_version_on_every_admin_page(admin_client, path):
    response = admin_client.get(path)

    assert response.status_code == 200
    version_block = re.search(r'<div class="sidebar-version">([^<]*)</div>', response.text)
    assert version_block is not None
    assert APP_VERSION in version_block.group(1)
    if APP_STATUS:
        assert APP_STATUS in version_block.group(1)


def test_login_page_has_no_sidebar_version(client):
    response = client.get("/admin/login")

    assert response.status_code == 200
    assert '<div class="sidebar-version">' not in response.text
