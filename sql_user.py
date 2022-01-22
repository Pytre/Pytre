import os, getpass

import settings_with_keepass as settings


SETTINGS = settings.Settings()
USERS = SETTINGS.users


class User:
    def __init__(self, name="", domain=""):
        self.name = name if name else self._get_user_name()
        self.domain = domain if domain else self._get_user_domain()

        if self.domain:
            self._domain_and_name = f"{self.domain}\\{self.name}"
        else:
            self._domain_and_name = self.name

        self.dict = USERS.get(self._domain_and_name, dict())
        self.exist_in_settings = True if self.dict else False
        self.is_authorized = True if self.dict else False

        self.title = self.dict.get("title", "")
        self.x3_id = self.dict.get("x3_id", "")
        self.msg_login = self.dict.get("msg_login", "")
        self.superuser = self.dict.get("superuser", False)

        self.other_attributes = [attr for attr in self.dict if not hasattr(self, attr)]
        for attr in self.other_attributes:
            setattr(self, attr, self.dict.get(attr, ""))

        if self.msg_login == "":
            self.msg_login = f"Bonjour {self.title.split(' ')[0]} !"

    def _get_user_name(self) -> str:
        return getpass.getuser()

    def _get_user_domain(self) -> str:
        return os.environ.get("userdnsdomain") or ""


if __name__ == "__main__":
    my_users = [User(), User("Inexistant"), User("ebrun", "PROSOL.PRI")]

    for my_user in my_users:
        print(str("=") * 50)

        if my_user.domain:
            print(f"{my_user.name} sur domaine {my_user.domain} :")
        else:
            print(f"{my_user.name} sur aucun domaine :")

        print(f"- autorisation : {my_user.is_authorized}")
        print(f"- superuser : {my_user.superuser}")
