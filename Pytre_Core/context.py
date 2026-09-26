from collections.abc import Callable
from threading import Lock

from Pytre_Core.kee import Kee
from Pytre_Core.settings import Settings
from Pytre_Core.servers import Servers
from Pytre_Core.oidc_providers import OidcProviders
from Pytre_Core.users import Users, User
from Pytre_Core.user_prefs import UserPrefs
from Pytre_Core.logs_user import UserLogs
from Pytre_Core.remotes import Remotes


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
    def __init__(self, master_key: str = ""):
        """`master_key` is the key to access keepass database"""

        attributes = ("kee", "settings", "servers", "users", "oidc_providers")
        super().__init__(attributes=attributes)

        self._master_key = master_key

    @property
    def kee(self) -> Kee:
        return self._get_or_create("kee", lambda: Kee(password=self._master_key))

    @property
    def settings(self) -> Settings:
        return self._get_or_create("settings", lambda: Settings(self.kee))

    @property
    def servers(self) -> Servers:
        return self._get_or_create("servers", lambda: Servers(self.kee))

    @property
    def users(self) -> Users:
        return self._get_or_create("users", lambda: Users(self.kee))

    @property
    def oidc_providers(self) -> OidcProviders:
        return self._get_or_create("oidc_providers", lambda: OidcProviders(self.kee))


class UserContext(LazyContext):
    def __init__(self, users: Users, username: str = "", current_user_override: User | None = None):
        """`current_user_override`, when given, is returned as-is by `current_user`."""
        self._users: Users = users
        self._username: str = username
        self._current_user_override: User | None = current_user_override

        self._base: UserContext | None = None  # provides prefs, logs and remotes when set by with_remote_user_infos()

        attributes = ("current_user", "user_prefs", "user_logs", "remotes")
        super().__init__(attributes=attributes)

    @property
    def current_user(self) -> User:
        if self._current_user_override:
            return self._current_user_override
        return self._get_or_create("current_user", lambda: User(self._users, username=self._username))

    @property
    def user_prefs(self) -> UserPrefs:
        if self._base:
            return self._base.user_prefs
        return self._get_or_create("user_prefs", UserPrefs)

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
        derived = UserContext(users=self._users, current_user_override=user)
        derived._base = self._base or self

        return derived
