import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from fastapi import Request

from Pytre_Core.context import AppContext
from Pytre_Core.logs_central import ServerDriven
from Pytre_Core.oidc_client import OidcClient, new_token
from Pytre_Core.servers import Server
from Pytre_Core.users import verify_password
from Pytre_Core.utils import log as _log
from Pytre_Core.sql_query.query_drivers_server import AsyncSqlDriver, get_driver

from Pytre_Server.log_buffer import LogBufferHandler
from Pytre_Server.login_throttle import LoginThrottle
from Pytre_Server.query_catalog import QueryCatalog


PASSWORD_MAX_CONCURRENT_CHECKS = 2  # each Argon2 check takes 64 MiB and ~4 CPU cores: bounds both in a login flood
PASSWORD_CHECK_WAIT_SECS = 0.5  # wait for a free slot (a few checks duration, even on a small server) before refusing

OIDC_PENDING_TTL_SECS = 600  # long enough for a slow IdP, short enough to bound memory
OIDC_PENDING_MAX = 200  # hard cap to avoid to grow unbounded - new entry replace older one

CLIENT_CODE_PENDING_TTL_SECS = 60  # long enough for a slow redirection
CLIENT_CODE_PENDING_MAX = 200  # hard cap to avoid to grow unbounded - new entry replace older one


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class PasswordChecksSaturated(Exception):
    """Raised when no password check slot was freed within PASSWORD_CHECK_WAIT_SECS."""


@dataclass(slots=True)
class _DriverEntry:
    """A driver bundled with its own in-flight call counter.
    Tracking inflight per driver instance (not per server_id) matters once a server is reset.
    The old driver and its eventual replacement are two different instances sharing the same
    server_id, and each must only ever count its own in-flight calls, never the other's."""

    driver: AsyncSqlDriver
    inflight: int = 0  # nb of current execute() calls


@dataclass(slots=True)
class _OidcPendingLogin:
    """One in-flight SSO login attempt - the state token is the dict key (see ServerResources.oidc_pending).
    Single-use: popped by the callback, whether it succeeds or not."""

    provider_issuer: str  # re-looked-up from AppContext.oidc_providers at callback time, never a stale copy
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)
    next_path: str  # already validated by _safe_next() before being stored
    expires_at: float  # time.monotonic()


@dataclass(slots=True)
class _ClientCodePending:
    """One in-flight client code <-> PKCE bridge, bound to the loopback leg of the client's
    mediated-mode login (Étape 2) - the code is the dict key (see ServerResources.client_code_pending).
    Single-use: popped by /login/sso/token, whether the exchange succeeds or not."""

    username: str
    code_challenge: str
    expires_at: float  # time.monotonic()


@dataclass(frozen=True, eq=False, repr=False)
class ServerResources:
    """Shared, mutable state that must survive a full ServerState reload

    Each driver runs at most `db_pool_size` queries at once on its SQL server.
    One driver per server_id is created lazily and reused across every user's requests, never per-request."""

    driver_entries: dict[str, _DriverEntry] = field(default_factory=dict)  # server.id -> driver entry
    drivers_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _disconnect_tasks: set[asyncio.Task] = field(default_factory=set, repr=False)  # ref to avoid garbage collection
    db_pool_size: int = 30  # concurrent queries each driver can run on its SQL server

    user_semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    login_throttle: LoginThrottle = field(default_factory=LoginThrottle)
    password_semaphore: asyncio.BoundedSemaphore = field(
        default_factory=lambda: asyncio.BoundedSemaphore(PASSWORD_MAX_CONCURRENT_CHECKS), repr=False
    )

    execution_logs: ServerDriven | None = None
    log_buffer: LogBufferHandler | None = None
    ignore_hash_until: dict[str, datetime] = field(default_factory=dict)  # query_id -> until UTC datetime

    oidc_client: OidcClient = field(default_factory=OidcClient)
    oidc_pending: dict[str, _OidcPendingLogin] = field(default_factory=dict, repr=False)

    client_code_pending: dict[str, _ClientCodePending] = field(default_factory=dict, repr=False)

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

            driver = await get_driver(server, self.db_pool_size)
            await driver.connect()  # not cached on failure - next call retries fresh

            driver_entry = _DriverEntry(driver=driver)
            self.driver_entries[server.id] = driver_entry

            return driver_entry

    async def reset_connections(self, server_id: str | None = None) -> int:
        """Detach `server_id` if set and cached otherwise every cached driver so the next request
        reconnects with fresh Server parameters. Already-running queries on a detached driver are not
        affected - each stale driver is disconnected only when no use_driver() call is still holding it."""
        async with self.drivers_lock:
            if server_id is None:
                stale = dict(self.driver_entries)
                self.driver_entries.clear()
            elif server_id in self.driver_entries:
                stale = {server_id: self.driver_entries.pop(server_id)}
            else:
                stale = {}

        for driver_entry in stale.values():
            task = asyncio.create_task(self._disconnect_when_idle(driver_entry))
            self._disconnect_tasks.add(task)
            task.add_done_callback(self._disconnect_tasks.discard)

        return len(stale)

    async def _disconnect_when_idle(self, driver_entry: _DriverEntry) -> None:
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
    # concurrency caps (executions per user and password checks)
    # ------------------------------------------------------------------------------------------
    def _get_user_semaphore(self, max_concurrency: int, username: str) -> asyncio.Semaphore:
        """Lazily creates a per-user semaphore capping concurrent /execute requests.
        No lock needed: dict access has no await point, so two "concurrent" tasks
        can't interleave between the check and the creation."""
        if username not in self.user_semaphores:
            self.user_semaphores[username] = asyncio.Semaphore(max_concurrency)
        return self.user_semaphores[username]

    async def verify_password_capped(self, password: str, password_hash: str) -> bool:
        """verify_password() in a worker thread (Argon2 releases the GIL, the event loop stays free), with at most
        PASSWORD_MAX_CONCURRENT_CHECKS running at once across the server. Waiting for a slot holds no thread.
        The slot is released once the check is actually over, even if the caller stopped waiting for it. Raises
        PasswordChecksSaturated, without checking anything, when no slot is freed within PASSWORD_CHECK_WAIT_SECS."""
        try:
            async with asyncio.timeout(PASSWORD_CHECK_WAIT_SECS):
                await self.password_semaphore.acquire()
        except TimeoutError:
            msg = f"No password check slot freed within {PASSWORD_CHECK_WAIT_SECS}s"
            raise PasswordChecksSaturated(msg) from None

        def end_check(check: asyncio.Task) -> None:
            """Releases the slot once the check is over, and retrieves its exception so that a check nobody
            waits for anymore doesn't log "Task exception was never retrieved"."""
            self.password_semaphore.release()
            if not check.cancelled():
                check.exception()

        # no await until the release is attached: the slot can't leak
        check = asyncio.create_task(asyncio.to_thread(verify_password, password, password_hash))
        check.add_done_callback(end_check)

        return await asyncio.shield(check)  # a cancelled caller doesn't cancel the check, which keeps its slot

    # ------------------------------------------------------------------------------------------
    # OIDC pending login (webui SSO)
    # ------------------------------------------------------------------------------------------
    def oidc_start_pending_login(self, provider_issuer: str, nonce: str, code_verifier: str, next_path: str) -> str:
        """Registers a new pending SSO login and returns its state token, to embed in the
        authorization URL sent to the IdP - the callback looks it back up by that same value."""
        entry = _OidcPendingLogin(
            provider_issuer=provider_issuer,
            nonce=nonce,
            code_verifier=code_verifier,
            next_path=next_path,
            expires_at=time.monotonic() + OIDC_PENDING_TTL_SECS,
        )

        return _PendingHelper._start_pending(self.oidc_pending, OIDC_PENDING_MAX, entry)

    def oidc_pop_pending_login(self, state_token: str) -> _OidcPendingLogin | None:
        """Single-use: removes and returns the pending login for `state_token`, or None if it
        doesn't exist or has expired (already consumed, forged, or the browser took too long)."""
        return _PendingHelper._pop_or_peek_pending(self.oidc_pending, state_token)

    def oidc_peek_pending_login(self, state_token: str) -> _OidcPendingLogin | None:
        """Read-only counterpart to oidc_pop_pending_login() - the admin SSO callback route needs
        to know which provider a state belongs to before calling handle_callback().
        Never treat this as authorization - only the pop in handle_callback() consumes the entry."""
        return _PendingHelper._pop_or_peek_pending(self.oidc_pending, state_token, peek_only=True)

    # ------------------------------------------------------------------------------------------
    # Client code pending (mediated-mode SSO - loopback bridge)
    # ------------------------------------------------------------------------------------------
    def client_code_start_pending(self, username: str, code_challenge: str) -> str:
        """Registers a new one-time code bridging a successful OIDC login to the client's own
        PKCE exchange, and returns it - the client's loopback redirect carries this code back,
        then trades it (plus its code_verifier) for a Pytre token pair via /login/sso/token."""
        entry = _ClientCodePending(
            username=username,
            code_challenge=code_challenge,
            expires_at=time.monotonic() + CLIENT_CODE_PENDING_TTL_SECS,
        )
        return _PendingHelper._start_pending(self.client_code_pending, CLIENT_CODE_PENDING_MAX, entry)

    def client_code_pop_pending(self, code: str) -> _ClientCodePending | None:
        """Single-use: removes and returns the pending entry for `code`, or None if it doesn't
        exist or has expired (already consumed, forged, or the client took too long)."""
        return _PendingHelper._pop_or_peek_pending(self.client_code_pending, code)


class _PendingHelper:
    """Shared helpers for pending dict object."""

    class _Pending(Protocol):
        expires_at: float  # time.monotonic()

    @staticmethod
    def _start_pending[T: _Pending](pending: dict[str, T], max_size: int, entry: T) -> str:
        """Prunes expired entries, evicts the oldest one if at the cap, stores `entry` under a
        fresh token, and returns that token."""
        _PendingHelper._prune_expired(pending)

        if len(pending) >= max_size:
            oldest = min(pending, key=lambda k: pending[k].expires_at)
            del pending[oldest]

        token = new_token()
        pending[token] = entry

        return token

    @staticmethod
    def _pop_or_peek_pending[T: _Pending](pending: dict[str, T], key: str, peek_only: bool = False) -> T | None:
        """Returns the entry for `key` and remove it from `pending` unless `peek_only`. If it doesn't
        exist or has expired (already consumed, forged, or the caller took too long) returns None."""

        if peek_only:
            entry = pending.get(key)
        else:
            entry = pending.pop(key, None)

        if entry is None or entry.expires_at <= time.monotonic():
            return None

        return entry

    @staticmethod
    def _prune_expired[T: _Pending](pending_dict: dict[str, T]) -> None:
        """Removes every entry whose TTL has already elapsed."""
        now = time.monotonic()
        expired = [key for key, entry in pending_dict.items() if entry.expires_at <= now]
        for key in expired:
            del pending_dict[key]


@dataclass(frozen=True, eq=False)
class ServerState:
    """App context, catalog of available queries, and per-launch configuration.
    See ServerResources for the state a reload must not reset."""

    app_ctx: AppContext
    query_catalog: QueryCatalog
    chunk_size: int  # rows to stream to client in a chunk
    max_concurrency_per_user: int  # concurrent requests allowed per user
    heartbeat_secs: int = 30  # seconds between NDJSON pings while a query executes, 0 to disable
    resources: ServerResources = field(default_factory=ServerResources)


def get_server_state(request: Request) -> ServerState:
    """FastAPI dependency: retrieves the ServerState built by create_app() for the running app.
    Works identically for any router mounted on that app (client routes, admin routes...)."""
    return request.app.state.server


def client_host(request: Request) -> str | None:
    """Address of the client: the one from X-Forwarded-For when the request comes through a proxy listed in
    --forwarded-allow-ips (uvicorn rewrites it), otherwise the one of the TCP connection."""
    return request.client.host if request.client else None
