import os, getpass


from settings import USERS


class User:
    def __init__(self):
        self.name = self._get_user_name()
        self.domain = self._get_user_domain()
        self._domain_and_name = str(self.domain) + "\\" + self.name

        self.is_authorized = self._is_user_authorized()

        self.x3_login = self.get_parameter("x3_login")
        self.msg_login = self.get_parameter("msg_login")

    def _get_user_name(self) -> str:
        return getpass.getuser()

    def _get_user_domain(self) -> str:
        return os.environ.get("userdnsdomain")

    def _is_user_authorized(self) -> bool:
        return True if self._domain_and_name in USERS else False

    def get_parameter(self, parameter: str) -> str:
        return USERS.get(self._domain_and_name).get(parameter)


if __name__ == "__main__":
    my_user = User()
    print(f"{my_user.name} for {my_user.domain}, checking authorization : " + str(my_user.is_authorized))
