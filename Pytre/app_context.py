from collections.abc import Callable
from threading import Lock
from typing import TypeVar

from kee import Kee
from settings import Settings
from servers import Servers
from users import Users, CurrentUser
from user_prefs import UserPrefs
from logs_user import UserDb


T = TypeVar("T")


class AppContext:
    def __init__(self):
        attributes = ("kee", "settings", "servers", "users", "current_user", "user_prefs", "user_db")

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

    @property
    def current_user(self) -> CurrentUser:
        return self._get_or_create("current_user", lambda: CurrentUser(self.users))

    @property
    def user_prefs(self) -> UserPrefs:
        return self._get_or_create("user_prefs", UserPrefs)

    @property
    def user_db(self) -> UserDb:
        return self._get_or_create("user_db", UserDb)


def build_app_context() -> AppContext:
    return AppContext()
