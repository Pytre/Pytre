"""SQL Server Browser (SSRP) client - resolves a named instance's dynamic TCP port."""

import asyncio
import logging
import socket

from Pytre_Core.utils import log as _log

SQL_BROWSER_PORT = 1434  # udp browser service port
_REQUEST_ALL_INSTANCES = b"\x03"  # CLNT_UCAST_EX - ask for every instance


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


def split_instance_host(host: str) -> tuple[str, str | None]:
    """Split a `HOST` or `HOST\\INSTANCE` string into (hostname, instance_name), instance_name
    is None when `host` doesn't contain a named-instance suffix."""
    hostname, sep, instance = host.partition("\\")
    return hostname, instance if sep else None


async def resolve_instance_port(host: str, instance: str, timeout: float = 5.0) -> int | None:
    """Resolve the TCP port of a named SQL Server instance. Returns None on any failure."""

    loop = asyncio.get_running_loop()
    try:
        port = await loop.run_in_executor(None, _query_browser_service, host, instance, timeout)
    except OSError as e:
        log(f"SQL Browser query to '{host}' failed: {e}", logging.WARNING)
        return None

    if port is None:
        log(f"SQL Browser: instance '{instance}' not found on '{host}'", logging.WARNING)

    log(f"SQL Browser: instance '{instance}' found on '{host}' with port {port}", logging.INFO)
    return port


def _query_browser_service(host: str, instance: str, timeout: float = 5.0) -> int | None:
    """Retrieve instance port from the SQL Server Browser service via a blocking UDP query."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(_REQUEST_ALL_INSTANCES, (host, SQL_BROWSER_PORT))
        response, _ = sock.recvfrom(4096)  # response is SVR_RESP (1 byte), RESP_SIZE (2 bytes), RESP_DATA (max 65535)

    data = response[3:].decode("ascii", errors="replace")  # skip the 3-byte response header

    for instance_resp in data.split(";;"):
        fields = instance_resp.split(";")  # field_A_name, field_A_value, field_B_name, field_B_value, ...
        instance_dict = dict(zip(fields[0::2], fields[1::2]))  # turn fields list into a proprer dict

        if not instance_dict.get("InstanceName", "").casefold() == instance.casefold():
            continue

        port = instance_dict.get("tcp")
        if port and port.isdigit():
            return int(port)

    return None
