import csv
import ipaddress
import json
import jwt
import logging
from contextlib import contextmanager
from collections.abc import Generator, Iterator
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Self, Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx2
from jwt import DecodeError

from Pytre_Core.keyring import KeyringCatalog
from Pytre_Core.user_prefs import CONFIG_FOLDER
from Pytre_Core.utils import detect_encoding, log as _log


REMOTES_FILE: Path = CONFIG_FOLDER / "Pytre_Remotes.json"  # file for loading and saving remotes
REMOTES_CIPHER: KeyringCatalog = KeyringCatalog.REMOTES_ENCRYPTION_KEY

_REQUEST_TIMEOUT = 5  # seconds - timeout for short operations
_EXEC_TIMEOUT = 15 * 60  # seconds - timeout for SQL executions


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


def encode_row_value(value: Any) -> Any:
    """JSON-safe encoding of a raw SQL value"""

    if isinstance(value, datetime):  # no native date type
        return {"$datetime": value.isoformat()}
    elif isinstance(value, date):  # no native date type
        return {"$date": value.isoformat()}
    elif isinstance(value, Decimal):  # JSON number would lose Decimal's exact precision
        return {"$decimal": str(value)}

    return value


def decode_row_value(value: Any) -> Any:
    """Reverses the server-side JSON wrapping"""
    if not isinstance(value, dict):
        return value

    if "$datetime" in value:
        return datetime.fromisoformat(value["$datetime"])
    elif "$date" in value:
        return date.fromisoformat(value["$date"])
    elif "$decimal" in value:
        return Decimal(value["$decimal"])

    return value


class RemoteExecutionError(Exception):
    """Raised when Pytre_Server reports a row conversion/serialization error via a NDJSON __error__ line."""


class Remotes:
    """Manages configured remotes of an user. Servers are not fetched on initialization."""

    def __init__(self, remotes_file: Path = REMOTES_FILE):
        self.remotes_file: Path = remotes_file
        self.remotes_dict: dict[str, Remote] = {}
        self.servers_dict: dict[str, RemoteServer] = {}

        self.get_all_remotes(reload=True)

    def get_all_remotes(self, reload: bool = False) -> dict[str, Remote]:
        if reload:
            self.load_remotes()

        return self.remotes_dict

    def get_all_servers(self, reload: bool = False) -> dict[str, RemoteServer]:
        servers: dict[str, RemoteServer] = {}
        for remote in self.remotes_dict.values():
            if reload:
                remote.refresh_servers()

            servers.update({s.id: s for s in remote.servers})

        self.servers_dict = servers

        return self.servers_dict

    def load_remotes(self) -> bool:
        """Load `remotes_dict` from a json file. Returns whether it succeeded."""
        json_dict = {}
        remotes_dict: dict[str, Remote] = {}
        load_issues: bool = False

        # read data from json
        if self.remotes_file.exists():
            try:
                with open(self.remotes_file, mode="r", encoding="utf-8") as f:
                    json_dict = json.load(f)
                    assert isinstance(json_dict, dict)
            except (json.JSONDecodeError, IOError) as e:
                log(f"Error while loading remotes json file : {e}", logging.WARNING)
                load_issues = True
            except AssertionError as e:
                msg = f"Content from remotes json file was exepected to be a dict but got {type(json_dict).__name__}"
                log(msg, logging.ERROR)
                load_issues = True

        # convert json data to a dictionary of remotes
        for key, val in json_dict.items():
            try:
                if key in remotes_dict:
                    log(f"Loaded remote from json ignored, uuid already exists : {val}")
                remotes_dict[key] = Remote(self, RemoteAddress.from_dict({"uuid": key} | val))
            except (TypeError, ValueError) as e:
                log(f"Remote json load error for {val}: {e}", logging.WARNING)
                load_issues = True

        self.remotes_dict = remotes_dict

        return not load_issues

    def save_remotes(self) -> bool:
        """Save `remotes_dict` to a json file. Only RemoteAddress is saved. Returns whether it succeeded."""
        remote_addresses: dict[str, dict] = {}
        for uuid, remote in self.remotes_dict.items():
            address_dict_without_uuid = {k: v for k, v in remote.address.to_dict().items() if not k == "uuid"}
            remote_addresses[uuid] = address_dict_without_uuid

        try:
            with open(self.remotes_file, mode="w", encoding="utf-8") as f:
                json.dump(remote_addresses, f, indent=2, ensure_ascii=False)
        except (OSError, PermissionError) as e:
            log(f"Error while saving remotes to json: {e}", logging.ERROR)
            return False

        return True

    def csv_import(self, filepath: Path | str, delimiter: str = ";", quotechar: str = '"') -> bool:
        """Import remotes from a csv file.
        If no password but remote already exists, keep its password."""

        if not (delimiter) or len(delimiter) != 1 or delimiter == quotechar:
            raise ValueError(f"Delimiter must be a single character and different from quote character ({quotechar})")
        if not (quotechar) or len(quotechar) != 1:
            raise ValueError("Quote character must be a single character")
        if not Path(filepath).exists():
            raise FileNotFoundError(f"File to import users does not exist : {filepath}")

        self.get_all_remotes(reload=True)

        # expected columns in file to be imported
        cols_required = ["url", "port", "username", "description"]
        cols_optional_pwd = ["password", "encrypted_pwd"]

        # detect encoding format
        encoding = detect_encoding(filepath)
        if not encoding:
            raise ValueError(f"Unable to detect encoding for: {filepath}")

        # load file to import
        with open(filepath, mode="r", encoding=encoding) as csv_file:
            csv_reader = csv.DictReader(csv_file, delimiter=delimiter, quotechar=quotechar)

            # fieldnames normalization
            csv_reader.fieldnames = [header.strip().lower() for header in csv_reader.fieldnames or []]

            # control no required columns is missing
            missing_cols = [col for col in cols_required if col not in csv_reader.fieldnames]
            if missing_cols:
                raise ValueError(f"Missing columns in file to import : {', '.join(missing_cols)}")

            # control that there is not both a password and an encrypted_pwd column
            pwd_cols = [col for col in cols_optional_pwd if col in csv_reader.fieldnames]
            if len(pwd_cols) > 1:
                raise ValueError("Import file can't have both a password and an encrypted_pwd column")

            # create remotes from file rows
            for row_num, row in enumerate(csv_reader, start=2):
                try:
                    remote_address = RemoteAddress.from_dict(row)
                except ValueError as err:
                    log(f"Ligne {row_num} ignorée : données incomplètes : {err}", logging.WARNING)
                    continue

                existing_remote = self.find_remote_from_id(remote_address.id)
                if existing_remote:
                    remote_address.uuid = existing_remote.uuid  # update UUID for imported remote with existing one
                    if remote_address.password == "":  # retrieve previous password if missing from imported remote
                        remote_address.password = existing_remote.address.password

                self.remotes_dict[remote_address.uuid] = Remote(self, remote_address)

            # save remotes to json
            self.save_remotes()

            return True

    def csv_export(
        self, filepath: Path | str, delimiter: str = ";", overwrite: bool = False, encrypt: bool = True
    ) -> bool:
        """Export remotes address. When `encrypt` is False passwords are not encrypted in the exported file."""

        quotechar = '"'

        if not delimiter or len(delimiter) != 1 or delimiter == quotechar:
            raise ValueError(f"Delimiter must be a single character and different from quote character ({quotechar})")
        if Path(filepath).exists() and not overwrite:
            raise FileExistsError(f"File to export users already exists and overwrite is set to False : {filepath}")

        pwd_col_name = "encrypted_pwd" if encrypt else "password"
        rows: list = [["url", "port", "username", pwd_col_name, "description"]]

        self.get_all_remotes(reload=True)
        for remote in self.remotes_dict.values():
            a = remote.address
            pwd = REMOTES_CIPHER.encrypt(a.password) if encrypt else a.password
            rows.append([a.url, a.port, a.username, pwd, a.description])

        with open(filepath, mode="w", encoding="utf-8-sig", newline="") as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=delimiter, quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerows(rows)

        return True

    def find_remote_from_uuid(self, uuid: str) -> Remote | None:
        """Find and return a remote from it's uuid"""
        return self.remotes_dict.get(uuid, None)

    def find_remote_from_id(self, id: str) -> Remote | None:
        """Find and return a remote from it's id (case insensitive)"""
        return next((r for r in self.remotes_dict.values() if r.id.lower() == id.lower()), None)


class Remote:
    """Local handle for a Pytre_Server instance.
    Holds the SQL servers it exposes - does not connect to a SQL server itself."""

    def __init__(self, remotes: Remotes | None, address: RemoteAddress, load: bool = False):
        """`remotes` is None for a Remote built only to carry an address - see RemoteServer.deserialize(),
        the only place this happens. save() / delete() raise RuntimeError in that case."""

        self.remotes: Remotes | None = remotes
        self.address: RemoteAddress = address
        self.servers: list[RemoteServer] = []

        if load:
            self.refresh_servers()

    @property
    def uuid(self):
        """Mirror the RemoteAddress which define this remote instance"""
        return self.address.uuid

    @property
    def id(self):
        """Mirror the RemoteAddress which define this remote instance : username@url:port"""
        return self.address.id

    @property
    def auth_headers(self) -> dict[str, str]:
        if not self.address.access_token:
            return {}
        return {"Authorization": f"Bearer {self.address.access_token}"}

    def login(self) -> bool:
        """Authenticates and store token on `address`. Returns whether it succeeded. Errors are logged, not raised.
        Refuses to send credentials over plain HTTP if not on localhost or on a private network."""

        if not self.address.channel_is_secure:
            msg = f"Login to a non-local remote is not allowed over plain HTTP: {self.id}"
            log(msg, logging.ERROR)
            return False

        # try to get an access token only if refresh_token still valid
        if self.address.refresh_token_is_valid:
            try:
                url_route = self.address.build_url("/refresh")
                body = {"refresh_token": self.address.refresh_token}
                response = httpx2.post(url=url_route, json=body, timeout=_REQUEST_TIMEOUT)
                response.raise_for_status()
                self.address.access_token = response.json()["access_token"]
                return True
            except (httpx2.RequestError, httpx2.HTTPStatusError) as e:
                log(f"Failed to retrieve an acces token for {self.id} : {e}", logging.DEBUG)
                self.address.access_token = ""

        # if retrieving access token failed then renew refresh token
        try:
            url_route = self.address.build_url("/login")
            body = {"username": self.address.username, "password": self.address.password}
            response = httpx2.post(url=url_route, json=body, timeout=_REQUEST_TIMEOUT)
            response.raise_for_status()
            self.address.refresh_token = response.json()["refresh_token"]
            self.address.access_token = response.json()["access_token"]
        except (httpx2.RequestError, httpx2.HTTPStatusError) as e:
            log(f"Login failed for {self.id} : {e}", logging.WARNING)
            self.address.refresh_token = ""
            self.address.access_token = ""
            return False

        return True

    def get_authenticated(self, url: str, params: dict | None = None) -> httpx2.Response:
        """GETs `url` using a valid Bearer token - logs in and retries once if the token is
        missing, expired, or invalidated (any of which the server reports as 401). Raises
        httpx2.RequestError on a login failure or network-level issue; a non-401 error status is
        returned as-is, for the caller to raise_for_status() itself."""

        response = None

        if self.address.access_token_is_valid:
            response = httpx2.get(url=url, params=params, headers=self.auth_headers, timeout=_REQUEST_TIMEOUT)

        if response is None or response.status_code == 401:
            if not self.login():
                raise httpx2.RequestError(f"Login failed for {self.id}")
            response = httpx2.get(url=url, params=params, headers=self.auth_headers, timeout=_REQUEST_TIMEOUT)

        return response

    def refresh_servers(self) -> list[RemoteServer]:
        """Fetches exposed SQL servers. On failure, logs a warning, sets last_error and keeps the previous list."""
        try:
            url_route = self.address.build_url(route="/servers")
            response = self.get_authenticated(url=url_route)
            response.raise_for_status()
            servers = response.json()
        except httpx2.RequestError as e:
            msg = f"Remote unreachable, retrieving servers from {self.id} failed : {e}"
            log(msg, logging.WARNING)
            self.last_error = msg
            return self.servers
        except httpx2.HTTPStatusError as e:
            msg = f"Couldn't retrieve remote servers from {self.id} : {e}"
            log(msg, logging.WARNING)
            self.last_error = msg
            return self.servers

        self.last_error = None
        self.servers = [RemoteServer(**s, remote=self) for s in servers]

        return self.servers

    def save(self) -> bool:
        """Save remote to preferences. If a `previous_id` exists then it will allow overwriting."""
        if self.remotes is None:
            err_msg = f"Can't save remote without a remotes collection set"
            log(err_msg, logging.ERROR)
            raise RuntimeError(err_msg)

        self.remotes.get_all_remotes(reload=True)

        # controls
        if any((r for uuid, r in self.remotes.remotes_dict.items() if r.id == self.id and uuid != self.uuid)):
            raise LookupError("Remote already exists")

        try:
            RemoteAddress.from_dict(self.address.to_dict())  # check if address can be created from current values
        except ValueError as err:
            raise

        # update remotes_list with current remote
        self.remotes.remotes_dict[self.uuid] = self

        # save remotes_list to user preferences
        self.remotes.save_remotes()

        return True

    def delete(self) -> bool:
        """Delete remotes matching id (case insensitive) from preferences"""
        if self.remotes is None:
            err_msg = f"Can't delete remote without a remotes collection set"
            log(err_msg, logging.ERROR)
            raise RuntimeError(err_msg)

        self.remotes.get_all_remotes(reload=True)

        remote = self.remotes.find_remote_from_uuid(self.uuid)
        if remote is None:
            raise LookupError("Remote not found")

        del self.remotes.remotes_dict[self.uuid]
        self.remotes.save_remotes()

        return True


class RemoteAddress:
    """Handles a remote definition infos"""

    def __init__(
        self,
        url: str,
        port: int,
        username: str,
        password: str = "",
        uuid: str = "",
        description: str = "",
        refresh_token: str = "",
    ):
        self.uuid: str = uuid if not uuid == "" else str(uuid4())
        self.url: str = url
        self.port: int = port
        self.username: str = username
        self.password: str = password
        self.description: str = description
        self.refresh_token: str = refresh_token
        self.access_token: str = ""  # in memory only, to_dict / from_dict don't use it

    @property
    def id(self) -> str:
        """Return as username@url:port"""
        for val in (self.url, self.port, self.username):
            if str(val).strip() == "":
                raise ValueError("All values to build id must be set")

        return f"{self.username.strip()}@{self.url.strip()}:{str(self.port).strip()}".lower()

    @property
    def channel_is_secure(self) -> bool:
        """True for https protocol or "localhost" or any private-network/loopback address
        (RFC 1918, 127.0.0.0/8, ::1...) - the only hosts allowed over plain HTTP."""
        parsed_url = urlparse(self.url)

        if parsed_url.scheme == "https":
            return True

        host = parsed_url.hostname or ""
        try:
            return host == "localhost" or ipaddress.ip_address(host).is_private
        except ValueError:
            return False

    @property
    def description_for_ui(self) -> str:
        """Return `description` if not empty else fallback to `id`"""
        return self.description or self.id

    @property
    def refresh_token_is_valid(self) -> bool:
        """Return whether the refresh token exists, well-formed and not expired."""
        return self._token_is_valid(self.refresh_token)

    @property
    def access_token_is_valid(self) -> bool:
        """Return whether the access token exists, well-formed and not expired."""
        return self._token_is_valid(self.access_token)

    def _token_is_valid(self, token: str, min_remaining: timedelta = timedelta(seconds=5)) -> bool:
        """Return whether the token exists, well-formed, and won't expire within `min_remaining`."""
        try:
            decoded = jwt.decode(token, options={"verify_signature": False, "verify_exp": True})
        except jwt.InvalidTokenError:
            return False

        exp = datetime.fromtimestamp(decoded.get("exp", 0), tz=timezone.utc)

        return datetime.now(timezone.utc) < exp - min_remaining

    def build_url(self, route: str = "") -> str:
        """Builds the URL to access a Remote. Returns the base URL if `route` is empty."""
        return f"{self.url}:{self.port}{route}"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "uuid": self.uuid,
            "url": self.url,
            "port": self.port,
            "username": self.username,
            "encrypted_pwd": REMOTES_CIPHER.encrypt(self.password),
            "description": self.description,
            "token": REMOTES_CIPHER.encrypt(self.refresh_token),
        }

    @classmethod
    def from_dict(cls, dict_attr: dict[str, str | int]) -> Self:
        if not isinstance(dict_attr, dict):
            msg = f"RemoteAddress.from_dict() expected dict got {type(dict_attr).__name__}"
            log(msg, logging.ERROR)
            raise TypeError(msg)

        # determine which password key is in dict_attr
        # pwd_key will be None unless password has been encrypted
        pwd_attr_vals = {"password": None, "encrypted_pwd": "encrypted_pwd"}

        pwd_attr_found = [v for k, v in pwd_attr_vals.items() if k in dict_attr.keys()]
        if len(pwd_attr_found) > 1:
            raise ValueError("Dict to build RemoteAddress can't have both password and encrypted_pwd")

        pwd_key = pwd_attr_found[0] if pwd_attr_found else None

        # key as class attribute, value as key from dict_attr (None if no difference)
        kwargs: dict = {
            "uuid": None,
            "url": None,
            "port": None,
            "username": None,
            "password": pwd_key,
            "description": None,
            "refresh_token": "token",
        }

        # loop kwargs to set args for init with value from dict_attr
        for key, dict_key in kwargs.items():
            val = dict_attr.get(dict_key or key)

            # control required attributes are not None
            if val is None and key in ("url", "port", "username"):
                msg = f"{key} is required to create a RemoteAddress"
                log(msg, logging.ERROR)
                raise ValueError(msg)
            else:
                val = val if val else ""  # convert None val for optional attributes

            if key == "uuid" and not val:
                val = str(uuid4())  # generate an uuid if missing
            elif key in ("url", "username") and val == "":
                msg = f"{key} can't be empty to create a RemoteAddress"
                log(msg, logging.ERROR)
                raise ValueError(msg)
            elif key == "port":
                msg = ""
                if not str(val).isdigit():
                    msg = f"port must be an integer to create a RemoteAddress, got {val}"
                elif str(val) == "0":
                    msg = f"port can't be 0 to create a RemoteAddress"
                if msg:
                    log(msg, logging.ERROR)
                    raise ValueError(msg)
                val = int(val or 0)  # or 0 to pass type checking
            elif key == "password":
                if pwd_key is not None:
                    val = REMOTES_CIPHER.decrypt(str(val))
            elif key == "refresh_token":
                val = REMOTES_CIPHER.decrypt(str(val))

            kwargs[key] = val

        # create and return a class instance
        return cls(**kwargs)


class RemoteServer:
    """A SQL server exposed by a Remote.
    Carries the owning Remote's `address` to allow routing to the right Remote when merged into a flat list."""

    def __init__(self, id: str, description: str, remote: Remote) -> None:
        self.id_on_remote: str = id
        self.description: str = description
        self.remote: Remote = remote

        self.serialized_queries: list[dict] = []
        self.last_error: str | None = None

    @property
    def id(self):
        """Return as id_on_remote|username@url:port"""
        return f"{self.id_on_remote}|{self.address.id}"

    @property
    def address(self) -> RemoteAddress:
        return self.remote.address

    @property
    def _auth_headers(self) -> dict[str, str]:
        return self.remote.auth_headers

    @contextmanager
    def execute(self, query_id: str, template_hash: str, parameters: dict[str, str]) -> Generator[RemoteCursor]:
        """Executes query_id via POST /execute, streaming NDJSON rows.
        The underlying HTTP connection closes as soon as the `with` block exits."""

        # check if server is available to avoid long timeout if it is not
        response = httpx2.get(url=self.address.build_url("/health"), timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()

        # proceed to query execution as the server has been confirmed to be available
        url = self.address.build_url("/execute")
        body = {
            "server_id": self.id_on_remote,
            "query_id": query_id,
            "template_hash": template_hash,
            "parameters": parameters,
        }

        max_login_retry = 1
        for i in range(max_login_retry + 1):
            with httpx2.stream("POST", url, json=body, headers=self._auth_headers, timeout=_EXEC_TIMEOUT) as response:
                if response.status_code == 401 and not i == max_login_retry and self.remote.login():
                    continue

                try:
                    response.raise_for_status()
                except httpx2.HTTPStatusError:
                    response.read()  # buffer the body as the connection closes as soon as `with` ends,
                    raise  # after that e.response.json()/.text would raise ResponseNotRead

                lines = response.iter_lines()
                first_line = next(lines, None)
                col_names = json.loads(first_line)["__columns__"] if first_line is not None else []

                yield RemoteCursor(tuple(col_names), self._iter_records(lines))
                break

    def _iter_records(self, lines: Iterator[str]) -> Generator[tuple]:
        for line in lines:
            if not line:
                continue

            record = json.loads(line)
            if "__error__" in record:
                error = record["__error__"]
                raise RemoteExecutionError(f"{error['type']}: {error['message']}")

            yield tuple(decode_row_value(v) for v in record["__row__"])

    def get_serialized_queries(self) -> list[dict]:
        """Fetches queries exposed by this remote server. Called on demand and not eagerly.
        On failure, logs a warning, sets last_error and keeps the previous list rather than clearing it."""
        id_for_error_msg = f"{self.address.build_url()} (server_id={self.id_on_remote})"

        try:
            url_route = self.address.build_url(route="/queries")
            params = {"server_id": self.id_on_remote}
            response = self.remote.get_authenticated(url=url_route, params=params)
            response.raise_for_status()
            serialized_queries = response.json()
        except httpx2.RequestError as e:
            msg = f"Remote unreachable, retrieving queries from {id_for_error_msg} failed : {e}"
            log(msg, logging.WARNING)
            self.last_error = msg
            return self.serialized_queries
        except httpx2.HTTPStatusError as e:
            msg = f"Couldn't retrieve queries from {id_for_error_msg} : {e}"
            log(msg, logging.WARNING)
            self.last_error = msg
            return self.serialized_queries

        self.last_error = None
        self.serialized_queries = serialized_queries
        return self.serialized_queries

    def serialize(self) -> dict:
        """Only carries what execute() needs standalone: id_on_remote, description, and address
        (RemoteAddress.to_dict() itself excludes the in-memory-only token). Everything else -
        serialized_queries/last_error, and the rest of `remote` (its Remotes collection, sibling
        servers) - is dropped, not needed for the execution round-trip - see Query.serialize()."""

        return {
            "id_on_remote": self.id_on_remote,
            "description": self.description,
            "address": self.address.to_dict(),
        }

    @classmethod
    def deserialize(cls, data: dict) -> Self:
        """Rebuilds id_on_remote, description, address only - serialized_queries/last_error stay
        at their defaults, and the reconstructed Remote has no Remotes collection (remotes=None),
        since this is only used for the execution round-trip - see Query.serialize()."""

        address = RemoteAddress.from_dict(data["address"])
        remote = Remote(None, address)
        return cls(data["id_on_remote"], data["description"], remote)


class RemoteCursor:
    """Wraps the row iterator from RemoteServer.execute() with the columns already parsed out.
    Lets QueryExecute read `.description` the same way as a local cursor's `.description`"""

    def __init__(self, col_names: tuple[str, ...], rows: Generator[tuple]) -> None:
        self._col_names: tuple[str, ...] = col_names
        self._rows: Generator[tuple] = rows

    def __iter__(self) -> Generator[tuple]:
        return self._rows

    @property
    def description(self) -> tuple[Any, ...]:
        """Describes columns the same way a local cursor's `.description` would.
        Only column names are available - other fields are always None."""
        return tuple((col_name, None, None, None, None, None, None) for col_name in self._col_names)
