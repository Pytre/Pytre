import logging
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from threading import Lock

from Pytre_Core.kee import Kee, KEE_FILE
from Pytre_Core.logs_user import UserLogs
from Pytre_Core.oidc_providers import OidcProviders
from Pytre_Core.remotes import Remotes
from Pytre_Core.servers import Servers
from Pytre_Core.settings import Settings
from Pytre_Core.user_prefs import DisplayFormat, UserPrefs
from Pytre_Core.users import Users, User
from Pytre_Core.utils import log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class AppMode(Enum):
    SERVER = "server"  # server: KeePass database
    HYBRID = "hybrid"  # client: shared KeePass database (e.g. on a network drive), plus the user's remotes
    CLIENT = "client"  # client: no KeePass database, user's remotes only


def detect_client_mode(kee_file: Path = KEE_FILE) -> AppMode:
    """Mode for the client: HYBRID if the KeePass database exists, CLIENT otherwise - never creates the database"""
    return AppMode.HYBRID if kee_file.exists() else AppMode.CLIENT


class LazyContext:
    def __init__(self, attributes: tuple):
        self._locks: dict[str, Lock] = {}

        for attr in attributes:
            setattr(self, f"_{attr}", None)
            self._locks[attr] = Lock()

    def _get_or_create[T](self, name: str, factory: Callable[[], T]) -> T:
        value = getattr(self, f"_{name}")

        if value is None:
            with self._locks[name]:
                value = getattr(self, f"_{name}")  # rechecking in case attribute was created while waiting
                if value is None:
                    value = factory()
                    setattr(self, f"_{name}", value)

        return value


class AppContext(LazyContext):
    def __init__(self, master_key: str = "", mode: AppMode = AppMode.SERVER):
        """`master_key` is the key to access keepass database.
        In CLIENT `mode` there is no keepass database: kee, settings, servers, users and oidc_providers raise."""

        attributes = ("kee", "settings", "servers", "users", "oidc_providers")
        super().__init__(attributes=attributes)

        self._master_key: str = master_key
        self.mode: AppMode = mode

    @property
    def acts_as_server(self) -> bool:
        return self.mode in (AppMode.HYBRID, AppMode.SERVER)

    def _require_server(self, name: str) -> None:
        if not self.acts_as_server:
            err_msg = f"'{name}' is not available in {self.mode.value} mode"
            log(err_msg, logging.CRITICAL)
            raise RuntimeError(err_msg)

    @property
    def kee(self) -> Kee:
        self._require_server("kee")
        return self._get_or_create("kee", lambda: Kee(password=self._master_key))

    @property
    def settings(self) -> Settings:
        self._require_server("settings")
        return self._get_or_create("settings", lambda: Settings(self.kee))

    @property
    def servers(self) -> Servers:
        self._require_server("servers")
        return self._get_or_create("servers", lambda: Servers(self.kee))

    @property
    def users(self) -> Users:
        self._require_server("users")
        return self._get_or_create("users", lambda: Users(self.kee))

    @property
    def oidc_providers(self) -> OidcProviders:
        self._require_server("oidc_providers")
        return self._get_or_create("oidc_providers", lambda: OidcProviders(self.kee))


class UserContext(LazyContext):
    def __init__(
        self,
        users: Users | None,
        username: str = "",
        user_override: User | None = None,
        display_format_override: DisplayFormat | None = None,
    ):
        """`users` should be None (no users database) in CLIENT mode.
        `user_override`, when given, is returned as-is by `user`.
        `display_format_override`, when given, is returned as-is by `display_format`."""
        self._users: Users | None = users
        self._username: str = username
        self._user_override: User | None = user_override
        self._display_format_override: DisplayFormat | None = display_format_override

        self._base: UserContext | None = None  # provides prefs, logs and remotes when set by with_remote_user_infos()

        attributes = ("user", "user_prefs", "user_logs", "remotes")
        super().__init__(attributes=attributes)

    @property
    def user(self) -> User:
        if self._user_override:
            return self._user_override
        return self._get_or_create("user", lambda: User(self._users, username=self._username))

    @property
    def user_prefs(self) -> UserPrefs:
        if self._base:
            return self._base.user_prefs
        return self._get_or_create("user_prefs", UserPrefs)

    @property
    def display_format(self) -> DisplayFormat:
        if self._display_format_override is not None:
            return self._display_format_override
        return self.user_prefs.display_format

    @property
    def user_logs(self) -> UserLogs:
        if self._base:
            return self._base.user_logs
        return self._get_or_create("user_logs", UserLogs)

    @property
    def remotes(self) -> Remotes:
        if self._base:
            return self._base.remotes
        return self._get_or_create("remotes", lambda: Remotes())

    def with_remote_user_infos(self, user_infos: dict | None) -> UserContext:
        """Same context, but override current_user with a remote's user_infos.
        All other attributes are shared with `self` and stay lazy.
        Returns `self` if `user_infos` is None or empty."""

        if not user_infos:
            return self

        user = User.create_from_remote_infos(self._users, user_infos)
        derived = UserContext(
            users=self._users,
            user_override=user,
            display_format_override=self._display_format_override,
        )
        derived._base = self._base or self

        return derived
