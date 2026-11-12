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
from Pytre_Server.auth_token import TokenType


THROTTLE_WINDOW = timedelta(minutes=15)  # rolling: a key is released once enough of its failures get older

USER_IP_MAX_ATTEMPTS = 5  # one username from one address: a user mistyping only throttles themselves
USER_IP_MAX_TRACKED = 10_000  # bounds memory during a flood, the stalest one is forgotten first

USER_MAX_ATTEMPTS = 50  # one username from every address: caps a brute force spread over many addresses
USER_MAX_TRACKED = 10_000  # bounds memory during a flood, the stalest one is forgotten first

IP_MAX_ATTEMPTS = 20  # many users may share one address (RDS/Citrix server, NAT, outgoing proxy)
IP_MAX_TRACKED = 10_000  # bounds memory during a flood, the stalest one is forgotten first

KNOWN_ADDRESS_LIFETIME = TokenType.REFRESH.value  # as long as the refresh token the address obtained
KNOWN_ADDRESSES_PER_USER = 10  # the most recent ones
KNOWN_ADDRESSES_MAX_USERS = 10_000  # bounds memory, the username with the stalest proof is forgotten first


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class LoginThrottle:
    """Failed logins counted per username from one address, per username from every address, and per address.

    Login routes ask is_throttled() before checking a password, then report the outcome with record_failure() or
    record_success(), and /refresh reports a valid token with remember_address().

    Throttling a username from every address can't lock its user out: the addresses it was recently used from are
    exempted. Only a username's count from one address is reset by a successful login: resetting its count from
    every address, or the count of the address, would let an attacker with a valid account restart them."""

    def __init__(self) -> None:
        self.user_ip = _FailureCounter("User on address", USER_IP_MAX_ATTEMPTS, USER_IP_MAX_TRACKED, logging.INFO)
        self.user = _FailureCounter("User", USER_MAX_ATTEMPTS, USER_MAX_TRACKED, logging.WARNING)
        self.ip = _FailureCounter("Address", IP_MAX_ATTEMPTS, IP_MAX_TRACKED, logging.WARNING)
        self.known_addresses = _KnownAddresses()

    def is_throttled(self, username: str, host: str | None) -> bool:
        """True within the rolling window if:
        - the address `host` has hit IP_MAX_ATTEMPTS failures,
        - or `username` from `host` has hit USER_IP_MAX_ATTEMPTS failures,
        - or `username` from every address has hit USER_MAX_ATTEMPTS failures while `host` isn't one of its known
        addresses."""
        user_key = self._user_key(username)
        ip_key = self._ip_key(host)
        user_ip_key = self._user_ip_key(username, host)

        if self.ip.is_throttled(ip_key) or self.user_ip.is_throttled(user_ip_key):
            return True

        return self.user.is_throttled(user_key) and not self.known_addresses.is_known(user_key, ip_key)

    def record_failure(self, username: str, host: str | None) -> None:
        """Records a failed login of `username` from the address `host`, in every count."""
        user_key = self._user_key(username)
        ip_key = self._ip_key(host)
        user_ip_key = self._user_ip_key(username, host)

        self.user_ip.record_failing(user_ip_key)
        self.user.record_failing(user_key)
        self.ip.record_failing(ip_key)

    def record_success(self, username: str, host: str | None) -> None:
        """Records a successful login of `username` from the address `host`: forgets the failures of `username` from
        `host`, and makes `host` one of its known addresses. The other counts are kept."""
        user_key = self._user_key(username)
        ip_key = self._ip_key(host)
        user_ip_key = self._user_ip_key(username, host)

        self.user_ip.forget_failings(user_ip_key)
        self.known_addresses.remember(user_key, ip_key)

    def remember_address(self, username: str, host: str | None) -> None:
        """Makes the address `host` one of the known addresses of `username`, without resetting any count - for a
        valid refresh token: it proves legitimacy, but resetting the count of `username` from `host` at each refresh
        would give an attacker sharing that address new attempts."""
        user_key = self._user_key(username)
        ip_key = self._ip_key(host)

        self.known_addresses.remember(user_key, ip_key)

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

    @staticmethod
    def _user_ip_key(username: str, host: str | None) -> str:
        """Key counting the failed logins of a username from one address, e.g. "user@10.0.0.1". An address key has
        no "@", so the key stays unique even for a username that has one (an email address)."""
        user_key = LoginThrottle._user_key(username)
        ip_key = LoginThrottle._ip_key(host)

        return f"{user_key}@{ip_key}"


@dataclass(slots=True)
class _FailureCounter:
    """Failed logins counted per key (a username, a client address...) within a rolling window. At most `max_tracked`
    keys are kept: beyond, the one whose last failure is the oldest is forgotten."""

    label: str  # names the keys in the logs, e.g. "User"
    max_attempts: int
    max_tracked: int
    log_level: int = logging.INFO
    window: timedelta = THROTTLE_WINDOW
    failed_login: dict[str, list[datetime]] = field(default_factory=dict, repr=False)  # UTC, ordered by last failure

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


@dataclass(slots=True)
class _KnownAddresses:
    """Addresses each username was recently proved legitimate from, by a successful login or a valid refresh token.
    Each username keeps its `max_per_user` most recent ones for `lifetime`. At most `max_users` usernames are kept:
    beyond, the one whose last proof is the oldest is forgotten."""

    lifetime: timedelta = KNOWN_ADDRESS_LIFETIME
    max_per_user: int = KNOWN_ADDRESSES_PER_USER
    max_users: int = KNOWN_ADDRESSES_MAX_USERS
    addresses: dict[str, dict[str, datetime]] = field(default_factory=dict, repr=False)  # user-> ip->last proof (UTC)

    def is_known(self, user_key: str, ip_key: str) -> bool:
        """True if `user_key` was proved legitimate from `ip_key` within `lifetime`."""
        last_proof = self.addresses.get(user_key, {}).get(ip_key)
        return last_proof is not None and datetime.now(timezone.utc) - last_proof < self.lifetime

    def remember(self, user_key: str, ip_key: str) -> None:
        """Records that `user_key` was just proved legitimate from `ip_key`."""
        now = datetime.now(timezone.utc)

        user_addresses = self.addresses.pop(user_key, {})  # re-inserted last: both dicts stay ordered by last proof
        user_addresses.pop(ip_key, None)
        user_addresses[ip_key] = now

        user_addresses = {ip: proof for ip, proof in user_addresses.items() if now - proof < self.lifetime}
        while len(user_addresses) > self.max_per_user:
            del user_addresses[next(iter(user_addresses))]

        self.addresses[user_key] = user_addresses
        while len(self.addresses) > self.max_users:
            del self.addresses[next(iter(self.addresses))]
