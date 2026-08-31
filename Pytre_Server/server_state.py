import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import Request
from fastmssql import PoolConfig

from Pytre_Core.context import AppContext
from Pytre_Core.logs_central import ServerDriven
from Pytre_Core.servers import Server
from Pytre_Core.utils import log as _log

from Pytre_Server.query_catalog import QueryCatalog
from Pytre_Server.execution.fastmssql_driver import FastMssqlDriver
from Pytre_Server.execution.protocol import AsyncSqlDriver
from Pytre_Server.execution.sql_browser import split_instance_host, resolve_instance_port


MAX_LOGIN_ATTEMPTS = 5
LOGIN_THROTTLE_WINDOW_MINUTES = 15


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass
class _DriverEntry:
    """A driver bundled with its own in-flight call counter.
    Tracking inflight per driver instance (not per server_id) matters once a server is reset.
    The old driver and its eventual replacement are two different instances sharing the same
    server_id, and each must only ever count its own in-flight calls, never the other's."""

    driver: AsyncSqlDriver
    inflight: int = 0  # nb of current execute() calls


@dataclass(frozen=True)
class ServerResources:
    """Shared, mutable state that must survive a full ServerState reload

    fastmssql.Connection is a connection pool, so one driver per server_id is
    created lazily and reused across every user's requests, never per-request."""

    driver_entries: dict[str, _DriverEntry] = field(default_factory=dict)  # server.id -> driver entry
    drivers_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _disconnect_tasks: set[asyncio.Task] = field(default_factory=set, repr=False)  # ref to avoid garbage collection

    resolved_hosts: dict[str, tuple[str, int]] = field(default_factory=dict)  # server.host -> (hostname, port)

    user_semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    throttle_failed_login: dict[str, list[datetime]] = field(default_factory=dict)

    execution_logs: ServerDriven | None = None
    ignore_hash_until: dict[str, datetime] = field(default_factory=dict)  # query_id -> hash ignored until datetime

    # ------------------------------------------------------------------------------------------
    # driver related methods
    # ------------------------------------------------------------------------------------------
    @asynccontextmanager
    async def use_driver(self, server: Server) -> AsyncGenerator[AsyncSqlDriver]:
        """Yields the driver for `server`, marking its entry in-flight for the duration -
        reset_connections() won't disconnect an entry while any use_driver() call on it
        hasn't exited yet."""
        entry = await self._get_driver_entry(server)
        entry.inflight += 1
        try:
            yield entry.driver
        finally:
            entry.inflight -= 1

    async def _get_driver_entry(self, server: Server) -> _DriverEntry:
        """Return the cached driver for `server`, or connect and cache a new one.
        Double-checked locking to avoid concurrent requests for the same not-yet-cached `server`."""
        if server.id in self.driver_entries:
            return self.driver_entries[server.id]

        async with self.drivers_lock:
            if server.id in self.driver_entries:  # check again: another request may have connected first
                return self.driver_entries[server.id]

            hostname, port = await self._resolve_connection_target(server)
            driver = FastMssqlDriver(
                host=hostname,
                port=port,
                database=server.database,
                username=server.user,
                password=server.password,
                trust_certificate=server.trust_server_certificate,
                pool_config=PoolConfig.performance(),
            )
            await driver.connect()  # not cached on failure - next call retries fresh

            driver_entry = _DriverEntry(driver=driver)
            self.driver_entries[server.id] = driver_entry

            return driver_entry

    async def _resolve_connection_target(self, server: Server) -> tuple[str, int]:
        """Return (hostname, port) to actually connect to.
        If `server.host` has no named-instance suffix (no backslash), returns it unchanged - no network call.
        Otherwise resolves the instance's real TCP port via the SQL Browser service once and caches it;
        falls back to the configured port unchanged if resolution fails (Browser service disabled/unreachable)."""
        if server.host in self.resolved_hosts:
            return self.resolved_hosts[server.host]

        hostname, instance_name = split_instance_host(server.host)
        target = (hostname, int(server.port))

        if instance_name is None:
            log(f"'{server.host}' has no named-instance suffix, using configured port as-is", logging.DEBUG)
        else:
            resolved_port = await resolve_instance_port(hostname, instance_name)
            if resolved_port is not None:
                target = (hostname, resolved_port)

        self.resolved_hosts[server.host] = target
        return target

    async def reset_connections(self, server_id: str | None = None) -> int:
        """Detach `server_id` if set and cached otherwise every cached driver + resolved host so the next request
        reconnects with fresh Server parameters. Already-running queries on a detached driver are not
        affected - each stale driver is disconnected only when no use_driver() call is still holding it."""
        async with self.drivers_lock:
            if server_id is None:
                stale = dict(self.driver_entries)
                self.driver_entries.clear()
                self.resolved_hosts.clear()
            elif server_id in self.driver_entries:
                stale = {server_id: self.driver_entries.pop(server_id)}
            else:
                stale = {}

        for server_id, driver_entry in stale.items():
            task = asyncio.create_task(self._disconnect_when_idle(server_id, driver_entry))
            self._disconnect_tasks.add(task)
            task.add_done_callback(self._disconnect_tasks.discard)

        return len(stale)

    async def _disconnect_when_idle(self, server_id: str, driver_entry: _DriverEntry) -> None:
        while driver_entry.inflight > 0:
            await asyncio.sleep(0.5)
        await driver_entry.driver.disconnect()

    async def shutdown_drivers(self) -> None:
        """Disconnect every currently cached driver and cancel any pending deferred-disconnect
        background task - called at server shutdown, when the process is exiting so there's no
        need to wait for those tasks to finish on their own."""
        for entry in self.driver_entries.values():
            await entry.driver.disconnect()

        for task in self._disconnect_tasks:
            task.cancel()

    # ------------------------------------------------------------------------------------------
    # users throttling
    # ------------------------------------------------------------------------------------------
    def _get_user_semaphore(self, max_concurrency: int, username: str) -> asyncio.Semaphore:
        """Lazily creates a per-user semaphore capping concurrent /execute requests.
        No lock needed: dict access has no await point, so two "concurrent" tasks
        can't interleave between the check and the creation."""
        if username not in self.user_semaphores:
            self.user_semaphores[username] = asyncio.Semaphore(max_concurrency)
        return self.user_semaphores[username]

    def is_login_throttled(self, username: str) -> bool:
        """True if `username` has hit MAX_LOGIN_ATTEMPTS failures within the rolling window"""
        cutoff = datetime.now() - timedelta(minutes=LOGIN_THROTTLE_WINDOW_MINUTES)
        user_key = self.throttle_standardize_username(username)

        recent = [a for a in self.throttle_failed_login.get(user_key, []) if a > cutoff]
        if recent:
            self.throttle_failed_login[user_key] = recent
        else:
            self.throttle_reset_failings(user_key)

        return len(recent) >= MAX_LOGIN_ATTEMPTS

    def throttle_record_failing(self, username: str) -> None:
        user_key = self.throttle_standardize_username(username)
        self.throttle_failed_login.setdefault(user_key, []).append(datetime.now())

    def throttle_reset_failings(self, username: str) -> None:
        user_key = self.throttle_standardize_username(username)
        self.throttle_failed_login.pop(user_key, None)

    @staticmethod
    def throttle_standardize_username(username: str) -> str:
        """Standardize `username` to avoid duplicates if different case is used"""
        return username.lower()


@dataclass(frozen=True)
class ServerState:
    """App context, catalog of available queries, and per-launch configuration.
    See ServerResources for the state a reload must not reset."""

    app_ctx: AppContext
    query_catalog: QueryCatalog
    chunk_size: int  # rows to stream to client in a chunk
    max_concurrency_per_user: int  # concurrent requests allowed per user
    resources: ServerResources = field(default_factory=ServerResources)


def get_server_state(request: Request) -> ServerState:
    """FastAPI dependency: retrieves the ServerState built by create_app() for the running app.
    Works identically for any router mounted on that app (client routes, admin routes...)."""
    return request.app.state.server
