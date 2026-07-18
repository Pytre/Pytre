import csv
import io
from uuid import UUID
from pathlib import Path
from enum import Enum

import pymssql
import psycopg2
from pykeepass.entry import Entry
from pykeepass.group import Group

from kee import Kee
from utils import detect_encoding
from about import APP_NAME, APP_VERSION


class Servers:
    def __init__(self, kee: Kee):
        self.kee: Kee = kee
        self.kee_grp: Group | None = None
        self.kee_users_grp: Group | None = None

        self.servers_dict: dict[str, Server] = {}
        self.groups: set[str] = set()

        self.cols_std = ["id", "description", "user", "password", "grp_authorized"]
        self.cols_cust = ["type", "charset", "database", "host", "port", "server", "login_timeout", "timeout"]

        self.get_all_servers()
        self.get_all_groups()

    def open_db(self, reload: bool = False) -> bool:
        self.kee._open_db(reload)
        if self.kee.is_ko:
            return False
        self.kee_grp = self.kee.grp_servers
        self.kee_users_grp = self.kee.grp_users
        return True

    def get_all_servers(self, reload: bool = False, grp_filter: list[str] | None = None) -> dict:
        self.open_db(reload)

        if self.kee_grp is None:
            return {}

        self.servers_dict: dict[str, Server] = {}
        for entry in self.kee_grp.entries:
            server = Server(entry=entry, servers=self)
            if grp_filter is None or not server.grp_authorized or set(server.grp_authorized) & set(grp_filter):
                self.servers_dict[str(entry.title)] = server

        return self.servers_dict

    def get_all_groups(self, reload: bool = False) -> set:
        self.open_db(reload)

        if self.kee_grp is None or self.kee_users_grp is None:
            return set()

        self.groups = set()
        for entry in self.kee_grp.entries + self.kee_users_grp.entries:
            tags = set(map(lambda val: val.lower().strip(), entry.tags)) if entry.tags else []
            self.groups.update(tags) if tags else None

        return self.groups

    def csv_import(self, filepath: Path | str, delimiter: str = ";", quotechar: str = '"') -> bool:
        if not (delimiter) or len(delimiter) != 1 or delimiter == quotechar:
            raise ValueError(f"Delimiter must be a single character and different from quote character ({quotechar})")
        if not (quotechar) or len(quotechar) != 1:
            raise ValueError("Quote character must be a single character")
        if not Path(filepath).exists():
            raise FileNotFoundError(f"File to import servers does not exist : {filepath}")

        self.open_db(True)
        if self.kee.db is None:
            raise RuntimeError("Database not opened, can't import csv")

        cols_dict = {}
        for i, val in enumerate(self.cols_std + self.cols_cust):
            cols_dict[val] = i

        encoding = detect_encoding(filepath)
        if not encoding:
            raise ValueError(f"Unable to detect encoding for: {filepath}")

        with open(filepath, mode="r", encoding=encoding) as csv_file:
            csv_reader = csv.DictReader(csv_file, delimiter=delimiter, quotechar=quotechar)

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
                s_entry: Entry | None = None
                if self.kee_grp:
                    s_entry = next((s for s in self.kee_grp.entries if csv_row["id"].upper() == s.title.upper()), None)  # type: ignore
                if s_entry is None:
                    s_entry = self.kee.db.add_entry(self.kee_grp, "", "", "")

                # mise à jour des propriétés
                s_entry.title = csv_row["id"]
                s_entry.username = csv_row["user"]
                s_entry.password = csv_row["password"]
                s_entry.notes = csv_row["description"]

                for prop in self.cols_cust:
                    s_entry.set_custom_property(prop, csv_row[prop])

                # récup des groupes en utilisant csv.reader pour parser les cas de groupe entre guillemets
                groups = csv_row["grp_authorized"].strip()
                if groups:
                    grp_reader = csv.reader([groups], delimiter=delimiter, quotechar=quotechar)
                    s_entry.tags = [grp.strip() for grp in next(grp_reader) if grp.strip()]
                    # remplacement caractères interdits par keepass
                    s_entry.tags = [grp.replace(",", "_").replace(";", "_") for grp in s_entry.tags]
                else:
                    s_entry.tags = []

            # une fois que toutes les entries sont à jour, sauvegarde de la base
            self.kee.save_db()

            return True

    def csv_export(self, filepath: Path | str, delimiter: str = ";", overwrite: bool = False) -> bool:
        quotechar = '"'

        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot export servers")

        if not delimiter or len(delimiter) != 1 or delimiter == quotechar:
            raise ValueError(f"Delimiter must be a single character and different from quote character ({quotechar})")
        if Path(filepath).exists() and not overwrite:
            raise FileExistsError(f"File to export servers already exists and overwrite is set to False : {filepath}")

        rows = [self.cols_std[:-1] + self.cols_cust + [self.cols_std[-1]]]
        self.open_db(True)
        for s_entry in self.kee_grp.entries:
            row = [s_entry.title or "", s_entry.notes or "", s_entry.username or "", s_entry.password or ""]

            for item in self.cols_cust:
                value = s_entry.get_custom_property(item)
                value = value if value is not None else ""
                row.append(value)

            # utilisation de csv.writer pour joindre les groupes et gérer les caractères similaires au delimiter
            tags = s_entry.tags if s_entry.tags else []
            if tags:
                output = io.StringIO()
                grp_writer = csv.writer(output, delimiter=delimiter, quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)
                grp_writer.writerow(tags)
                return_size = len(grp_writer.dialect.lineterminator)
                groups = output.getvalue()[:-return_size]  # suppression du retour à la ligne
            else:
                groups = ""
            row.append(groups)

            rows.append(row)

        with open(filepath, mode="w", encoding="utf-8-sig", newline="") as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=delimiter, quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerows(rows)

        return True

    def find_entry_by_uuid(self, uuid: str) -> Entry | None:
        if self.kee.db is None:
            raise RuntimeError("Database not opened, can't find user by uuid")
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot find server entry by uuid")

        entry = self.kee.db.find_entries(uuid=UUID(uuid), first=True)

        return entry if entry and isinstance(entry, Entry) else None


class ServerType(Enum):
    mssql = "SQL Server"
    postgre = "PostgreSQL"


class ServerConnectionError(Exception):
    """Connection error to server"""


class Server:
    def __init__(self, servers: Servers, entry: Entry | None = None):
        self.servers: Servers = servers

        self.uuid: str = ""
        self.id: str = ""
        self.description: str = ""
        self.user: str = ""
        self.password: str = ""
        self.type: str = ServerType.mssql.name
        self.charset: str = ""
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

    def get_connection(self) -> pymssql.Connection | psycopg2.extensions.connection:
        if self.type == ServerType.mssql.name:
            return self._conn_mssql()
        elif self.type == ServerType.postgre.name:
            return self._conn_postgre()
        else:
            raise ValueError("Unknown server type, getting a connection is not possible")

    def _conn_mssql(self) -> pymssql.Connection:
        conn_params = {
            "server": self.server,
            "host": self.host,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "timeout": self.timeout,
            "login_timeout": self.login_timeout,
            "charset": self.charset,
            "as_dict": False,
            "port": self.port,
            "read_only": True,
            "appname": f"{APP_NAME}_{APP_VERSION}",
        }
        try:
            conn: pymssql.Connection = pymssql.connect(**conn_params)
        except pymssql._pymssql.OperationalError as err:
            err_msg = err.args[0][1].decode("utf-8", errors="replace")
            raise ServerConnectionError(err_msg) from err

        return conn

    def _conn_postgre(self) -> psycopg2.extensions.connection:
        conn_params = {
            "hostaddr": self.server,
            "host": self.host,
            "user": self.user,
            "password": self.password,
            "dbname": self.database,
            "connect_timeout": self.login_timeout,
            "client_encoding": self.charset,
            "port": self.port,
            "application_name": f"{APP_NAME}_{APP_VERSION}",
        }
        try:
            conn: psycopg2.extensions.connection = psycopg2.connect(**conn_params)
            cursor = conn.cursor()
            cursor.execute("SET statement_timeout = %s", (self.timeout * 1000,))  # needs to be milliseconds
            cursor.execute("SET default_transaction_read_only = on")
            cursor.close()
        except psycopg2.OperationalError as err:
            err_msg = "".join(err.args[0])
            raise ServerConnectionError(err_msg) from err
        except UnicodeDecodeError as err:
            err_msg = err.args[1].decode(err.args[0], errors="replace")
            raise ServerConnectionError(err_msg) from err

        return conn

    def _load_from_entry(self, s_entry: Entry):
        self.uuid = str(s_entry.uuid)
        self.id = str(s_entry.title)
        self.description = str(s_entry.notes or "")

        self.user = str(s_entry.username or "")
        self.password = str(s_entry.password or "")

        for property in s_entry.custom_properties:
            val = str(val) if (val := s_entry.get_custom_property(property)) is not None else ""
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

        # modify charset if not set, depending on server type
        if self.charset:
            pass
        elif self.type == ServerType.mssql.name:
            self.charset: str = "UTF-8"
        elif self.type == ServerType.postgre.name:
            self.charset: str = "UTF8"

    def reload(self) -> None:
        self.servers.open_db(True)
        entry = self.servers.find_entry_by_uuid(self.uuid)
        if entry is not None:
            self._load_from_entry(entry)

    def save(self) -> bool:
        if not self.id or self.id == "":
            raise ValueError("Server must have an id")

        self.servers.open_db(True)
        if self.servers.kee.db is None:
            raise RuntimeError("Database not opened, can't save it")

        # contrôles
        if [s for s in self.servers.get_all_servers().values() if s.id == self.id and not s.uuid == self.uuid]:
            raise ValueError(f"{self.id} est déjà utilisé comme identifiant")
        if not str(self.timeout).isdigit():
            raise ValueError(f"La valeur de timeout doit être un nombre : {self.timeout}")
        if not str(self.login_timeout).isdigit():
            raise ValueError(f"La valeur de login_timeout doit être un nombre : {self.login_timeout}")

        # récupération d'une entrée pour la sauvegarde
        entry = self.servers.kee.db.find_entries(uuid=UUID(self.uuid), first=True) if self.uuid else None
        if not entry:
            entry = self.servers.kee.db.add_entry(self.servers.kee_grp, "", "", "")
            self.uuid = str(entry.uuid)
        elif not isinstance(entry, Entry):
            raise TypeError("Unexpected type from find_entries() with first=True")

        # mise à jour de l'entrée
        entry.title = self.id
        entry.notes = self.description
        entry.username = self.user
        entry.password = self.password

        for key, val in self.to_dict().items():
            if key in ["uuid", "id", "description", "user", "password", "grp_authorized"]:
                continue  # attributs qui ne sont pas dans custom_property
            else:
                entry.set_custom_property(key, val)

        # caractères interdits par keepass pour les tags : , et ;
        entry.tags = [grp.replace(",", "_").replace(";", "_") for grp in self.grp_authorized]

        self.servers.kee.save_db()
        self.servers.get_all_groups(reload=False)
        return True

    def delete(self) -> bool:
        self.servers.open_db()
        if self.servers.kee.db is None:
            raise RuntimeError("Database not opened, can't delete server")

        s_entry = self.servers.kee.db.find_entries(uuid=UUID(self.uuid), first=True)
        if s_entry is None:
            raise LookupError("Server not found")
        elif not isinstance(s_entry, Entry):
            raise TypeError("Unexpected type from find_entries() with first=True")

        s_entry.delete()
        self.servers.kee.save_db()

        return True


if __name__ == "__main__":
    kee = Kee()
    servers = Servers(kee)

    print("--------------")
    print("Servers list :")
    for _, self in servers.get_all_servers().items():
        print(self)
    print("--------------")

    print(f"Groups list : {servers.groups}")
