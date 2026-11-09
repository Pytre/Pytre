"""
Throttling of failed logins, shared by the client API's /login and the web administration's /admin/login.

Failures are counted per username and per client address within a rolling window: past a threshold, further
attempts are refused before any password check, so they cost the server nothing. Kept in memory only, bounded,
and lost on restart.
"""

import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from Pytre_Core.utils import log as _log


LOGIN_THROTTLE_WINDOW = timedelta(minutes=15)
USER_MAX_ATTEMPTS = 5
USER_MAX_TRACKED = 10_000  # bounds memory during a flood, the stalest one is forgotten first
IP_MAX_ATTEMPTS = 20  # many users may share one address (RDS/Citrix server, NAT, outgoing proxy)
IP_MAX_TRACKED = 10_000  # bounds memory during a flood, the stalest one is forgotten first


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass
class LoginThrottle:
    """Failed logins counted per username and per client address. Login routes ask is_throttled() before checking a
    password, then report the outcome with record_failure() or record_success().
    The count of an address is never reset by a successful login: a valid account must not let an attacker restart
    the count of their address."""

    user: _FailureCounter = field(
        default_factory=lambda: _FailureCounter("User", USER_MAX_ATTEMPTS, USER_MAX_TRACKED, logging.INFO)
    )
    ip: _FailureCounter = field(
        default_factory=lambda: _FailureCounter("Address", IP_MAX_ATTEMPTS, IP_MAX_TRACKED, logging.WARNING)
    )

    def is_throttled(self, username: str, host: str | None) -> bool:
        """True if `username` has hit USER_MAX_ATTEMPTS failures, or the address `host` IP_MAX_ATTEMPTS failures
        within the rolling window."""
        user_key = self._user_key(username)
        ip_key = self._ip_key(host)

        return self.ip.is_throttled(ip_key) or self.user.is_throttled(user_key)

    def record_failure(self, username: str, host: str | None) -> None:
        """Records a failed login, for `username` and for the address `host`."""
        user_key = self._user_key(username)
        ip_key = self._ip_key(host)

        self.user.record_failing(user_key)
        self.ip.record_failing(ip_key)

    def record_success(self, username: str, host: str | None) -> None:
        """Records a successful login: forgets the failures of `username`. Those of the address `host` are kept."""
        user_key = self._user_key(username)
        ip_key = self._ip_key(host)

        self.user.forget_failings(user_key)

    @staticmethod
    def _user_key(username: str) -> str:
        """Key counting the failed logins of `username`: case-insensitive, so varying the case doesn't dodge the
        limit."""
        return username.lower()

    @staticmethod
    def _ip_key(host: str | None) -> str:
        """Key counting the failed logins of `host`: an IPv4 address as is, an IPv6 one by its /64 network (one
        subscriber usually gets a whole /64), an IPv4-mapped IPv6 address as its IPv4. A value that isn't an address
        is used as is, "unknown" without any."""
        if not host:
            return "unknown"

        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return host

        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped is not None:
                return str(address.ipv4_mapped)
            return str(ipaddress.IPv6Network((address, 64), strict=False))

        return str(address)


@dataclass(slots=True)
class _FailureCounter:
    """Failed logins counted per key (a username, a client address...) within a rolling window. At most `max_tracked`
    keys are kept: beyond, the one whose last failure is the oldest is forgotten."""

    label: str  # names the keys in the logs, e.g. "User"
    max_attempts: int
    max_tracked: int
    log_level: int = logging.INFO
    window: timedelta = LOGIN_THROTTLE_WINDOW
    failed_login: dict[str, list[datetime]] = field(default_factory=dict)  # UTC, ordered by last failure

    def is_throttled(self, key: str) -> bool:
        """True if `key` has hit `max_attempts` failures within the rolling window"""
        return len(self._recent_failed(key)) >= self.max_attempts

    def record_failing(self, key: str) -> None:
        """Records a failed login from `key`. Beyond `max_tracked` keys, the one whose latest failure is the oldest
        is forgotten."""
        attempts = self._recent_failed(key)
        attempts.append(datetime.now(timezone.utc))

        self.failed_login.pop(key, None)  # re-inserted last: the dict stays ordered by last failure
        self.failed_login[key] = attempts

        while len(self.failed_login) > self.max_tracked:
            del self.failed_login[next(iter(self.failed_login))]

        if len(attempts) == self.max_attempts:
            log(f"{self.label} '{key}' has been throttled, too many failed login attempts", self.log_level)

    def _recent_failed(self, key: str) -> list[datetime]:
        """Failed attempts for `key` still inside the rolling window. Also prunes the stored list as a side effect,
        removing it when none is left without changing its place in the dict order."""
        cutoff = datetime.now(timezone.utc) - self.window

        recent = [a for a in self.failed_login.get(key, []) if a > cutoff]
        if recent:
            self.failed_login[key] = recent
        else:
            self.forget_failings(key)

        return recent

    def forget_failings(self, key: str) -> None:
        """Forgets every failure of `key`: after a successful login, or once none is left in the window."""
        self.failed_login.pop(key, None)
