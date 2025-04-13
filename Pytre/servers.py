import csv
from pathlib import Path
from enum import Enum

from pykeepass.entry import Entry
from pykeepass.group import Group

from kee import Kee
from singleton_metaclass import Singleton


class Servers(metaclass=Singleton):
    def __init__(self):
        self.kee: Kee = Kee()
        self.kee_grp_name: str = "Serveurs"
        self.kee_grp: Group = None

        self.servers_dict: dict[str, Server] = {}

        self.cols_std = ["id", "description", "user", "password", "grp_authorized"]
        self.cols_cust = ["type", "charset", "database", "host", "port", "server", "login_timeout", "timeout"]

        self.get_all_servers()

    def open_db(self, reload: bool = False) -> bool:
        self.kee._open_db(reload)
        if self.kee.is_ko:
            return False
        self.kee_grp = self.kee.db.find_groups(name=self.kee_grp_name, first=True)
        return True

    def get_all_servers(self, reload: bool = False, grp_filter: list[str] = None) -> dict:
        self.open_db(reload)

        self.servers_dict = {}
        for entry in self.kee_grp.entries:
            server = Server(entry=entry, servers=self)
            if grp_filter is None or not server.grp_authorized or set(server.grp_authorized) & set(grp_filter):
                self.servers_dict[entry.title] = Server(entry=entry, servers=self)

        return self.servers_dict

    def find_server_entry(self, server_id: str) -> Entry | None:
        for entry in self.kee_grp.entries:
            if entry.title and server_id.upper() == entry.title.upper():
                return entry

        return None

    def csv_import(self, filename: Path, delimiter: str = ";") -> bool:
        self.open_db(True)

        cols_dict = {}
        for i, val in enumerate(self.cols_std + self.cols_cust):
            cols_dict[val] = i

        with open(filename, mode="r", encoding="latin-1") as csv_file:
            csv_reader = csv.DictReader(csv_file, delimiter=delimiter, quotechar='"')

            for num, csv_row in enumerate(csv_reader):
                # contrôle si toutes les colonnes nécessaires sont présentes
                if num == 0:
                    missing_cols: list = []
                    for col in self.cols_std + self.cols_cust:
                        if col not in csv_row.keys():
                            missing_cols.append(col)
                    if missing_cols:
                        raise KeyError(f"Colonnes manquantes : {', '.join(missing_cols)}")

                # récup entry si déjà ou existant ou création d'une nouvelle
                s_entry: Entry
                s_entry = [s for s in self.kee_grp.entries if csv_row["id"].upper() == s.title.upper()]
                s_entry = s_entry[0] if s_entry else self.kee.db.add_entry(self.kee_grp, "", "", "")

                # mise à jour des propriétés
                s_entry.title = csv_row["id"]
                s_entry.username = csv_row["user"]
                s_entry.password = csv_row["password"]
                s_entry.notes = csv_row["description"]

                for prop in self.cols_cust:
                    s_entry.set_custom_property(prop, csv_row[prop])

                groups = csv_row["grp_authorized"].split(delimiter)
                s_entry.tags = [group for group in groups]

            # une fois que toutes les entries sont à jour, sauvegarde de la base
            self.kee.save_db()

            return True

    def csv_export(self, filename: Path, delimiter: str = ";", overwrite: bool = False) -> bool:
        if Path(filename).exists() and not overwrite:
            return False

        rows = [self.cols_std[:-1] + self.cols_cust + [self.cols_std[-1]]]
        self.open_db(True)
        s_entry: Entry | None
        for s_entry in self.kee_grp.entries:
            # ignorer l'info du serveur par défaut
            if s_entry.title == self.default_title:
                continue

            row = [s_entry.title, s_entry.notes, s_entry.username, s_entry.password]

            for item in self.cols_cust:
                value = s_entry.get_custom_property(item)
                value = value if value is not None else ""
                row.append(value)

            groups = delimiter.join(s_entry.tags if s_entry.tags else [])
            row.append(groups)

            rows.append(row)

        with open(filename, mode="w", encoding="latin-1", newline="") as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=delimiter, quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerows(rows)

        return True


class ServerType(Enum):
    mssql = "SQL Server"
    # postgre = "PostgreSQL"


class Server:
    def __init__(self, entry: Entry = None, servers: Servers = None):
        self.servers: Servers = servers or Servers()

        self.uuid: str = ""
        self.id: str = ""
        self.description: str = ""
        self.user: str = ""
        self.password: str = ""
        self.type: str = list(ServerType)[0].name
        self.charset: str = "UTF-8"
        self.database: str = ""
        self.host: str = ""
        self.port: str = "1433"  # string attendu et pas int
        self.server: str = ""
        self.login_timeout: int = 60
        self.timeout: int = 300
        self.grp_authorized: list[str] = []

        if entry:
            self._load_from_entry(entry)

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "id": self.id,
            "description": self.description,
            "user": self.user,
            "password": self.password,
            "type": self.type,
            "charset": self.charset,
            "database": self.database,
            "host": self.host,
            "port": self.port,
            "server": self.server,
            "login_timeout": self.login_timeout,
            "timeout": self.timeout,
            "grp_authorized": self.grp_authorized,
        }

    def __repr__(self) -> str:
        return str(self.to_dict())

    def __str__(self) -> str:
        return str(self.to_dict())

    def _load_from_entry(self, s_entry: Entry):
        self.uuid = s_entry.uuid
        self.id = s_entry.title
        self.description = val if (val := s_entry.notes) is not None else ""

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

        # récup info groupes autorisés
        tags = s_entry.tags if s_entry.tags else []
        for tag in tags:
            group = tag.lower().strip()
            self.grp_authorized.append(group)

    def reload(self) -> None:
        self.servers.open_db(True)
        self.load()

    def save(self) -> bool:
        self.servers.open_db(True)

        # contrôle
        if [s for s in self.servers.get_all_servers().values() if s.id == self.id and not s.uuid == self.uuid]:
            raise ValueError(f"{self.id} est déjà utilisé comme identifiant")

        # récupération d'une entrée pour la sauvegarde
        entry: Entry = self.servers.kee.db.find_entries(uuid=self.uuid, first=True) if self.uuid else None
        if not entry:
            entry: Entry = self.servers.kee.db.add_entry(self.servers.kee_grp, "", "", "")
            self.uuid = entry.uuid

        # mise à jour de l'entrée
        entry.title = self.id
        entry.notes = self.description
        entry.username = self.user
        entry.password = self.password

        for key, val in self.to_dict().items():
            if key in ["uuid", "id", "description", "user", "password", "grp_authorized"]:
                continue  # attributs qui ne sont pas dans custom_property
            elif key in ["timeout", "login_timeout"]:
                if val.isdigit():
                    entry.set_custom_property(key, val)
                else:
                    return False
            else:
                entry.set_custom_property(key, val)

        entry.tags = [grp for grp in self.grp_authorized]

        self.servers.kee.save_db()
        return True

    def delete(self) -> bool:
        self.servers.open_db()

        s_entry: Entry = self.servers.kee.db.find_entries(title=self.id, uuid=self.uuid, first=True)
        if s_entry is None:
            raise LookupError("Server not found")

        s_entry.delete()
        self.servers.kee.save_db()

        return True


if __name__ == "__main__":
    server = Server()
    print(server)

    servers = Servers()
    for _, server in servers.get_all_servers().items():
        print(server)
