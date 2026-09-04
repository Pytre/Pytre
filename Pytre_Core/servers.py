import csv
import io
import logging
from uuid import UUID
from pathlib import Path
from enum import Enum

from pykeepass.entry import Entry
from pykeepass.group import Group

from Pytre_Core.kee import Kee
from Pytre_Core.utils import detect_encoding, log as _log


class _DbKind(Enum):
    NATIVE = "native"  # entry property / attribute
    CUSTOM = "custom"  # entry custom property
    TAGS = "tags"  # entry tags


class ServerField(Enum):
    """Registry of Server's predefined KeePass storage fields - single source of truth for where each one
    lives (db_kind/db_key), and for CSV which CSV col name is used (csv_header)."""

    ID = ("id", _DbKind.NATIVE, "title")
    DESCRIPTION = ("description", _DbKind.NATIVE, "notes")
    TYPE = ("type", _DbKind.CUSTOM, "type")
    USER = ("user", _DbKind.NATIVE, "username")
    PWD = ("password", _DbKind.NATIVE, "password")
    CHARSET = ("charset", _DbKind.CUSTOM, "charset")
    DATABASE = ("database", _DbKind.CUSTOM, "database")
    HOST = ("host", _DbKind.CUSTOM, "host")
    PORT = ("port", _DbKind.CUSTOM, "port")
    SERVER = ("server", _DbKind.CUSTOM, "server")
    LOGIN_TIMEOUT = ("login_timeout", _DbKind.CUSTOM, "login_timeout")
    TIMEOUT = ("timeout", _DbKind.CUSTOM, "timeout")
    GROUPS = ("grp_authorized", _DbKind.TAGS, "")

    def __init__(self, csv_header: str, db_kind: _DbKind, db_key: str):
        self.csv_header: str = csv_header
        self.db_kind: _DbKind = db_kind
        self.db_key: str = db_key

    @property
    def label(self) -> str:
        """Human-readable header label for csv_export()."""
        return self.csv_header.title()


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class Servers:
    def __init__(self, kee: Kee):
        self.kee: Kee = kee
        self.kee_grp: Group | None = None
        self.kee_users_grp: Group | None = None

        self.servers_dict: dict[str, Server] = {}  # authorized servers only
        self.groups: set[str] = set()

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

        servers_dict: dict[str, Server] = {}
        for entry in self.kee_grp.entries:
            server = Server(entry=entry, servers=self)
            servers_dict[str(entry.title)] = server

        self.servers_dict = filter_servers(servers_dict, grp_filter)

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

    def servers_add_groups(self, server_uuids: list[str] | tuple[str, ...], groups: list[str]):
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot add servers groups")

        self.open_db()

        save: bool = False

        groups_to_add = [group for group in groups if not group == "all"]
        for uuid in server_uuids:
            server = self.find_server_by_uuid(uuid)
            if server is None:
                continue

            new_groups = list(set(server.grp_authorized + groups_to_add))
            if not set(new_groups) == set(server.grp_authorized):
                server.grp_authorized = new_groups
                server.save(save_db=False)  # update db without committing changes
                save = True

        if save:
            self.kee.save_db()  # commit db changes to disk

    def servers_remove_groups(self, server_uuids: list[str] | tuple[str, ...], groups: list[str]):
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot remove servers groups")

        self.open_db()

        save: bool = False

        for uuid in server_uuids:
            server = self.find_server_by_uuid(uuid)
            if server is None:
                continue

            new_groups: list[str] = [g for g in server.grp_authorized if g not in groups]
            if not set(new_groups) == set(server.grp_authorized):
                server.grp_authorized = new_groups
                server.save(save_db=False)  # update db without committing changes
                save = True

        if save:
            self.kee.save_db()  # commit db changes to disk

    def csv_import(self, filepath: Path | str, delimiter: str = ";", quotechar: str = '"') -> bool:
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot import servers")

        if not (delimiter) or len(delimiter) != 1 or delimiter == quotechar:
            raise ValueError(f"Delimiter must be a single character and different from quote character ({quotechar})")
        if not (quotechar) or len(quotechar) != 1:
            raise ValueError("Quote character must be a single character")
        if not Path(filepath).exists():
            raise FileNotFoundError(f"File to import servers does not exist : {filepath}")

        self.open_db(True)
        if self.kee.db is None:
            raise RuntimeError("Database not opened, can't import csv")

        # retrieve existing servers
        entries_dict = {}
        s_entry: Entry
        for s_entry in self.kee_grp.entries:
            entries_dict[(s_entry.title or "").upper()] = s_entry

        # define expected columns
        cols_required = [f.csv_header for f in ServerField]

        # identify file encoding
        encoding = detect_encoding(filepath)
        if not encoding:
            raise ValueError(f"Unable to detect encoding for: {filepath}")

        # process import file
        with open(filepath, mode="r", encoding=encoding) as csv_file:
            csv_reader = csv.DictReader(csv_file, delimiter=delimiter, quotechar=quotechar)

            # csv fieldnames standardization
            csv_reader.fieldnames = [header.strip().lower() for header in csv_reader.fieldnames or []]

            # check for missing columns
            missing_cols = [col for col in cols_required if col not in csv_reader.fieldnames]
            if missing_cols:
                raise ValueError(f"Missing columns in file to import : {', '.join(missing_cols)}")

            # process rows
            for row_num, row in enumerate(csv_reader, start=2):
                server_id = row["id"].strip()
                if not server_id:
                    log(f"Ligne {row_num} ignorée : aucun id renseigné", logging.WARNING)
                    continue

                # create or retrieve db entry
                if server_id.upper() in entries_dict.keys():
                    s_entry = entries_dict[server_id.upper()]
                else:
                    s_entry = self.kee.db.add_entry(self.kee_grp, "", "", "")

                # update standard and custom properties
                for field in [f for f in ServerField if f.db_kind is _DbKind.NATIVE]:
                    value = row[field.csv_header]
                    if field is ServerField.PWD and not value:
                        continue  # preserve existing password when CSV column is blank
                    setattr(s_entry, field.db_key, value)

                for field in [f for f in ServerField if f.db_kind is _DbKind.CUSTOM]:
                    s_entry.set_custom_property(field.db_key, row[field.csv_header])

                # update groups using csv.reader to parse imported string
                groups = row["grp_authorized"].strip()
                if groups:
                    grp_reader = csv.reader([groups], delimiter=delimiter, quotechar=quotechar)
                    s_entry.tags = [grp.strip() for grp in next(grp_reader) if grp.strip()]
                    # replace forbidden keepass caracters (colon and semi colon)
                    s_entry.tags = [grp.replace(",", "_").replace(";", "_") for grp in s_entry.tags]
                else:
                    s_entry.tags = []

            # commit changes
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

        # Create and add header row to rows
        rows = [[f.label for f in ServerField]]

        # Start to construct rows list which will be saved to file
        self.open_db(True)
        for s_entry in self.kee_grp.entries:
            row = []

            # Convert groups list to string using csv.writer (avoid problem for caracters similar to delimiter)
            tags = s_entry.tags if s_entry.tags else []
            if tags:
                output = io.StringIO()
                grp_writer = csv.writer(output, delimiter=delimiter, quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)
                grp_writer.writerow(tags)
                return_size = len(grp_writer.dialect.lineterminator)
                groups = output.getvalue()[:-return_size]  # suppression du retour à la ligne
            else:
                groups = ""

            # Retrieve attr and append them to current row
            for field in ServerField:
                if field is ServerField.GROUPS:
                    value = groups
                elif field.db_kind is _DbKind.NATIVE:
                    value = getattr(s_entry, field.db_key) or ""
                else:  # db_kind == "custom"
                    value = s_entry.get_custom_property(field.db_key)

                row.append(value)

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

    def find_server_by_uuid(self, uuid: str) -> Server | None:
        entry = self.find_entry_by_uuid(uuid)
        return Server(self, entry=entry) if entry else None


class ServerType(Enum):
    mssql = "SQL Server"
    postgre = "PostgreSQL"


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

    def _load_from_entry(self, s_entry: Entry):
        # set attributes from keepass standard and custom properties
        self.uuid = str(s_entry.uuid)
        self.id = str(s_entry.title)
        self.description = str(s_entry.notes or "")
        self.user = str(s_entry.username or "")
        self.password = str(s_entry.password or "")

        self.type = s_entry.get_custom_property(ServerField.TYPE.db_key) or self.type
        self.database = s_entry.get_custom_property(ServerField.DATABASE.db_key) or self.database
        self.host = s_entry.get_custom_property(ServerField.HOST.db_key) or self.host
        self.port = s_entry.get_custom_property(ServerField.PORT.db_key) or self.port
        self.server = s_entry.get_custom_property(ServerField.SERVER.db_key) or self.server
        self.charset = (s_entry.get_custom_property(ServerField.CHARSET.db_key) or "").upper()  # force upper

        login_timeout = s_entry.get_custom_property(ServerField.LOGIN_TIMEOUT.db_key) or ""
        self.login_timeout = int(login_timeout) if login_timeout.isdigit() else self.login_timeout  # must be a number

        timeout = s_entry.get_custom_property(ServerField.TIMEOUT.db_key) or ""
        self.timeout = int(timeout) if timeout.isdigit() else self.timeout  # must be a number

        # Retrieve groups from keepass entry tags
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

    def save(self, save_db: bool = True) -> bool:
        """Updates the server's entry in memory. If `save_db` is False then save_db() is not called -
        the caller is responsible for persisting the change."""

        if not self.id or self.id == "":
            raise ValueError("Server must have an id")

        self.servers.open_db(save_db)  # batching (save_db=False) shouldn't reload to avoid discarding earlier changes
        if self.servers.kee.db is None:
            raise RuntimeError("Database not opened, can't save it")

        # control
        if [s for s in self.servers.get_all_servers().values() if s.id == self.id and not s.uuid == self.uuid]:
            raise ValueError(f"{self.id} est déjà utilisé comme identifiant")
        if not str(self.timeout).isdigit():
            raise ValueError(f"La valeur de timeout doit être un nombre : {self.timeout}")
        if not str(self.login_timeout).isdigit():
            raise ValueError(f"La valeur de login_timeout doit être un nombre : {self.login_timeout}")

        # Get existing entry or create a new one
        entry = self.servers.kee.db.find_entries(uuid=UUID(self.uuid), first=True) if self.uuid else None
        if not entry:
            entry = self.servers.kee.db.add_entry(self.servers.kee_grp, "", "", "")
            self.uuid = str(entry.uuid)
        elif not isinstance(entry, Entry):
            raise TypeError("Unexpected type from find_entries() with first=True")

        # Update entry
        entry.title = self.id
        entry.notes = self.description
        entry.username = self.user
        entry.password = self.password
        entry.set_custom_property(ServerField.TYPE.db_key, self.type)
        entry.set_custom_property(ServerField.CHARSET.db_key, self.charset)
        entry.set_custom_property(ServerField.DATABASE.db_key, self.database)
        entry.set_custom_property(ServerField.HOST.db_key, self.host)
        entry.set_custom_property(ServerField.PORT.db_key, self.port)
        entry.set_custom_property(ServerField.SERVER.db_key, self.server)
        entry.set_custom_property(ServerField.LOGIN_TIMEOUT.db_key, str(self.login_timeout))
        entry.set_custom_property(ServerField.TIMEOUT.db_key, str(self.timeout))

        # Sanitize grp, keepass tags forbid colon and semi colon
        entry.tags = [grp.replace(",", "_").replace(";", "_") for grp in self.grp_authorized]

        if save_db:
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


def filter_servers(servers: dict[str, Server], grp_filter: list[str] | None) -> dict[str, Server]:
    if grp_filter is None:
        return servers

    filtered_servers: dict[str, Server] = {}

    for id, server in servers.items():
        if grp_filter is None or not server.grp_authorized or set(server.grp_authorized) & set(grp_filter):
            filtered_servers[str(server.id)] = server

    return filtered_servers


if __name__ == "__main__":
    kee = Kee()
    servers = Servers(kee)

    print("--------------")
    print("Servers list :")
    for _, self in servers.get_all_servers().items():
        print(self)
    print("--------------")

    print(f"Groups list : {servers.groups}")
