from pykeepass.entry import Entry
from pykeepass.group import Group

from kee import Kee


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)

        return cls._instances[cls]


class Servers(metaclass=Singleton):
    def __init__(self):
        self.kee: Kee = Kee()
        self.kee_grp_name: str = "Serveurs"
        self.kee_grp: Group = None

        self.default_title: str = "Default"
        self.default_id: str = ""
        self.servers_dict: dict[str, Server] = {}

        self.set_default_id()

    def open_db(self, reload: bool = False) -> bool:
        self.kee._open_db(reload)
        if self.kee.is_ko:
            return False
        self.kee_grp = self.kee.db.find_groups(name=self.kee_grp_name, first=True)
        return True

    def set_default_id(self, reload=False) -> str:
        if not self.open_db(reload):
            return ""

        s_entry: Entry = self.kee.db.find_entries(path=[self.kee_grp_name, self.default_title])
        if s_entry:
            # TODO : migrer ancien paramètres d'une base pour supporter le multi serveur
            self.default_id = ""
            if s_entry.custom_properties:  # si monobase retenir default_title
                self.default_id = s_entry.title
            else:  # sinon default_title indique le default_id en username
                self.default_id = s_entry.username

        return self.default_id

    def get_all_servers(self, reload: bool = False) -> dict:
        self.open_db(reload)

        self.servers_dict = {}
        for entry in self.kee_grp.entries:
            # vérification si l'entrée défaut n'a pas des infos de connection
            # si c'est le cas alors on est en ancienne version et on l'ajoute
            if not entry.title == self.default_title or entry.custom_properties:
                self.servers_dict[entry.title] = Server(entry=entry)

        return self.servers_dict


class Server:
    def __init__(self, title: str = "", entry: Entry = None):
        self.servers: Servers = Servers()

        self.id: str = ""
        self.user: str = ""
        self.password: str = ""
        self.charset: str = "UTF-8"
        self.database: str = ""
        self.host: str = ""
        self.port: str = "1433"  # string attendu et pas int
        self.server: str = ""
        self.login_timeout: int = 60
        self.timeout: int = 300

        if not self.servers.default_id:
            self.servers.set_default_id()

        if entry:
            self._load_from_entry(entry)
        else:
            self.id = title if title else self.servers.default_id
            self.load()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user": self.user,
            "password": self.password,
            "charset": self.charset,
            "database": self.database,
            "host": self.host,
            "port": self.port,
            "server": self.server,
            "login_timeout": self.login_timeout,
            "timeout": self.timeout,
        }

    def __repr__(self) -> str:
        return str(self.to_dict())

    def __str__(self) -> str:
        return str(self.to_dict())

    def load(self):
        if not self.servers.open_db():
            return

        s_entry: Entry = self.servers.kee.db.find_entries(path=[self.servers.kee_grp_name, self.id])
        if s_entry:
            self._load_from_entry(s_entry)

    def _load_from_entry(self, s_entry: Entry):
        self.id = s_entry.title

        self.user = val if (val := s_entry.username) is not None else ""
        self.password = val if (val := s_entry.password) is not None else ""

        for property in s_entry.custom_properties:
            val: str = val if (val := s_entry.get_custom_property(property)) is not None else ""
            if property == "charset":
                val = val.upper() if val else self.charset
            elif property == "login_timeout":
                val = int(val) if val.isdigit() else self.login_timeout
            elif property == "timeout":
                val = int(val) if val.isdigit() else self.timeout

            setattr(self, property, val)

    def reload(self) -> None:
        self.servers.open_db(True)
        self.load()

    def save(self) -> bool:
        self.servers.open_db()

        entry: Entry = self.servers.kee.db.find_entries(path=[self.servers.kee_grp_name, self.id])
        if entry:
            entry.title = self.id
            entry.username = self.user
            entry.password = self.password
        else:
            entry: Entry = self.servers.kee.db.add_entry(
                self.servers.kee_grp, self.id, self.user, password=self.password
            )

        for key, val in self.to_dict().items():
            if key in ["title", "user", "password"]:
                continue  # déjà mis à jour avec la création ou recherche de l'entrée
            elif key in ["timeout", "login_timeout"]:
                if val.isdigit():
                    entry.set_custom_property(key, val)
                else:
                    return False
            else:
                entry.set_custom_property(key, val)

        self.servers.kee.save_db()
        return True


if __name__ == "__main__":
    # server = Server()
    # print(server)

    servers = Servers()
    for _, server in servers.get_all_servers().items():
        print(server)
