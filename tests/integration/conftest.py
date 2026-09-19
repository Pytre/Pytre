"""
Shared fixtures for integration tests.

Most integration tests use a dedicated, committed test KeePass database
(tests/resources/Pytre_test.db, built by tests/resources/build_test_keepass.py), so they run by
default in any environment without personal setup. Only tests that need a real SQL Server
connection depend on the local mssql-test docker container, guarded by the `requires_docker`
marker below (and, for the async PostgreSQL driver, the local postgre-test container guarded by
`requires_postgre_docker`). Each container is started automatically (via tests/resources/docker-compose.mssql.yml /
docker-compose.postgre.yml) the first time such a test is collected - no manual "docker run" step required.
"""

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import Pytre_Core.kee as kee_module
from Pytre_Server.app import create_app
from tests.resources.build_test_keepass import TEST_PG_SERVER, TEST_SQL_SERVER, TEST_USER_PASSWORD

TEST_DB_PATH = Path(__file__).parent.parent / "resources" / "Pytre_test.db"
TEST_DB_PASSWORD = "password"  # matches Pytre_Core.kee.BLANK_PWD - see build_test_keepass.py
TEST_USERNAME = "test_user"
TEST_SERVER_ID = "test"
TEST_QUERY_ID = "TEST_QUERY"


@pytest.fixture
def client(monkeypatch):
    """TestClient wired to the dedicated test KeePass database. Read-only: a test that writes
    to the database (e.g. /change-password) must use `isolated_client` instead, to avoid
    corrupting this committed fixture for other tests and future runs."""
    monkeypatch.setattr(kee_module, "KEE_FILE", TEST_DB_PATH)
    return TestClient(create_app(master_key=TEST_DB_PASSWORD))


@pytest.fixture
def isolated_client(monkeypatch, tmp_path):
    """Like `client`, but wired to a per-test copy of the test KeePass database - use this for
    any test that writes to the database, so it can never affect other tests or corrupt the
    committed fixture, even if the test itself crashes before any cleanup could run."""
    db_copy = tmp_path / "Pytre_test.db"
    shutil.copy(TEST_DB_PATH, db_copy)
    monkeypatch.setattr(kee_module, "KEE_FILE", db_copy)
    return TestClient(create_app(master_key=TEST_DB_PASSWORD))


@pytest.fixture
def admin_client(monkeypatch) -> TestClient:
    """Like `client`, but already logged in as the test admin user - use this (instead of
    `client`) for admin route tests that aren't specifically testing the login/session
    mechanism itself (that's covered by test_admin_login_route.py)."""
    monkeypatch.setattr(kee_module, "KEE_FILE", TEST_DB_PATH)
    test_client = TestClient(create_app(master_key=TEST_DB_PASSWORD))
    test_client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    return test_client


@pytest.fixture
def isolated_admin_client(monkeypatch, tmp_path) -> TestClient:
    """Like `isolated_client`, but already logged in as the test admin user - see `admin_client`."""
    db_copy = tmp_path / "Pytre_test.db"
    shutil.copy(TEST_DB_PATH, db_copy)
    monkeypatch.setattr(kee_module, "KEE_FILE", db_copy)
    test_client = TestClient(create_app(master_key=TEST_DB_PASSWORD))
    test_client.post("/admin/login", data={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    return test_client


@pytest.fixture
def auth_headers(client) -> dict[str, str]:
    """Bearer token headers for TEST_USERNAME, obtained via a real /login call against
    `client` - every protected route (/servers, /queries, /execute, /change-password) needs
    this instead of the old `username` query param/body field."""
    response = client.post("/login", json={"username": TEST_USERNAME, "password": TEST_USER_PASSWORD})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _docker_test_server_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 1433), timeout=1):
            return True
    except OSError:
        return False


def _postgre_test_server_reachable() -> bool:
    """A published docker port accepts TCP connections before the process inside is ready, so a bare
    connect isn't enough - send a PostgreSQL SSLRequest and require a real answer ('S' or 'N')."""
    ssl_request = b"\x00\x00\x00\x08\x04\xd2\x16\x2f"
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=1) as sock:
            sock.sendall(ssl_request)
            return sock.recv(1) in (b"S", b"N")
    except OSError:
        return False


def _ensure_container(
    container: str, compose_file: Path, env_vars: dict[str, str], reachable: Callable[[], bool], timeout: float
) -> bool:
    """Start `container` (via its compose file) if it isn't already reachable, then wait for it."""
    if reachable():
        return True

    if shutil.which("docker") is None:
        return False

    container_exists = subprocess.run(["docker", "inspect", container], capture_output=True, check=False).returncode == 0

    if container_exists:
        subprocess.run(["docker", "start", container], check=False)
    else:
        env = os.environ.copy()
        env.update(env_vars)
        subprocess.run(["docker", "compose", "-f", str(compose_file), "up", "-d"], env=env, check=False)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if reachable():
            return True
        time.sleep(1)
    return False


def _ensure_docker_test_server(timeout: float = 60.0) -> bool:
    """Start the mssql-test container (via docker-compose.mssql.yml) if it isn't already reachable."""
    compose_file = Path(__file__).parent.parent / "resources" / "docker-compose.mssql.yml"
    env_vars = {"MSSQL_SA_PASSWORD": TEST_SQL_SERVER["password"]}
    return _ensure_container("mssql-test", compose_file, env_vars, _docker_test_server_reachable, timeout)


def _ensure_postgre_test_server(timeout: float = 60.0) -> bool:
    """Start the postgre-test container (via docker-compose.postgre.yml) if it isn't already reachable."""
    compose_file = Path(__file__).parent.parent / "resources" / "docker-compose.postgre.yml"
    env_vars = {"POSTGRES_PASSWORD": TEST_PG_SERVER["password"]}
    return _ensure_container("postgre-test", compose_file, env_vars, _postgre_test_server_reachable, timeout)


requires_docker = pytest.mark.skipif(
    not _ensure_docker_test_server(),
    reason="mssql-test docker container could not be started/reached on 127.0.0.1:1433",
)

requires_postgre_docker = pytest.mark.skipif(
    not _ensure_postgre_test_server(),
    reason="postgre-test docker container could not be started/reached on 127.0.0.1:5432",
)
