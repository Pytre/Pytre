from collections.abc import Callable
from threading import Lock
from typing import TypeVar

from Pytre_Core.kee import Kee
from Pytre_Core.settings import Settings
from Pytre_Core.servers import Servers
from Pytre_Core.users import Users, User
from Pytre_Core.user_prefs import UserPrefs
from Pytre_Core.logs_user import UserLogs


T = TypeVar("T")


class LazyContext:
    def __init__(self, attributes: tuple):
        self._locks: dict[str, Lock] = {}

        for attr in attributes:
            setattr(self, f"_{attr}", None)
            self._locks[attr] = Lock()

    def _get_or_create(self, name: str, factory: Callable[[], T]) -> T:
        value = getattr(self, f"_{name}")

        if value is None:
            with self._locks[name]:
                value = getattr(self, f"_{name}")  # rechecking in case attribute was created while waiting
                if value is None:
                    value = factory()
                    setattr(self, f"_{name}", value)

        return value


class AppContext(LazyContext):
    def __init__(self):
        attributes = ("kee", "settings", "servers", "users")
        super().__init__(attributes=attributes)

    @property
    def kee(self) -> Kee:
        return self._get_or_create("kee", Kee)

    @property
    def settings(self) -> Settings:
        return self._get_or_create("settings", lambda: Settings(self.kee))

    @property
    def servers(self) -> Servers:
        return self._get_or_create("servers", lambda: Servers(self.kee))

    @property
    def users(self) -> Users:
        return self._get_or_create("users", lambda: Users(self.kee))


class UserContext(LazyContext):
    def __init__(self, users: Users, username: str = ""):
        self._users: Users = users
        self._username: str = username

        attributes = ("current_user", "user_prefs", "user_logs")
        super().__init__(attributes=attributes)

    @property
    def current_user(self) -> User:
        return self._get_or_create("current_user", lambda: User(self._users, username=self._username))

    @property
    def user_prefs(self) -> UserPrefs:
        return self._get_or_create("user_prefs", UserPrefs)

    @property
    def user_logs(self) -> UserLogs:
        return self._get_or_create("user_logs", UserLogs)
