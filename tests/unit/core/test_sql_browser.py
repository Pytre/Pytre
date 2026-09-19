"""
Tests for Pytre_Core/sql_query/sql_browser.py
"""

from unittest.mock import Mock

import pytest

from Pytre_Core.sql_query.sql_browser import _query_browser_service, resolve_instance_port, split_instance_host


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _instance_block(name: str, port: str) -> str:
    return f"ServerName;SWTPX3BDD01;InstanceName;{name};IsClustered;No;Version;15.0.2000.5;tcp;{port}"


def _mock_socket(monkeypatch, response_body: str):
    """Patches socket.socket so a query returns `response_body`, prefixed with the 3-byte
    header the real protocol always sends and _query_browser_service always strips."""
    fake_socket = Mock()
    fake_socket.__enter__ = Mock(return_value=fake_socket)
    fake_socket.__exit__ = Mock(return_value=False)
    fake_socket.recvfrom = Mock(return_value=(b"\x05\x00\x00" + response_body.encode("ascii"), ("1.2.3.4", 1434)))

    import Pytre_Core.sql_query.sql_browser as sql_browser

    monkeypatch.setattr(sql_browser.socket, "socket", Mock(return_value=fake_socket))
    return fake_socket


# ================================================================================
# split_instance_host
# ================================================================================


@pytest.mark.unit
def test_split_instance_host_plain_host_has_no_instance():
    assert split_instance_host("SWTPX3BDD01") == ("SWTPX3BDD01", None)


@pytest.mark.unit
def test_split_instance_host_splits_named_instance():
    assert split_instance_host("SWTPX3BDD01\\DBX3V12") == ("SWTPX3BDD01", "DBX3V12")


@pytest.mark.unit
def test_split_instance_host_empty_instance_after_backslash():
    assert split_instance_host("SWTPX3BDD01\\") == ("SWTPX3BDD01", "")


# ================================================================================
# _query_browser_service
# ================================================================================


@pytest.mark.unit
def test_query_browser_service_requests_all_instances(monkeypatch):
    """Always asks the Browser service for every instance (not a targeted per-name query) -
    that's what lets the case-insensitive match below work regardless of how the instance
    name was typed, instead of depending on the Browser service's own name matching."""
    fake_socket = _mock_socket(monkeypatch, _instance_block("DBX3V12", "52149") + ";;")

    _query_browser_service("SWTPX3BDD01", "DBX3V12")

    fake_socket.sendto.assert_called_once_with(b"\x03", ("SWTPX3BDD01", 1434))


@pytest.mark.unit
def test_query_browser_service_finds_matching_instance(monkeypatch):
    _mock_socket(monkeypatch, _instance_block("DBX3V12", "52149") + ";;")

    assert _query_browser_service("SWTPX3BDD01", "DBX3V12") == 52149


@pytest.mark.unit
def test_query_browser_service_matches_case_insensitively(monkeypatch):
    _mock_socket(monkeypatch, _instance_block("DBX3V12", "52149") + ";;")

    assert _query_browser_service("SWTPX3BDD01", "dbx3v12") == 52149


@pytest.mark.unit
def test_query_browser_service_finds_instance_among_several(monkeypatch):
    response = _instance_block("MSSQLSERVER", "1433") + ";;" + _instance_block("DBX3V12", "52149") + ";;"
    _mock_socket(monkeypatch, response)

    assert _query_browser_service("SWTPX3BDD01", "DBX3V12") == 52149


@pytest.mark.unit
def test_query_browser_service_returns_none_if_instance_not_found(monkeypatch):
    _mock_socket(monkeypatch, _instance_block("MSSQLSERVER", "1433") + ";;")

    assert _query_browser_service("SWTPX3BDD01", "DBX3V12") is None


@pytest.mark.unit
def test_query_browser_service_returns_none_if_tcp_field_missing(monkeypatch):
    _mock_socket(monkeypatch, "ServerName;SWTPX3BDD01;InstanceName;DBX3V12;IsClustered;No" + ";;")

    assert _query_browser_service("SWTPX3BDD01", "DBX3V12") is None


@pytest.mark.unit
def test_query_browser_service_returns_none_on_empty_response(monkeypatch):
    _mock_socket(monkeypatch, "")

    assert _query_browser_service("SWTPX3BDD01", "DBX3V12") is None


# ================================================================================
# resolve_instance_port
# ================================================================================


@pytest.mark.anyio
async def test_resolve_instance_port_returns_resolved_port(monkeypatch):
    monkeypatch.setattr(
        "Pytre_Core.sql_query.sql_browser._query_browser_service", lambda host, instance, timeout: 52149
    )

    port = await resolve_instance_port("SWTPX3BDD01", "DBX3V12")

    assert port == 52149


@pytest.mark.anyio
async def test_resolve_instance_port_returns_none_on_os_error(monkeypatch):
    def raise_timeout(host, instance, timeout):
        raise OSError("timed out")

    monkeypatch.setattr("Pytre_Core.sql_query.sql_browser._query_browser_service", raise_timeout)

    port = await resolve_instance_port("SWTPX3BDD01", "DBX3V12")

    assert port is None


@pytest.mark.anyio
async def test_resolve_instance_port_returns_none_if_instance_not_found(monkeypatch):
    monkeypatch.setattr(
        "Pytre_Core.sql_query.sql_browser._query_browser_service", lambda host, instance, timeout: None
    )

    port = await resolve_instance_port("SWTPX3BDD01", "DBX3V12")

    assert port is None
