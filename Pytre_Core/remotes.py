import csv
import ipaddress
import json
import jwt
import logging
from contextlib import contextmanager
from collections.abc import Generator, Iterator
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Self, Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx2

from Pytre_Core.app_folders import get_config_folder, get_install_folder, REMOTES_FILENAME
from Pytre_Core.keyring import KeyringCatalog
from Pytre_Core.policy import PlainHttpRemotesPolicy
from Pytre_Core.remote_protocol import decode_row_value, StreamKey
from Pytre_Core.user_prefs import DisplayFormat
from Pytre_Core.users import AuthType, Users
from Pytre_Core.utils import detect_encoding, log as _log


MANAGED_REMOTES_FILENAME: str = "Pytre.remotes.json"  # remotes deployed by the administrator, next to the executable
MANAGED_FIELDS = ("url", "port", "description", "auth_method", "allow_unsecure_channel")  # the file's, kept in sync
_SECRET_KEYS = ("password", "encrypted_pwd", "token")  # keys to never read from the managed remotes file

REMOTES_CIPHER: KeyringCatalog = KeyringCatalog.REMOTES_ENCRYPTION_KEY

_REQUEST_TIMEOUT = 5  # seconds - timeout for short operations
_EXEC_TIMEOUT = 15 * 60  # seconds - timeout for SQL executions


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@cache
def plain_http_allowed() -> bool:
    """Whether the enterprise policy allows plain HTTP to remotes other than this machine, read once: the policy file
    doesn't change while the app runs."""
    return PlainHttpRemotesPolicy.load() is PlainHttpRemotesPolicy.ALLOWED


# ------------------------------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------------------------------
class RemoteError(Exception):
    """Common base class for all remote exceptions."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.__cause__ = cause


class RemoteExecutionError(RemoteError):
    """Raised when Pytre_Server reports a row conversion/serialization error via a NDJSON __error__ line."""


class RemoteInsecureChannelError(RemoteError):
    """Raised when trying to use an insecure channel"""


class RemoteRequestError(RemoteError):
    """Raised on a request error / httpx2.RequestError wrapper"""

    @property
    def request(self) -> httpx2.Request | None:
        if isinstance(self.__cause__, (httpx2.RequestError, RemoteRequestError)):
            return self.__cause__.request


class RemoteHTTPStatusError(RemoteError):
    """Raised on an HTTP sttus of 4xx or 5xx / httpx2.HTTPStatusError wrapper"""

    @property
    def request(self) -> httpx2.Request | None:
        if isinstance(self.__cause__, (httpx2.HTTPStatusError, RemoteHTTPStatusError)):
            return self.__cause__.request

    @property
    def response(self) -> httpx2.Response | None:
        if isinstance(self.__cause__, (httpx2.HTTPStatusError, RemoteHTTPStatusError)):
            return self.__cause__.response


class RemoteAuthError(RemoteHTTPStatusError):
    """Raised on a failed login because of invalid credentials."""


class RemoteSsoRequiredError(RemoteAuthError):
    """Raised when a remote's auth_method is AuthType.OIDC and no refresh token is available/still
    valid - the caller must trigger an interactive browser login (Pytre_Client.sso_login), Remote.login()
    has no way to do this itself. Subclasses RemoteAuthError (not RemoteError directly) so existing
    isinstance(last_error, RemoteAuthError) checks in Pytre_Client keep treating it as an auth problem."""


# ------------------------------------------------------------------------------------------
# HTTP responses
# ------------------------------------------------------------------------------------------
class JsonResponse(Protocol):
    """Minimal surface needed from a Response - only json()"""

    def json(self) -> Any: ...


class AuthenticatedResponse(JsonResponse, Protocol):
    """Minimal surface needed from a Response by get_authenticated()'s callers -
    raise_for_status() + json() (inherited from JsonResponse)."""

    def raise_for_status(self) -> Any: ...


def detail_from_response(response: JsonResponse | None, fallback: str) -> str:
    """Extract detail from response - Pytre_Server always fails with HTTPException(detail="...")"""
    if response is not None:
        try:
            detail = response.json().get("detail")
            if isinstance(detail, str) and detail:
                return detail
        except (ValueError, AttributeError):
            pass
    return fallback


# ------------------------------------------------------------------------------------------
# Remotes related classes
# ------------------------------------------------------------------------------------------
class Remotes:
    """Manages configured remotes of an user. Servers are not fetched on initialization."""

    def __init__(self, remotes_file: Path | None = None, managed_file: Path | None = None):
        self.remotes_file: Path = remotes_file or get_config_folder() / REMOTES_FILENAME
        self.managed_file: Path = managed_file or get_install_folder() / MANAGED_REMOTES_FILENAME

        self.remotes_dict: dict[str, Remote] = {}  # key is set to the uuid
        self.servers_dict: dict[str, RemoteServer] = {}

        self.get_all_remotes(reload=True)

    def get_all_remotes(self, reload: bool = False) -> dict[str, Remote]:
        # synced only over a fully loaded file: saving it would otherwise drop the remotes that couldn't be read
        if reload and self.load_remotes():
            self.sync_managed()

        return self.remotes_dict

    def get_all_servers(self, reload: bool = False) -> dict[str, RemoteServer]:
        servers: dict[str, RemoteServer] = {}
        for remote in self.remotes_dict.values():
            if reload:
                remote.refresh_servers()

            servers.update({s.id: s for s in remote.servers})

        self.servers_dict = servers

        return self.servers_dict

    def asking_for_login(self) -> list[Remote]:
        """Remotes to offer a login to: their last call was refused for lack of a session or of valid credentials,
        and the user didn't opt out. Meaningful once the remotes were called, e.g. after
        get_all_servers(reload=True)"""
        return [
            r
            for r in self.remotes_dict.values()
            if r.address.ask_for_login and isinstance(r.last_error, RemoteAuthError)
        ]

    def load_remotes(self) -> bool:
        """Load user remotes, the readable ones if some can't be. Returns whether all were loaded."""
        self.remotes_dict, all_loaded = self._load_remotes(self.remotes_file)
        return all_loaded

    def _load_managed(self) -> dict[str, Remote] | None:
        """Load managed remotes by base_id, their secrets never read and their username defaulting to the
        session's. Empty without file, None if the file or one of its remotes can't be read."""

        defaults = {"username": Users.get_current_username()}
        managed_dict, all_loaded = self._load_remotes(self.managed_file, ignored_keys=_SECRET_KEYS, defaults=defaults)
        managed_dict = {r.base_id: r for r in managed_dict.values()}  # keyed by base_id.

        if not all_loaded:
            log(f"{self.managed_file.name} ignored, deployed remotes left unchanged", logging.WARNING)
            return None

        return managed_dict

    def _load_remotes(
        self, json_file: Path, ignored_keys: tuple[str, ...] = (), defaults: dict | None = None
    ) -> tuple[dict[str, Remote], bool]:
        """Load remotes from a json file, the readable ones if some can't be: `ignored_keys` are never read and
        `defaults` fill the values a remote lacks or leaves empty. Returns whether all were loaded."""
        json_dict = {}
        remotes_dict: dict[str, Remote] = {}
        load_issues: bool = False

        # read data from json
        if json_file.exists():
            try:
                with open(json_file, mode="r", encoding="utf-8-sig") as f:
                    json_dict = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError, IOError) as e:
                log(f"Error while loading remotes json file : {e}", logging.WARNING)
                load_issues = True

            if not isinstance(json_dict, dict):
                msg = f"Content from remotes json file was expected to be a dict but got {type(json_dict).__name__}"
                log(msg, logging.ERROR)
                json_dict = {}
                load_issues = True

        # convert json data to a dictionary of remotes
        for key, val in json_dict.items():
            try:
                if not isinstance(val, dict):
                    raise TypeError(f"expected a dict, got {type(val).__name__}")

                val_kept = {k: v for k, v in val.items() if k not in ignored_keys}
                val_kept |= {k: v for k, v in (defaults or {}).items() if not val_kept.get(k)}

                remotes_dict[key] = Remote(self, RemoteAddress.from_dict({"uuid": key} | val_kept))
            except (TypeError, ValueError) as e:
                log(f"Remote json load error for {val}: {e}", logging.WARNING)
                load_issues = True

        return remotes_dict, not load_issues

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

    def sync_managed(self) -> bool:
        """Applies the remotes deployed by the administrator onto `remotes_dict`, saved at once if anything changed.
        A deployed remote updates the managed one of the same base_id, otherwise adopts the user's one with the same
        base_id, the one of the same id first, otherwise is added.
        A managed remote no longer deployed is removed. A file that can't be read changes nothing.
        Returns whether `remotes_dict` changed."""

        deployed = self._load_managed()
        if deployed is None:
            return False

        changed = False

        # managed remotes: updated from the file, removed once no longer deployed
        for remote in [r for r in self.remotes_dict.values() if r.managed]:  # copy as self.remotes_dict could change
            wanted = deployed.pop(remote.base_id, None)
            if wanted is None:
                del self.remotes_dict[remote.uuid]
                log(f"Remote no longer deployed by the administrator removed: {remote.id}", logging.INFO)
                changed = True
            else:
                changed = remote.address.sync_with(wanted.address) or changed

        # deployed remotes without a managed one: the user's remote of the same base_id is adopted, otherwise it is added
        for wanted in deployed.values():
            adopted = self.find_remote_from_id(wanted.id) or self.find_remote_from_base_id(wanted.base_id)
            if adopted is not None:
                changed = adopted.address.sync_with(wanted.address, is_managed=True) or changed
                log(f"Remote converted as deployed by the administrator: {adopted.id}", logging.INFO)
            else:
                while wanted.uuid in self.remotes_dict:  # a key of the file colliding with one of the user's remotes
                    wanted.address.uuid = str(uuid4())
                wanted.address.managed = True
                self.remotes_dict[wanted.uuid] = wanted
                log(f"Remote deployed by the administrator added: {wanted.id}", logging.INFO)
                changed = True

        # remotes deployed by the administrator, saved at once so the user's file reflects them
        if changed:
            self.save_remotes()

        return changed

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
                # a row whose cells don't match the header is malformed (missing or unquoted delimiter)
                if None in row or None in row.values():
                    msg = f"Row {row_num} ignored: its number of values doesn't match the header's"
                    log(msg, logging.WARNING)
                    continue

                try:
                    remote_address = RemoteAddress.from_dict(row)
                    remote_address.managed = False  # only the administrator's file deploys remotes
                except ValueError as err:
                    log(f"Row {row_num} ignored : incomplete data : {err}", logging.WARNING)
                    continue

                existing_remote = self.find_remote_from_id(remote_address.id)

                if existing_remote and existing_remote.address.managed:
                    msg = f"Row {row_num} ignored : {remote_address.id} is deployed by your administrator"
                    log(msg, logging.WARNING)
                    continue

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
        rows: list = [["url", "port", "username", pwd_col_name, "allow_unsecure_channel", "description"]]

        self.get_all_remotes(reload=True)
        for remote in self.remotes_dict.values():
            a = remote.address
            pwd = REMOTES_CIPHER.encrypt(a.password) if encrypt else a.password
            rows.append([a.url, a.port, a.username, pwd, a.allow_unsecure_channel, a.description])

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

    def find_remote_from_base_id(self, base_id: str) -> Remote | None:
        """Find and return a remote from it's base_id (case insensitive)"""
        return next((r for r in self.remotes_dict.values() if r.base_id.lower() == base_id.lower()), None)


class Remote:
    """Local handle for a Pytre_Server instance.
    Holds the SQL servers it exposes - does not connect to a SQL server itself."""

    def __init__(self, remotes: Remotes | None, address: RemoteAddress, load: bool = False):
        """`remotes` is None for a Remote built only to carry an address - see RemoteServer.deserialize(),
        the only place this happens. save() / delete() raise RuntimeError in that case."""

        self.remotes: Remotes | None = remotes
        self.address: RemoteAddress = address
        self.servers: list[RemoteServer] = []
        self.user_infos: dict | None = None
        self.last_error: RemoteError | None = None

        if load:
            self.refresh_servers()

    @property
    def uuid(self) -> str:
        """Mirror the RemoteAddress which define this remote instance"""
        return self.address.uuid

    @property
    def base_id(self) -> str:
        """Mirror the RemoteAddress which define this remote instance: url:port"""
        return self.address.base_id

    @property
    def id(self) -> str:
        """Mirror the RemoteAddress which define this remote instance: username@url:port"""
        return self.address.id

    @property
    def managed(self) -> bool:
        """Mirror the RemoteAddress which define this remote instance"""
        return self.address.managed

    @property
    def auth_headers(self) -> dict[str, str]:
        if not self.address.access_token:
            return {}
        return {"Authorization": f"Bearer {self.address.access_token}"}

    def check_health(self) -> bool:
        """Checks if remote is reachable. Returns True on success, raises on failure."""
        try:
            response = httpx2.get(url=self.address.build_url("/health"), timeout=_REQUEST_TIMEOUT)
            response.raise_for_status()
        except httpx2.HTTPStatusError as e:
            msg = f"Couldn't reach {self.id} : {detail_from_response(e.response, str(e))}"
            log(msg, logging.WARNING)
            self.last_error = RemoteHTTPStatusError(msg, cause=e)
            raise self.last_error
        except httpx2.RequestError as e:
            msg = f"Remote unreachable : {self.id} : {e}"
            log(msg, logging.WARNING)
            self.last_error = RemoteRequestError(msg, cause=e)
            raise self.last_error

        self.last_error = None
        return True

    def check_channel(self) -> None:
        """Raises RemoteInsecureChannelError if credentials and tokens may not be sent to this remote, see
        RemoteAddress.channel_is_allowed()."""

        allowed = plain_http_allowed()
        if self.address.channel_is_allowed(allowed):
            return

        if not allowed:
            msg = f"Plain HTTP to {self.id} is disabled by your organization: its address must start with https://"
        else:
            msg = f"Login to a non-local remote is not allowed over plain HTTP: {self.id}"

        log(msg, logging.ERROR)

        self.last_error = RemoteInsecureChannelError(msg)
        raise self.last_error

    def login(self) -> bool:
        """Authenticates and store token on `address`. Returns True on success, raises on failure.
        Refuses to send credentials or tokens over a channel check_channel() doesn't allow."""

        self.check_channel()

        # try to get an access token only if refresh_token still valid
        if self.address.refresh_token_is_valid:
            try:
                url_route = self.address.build_url("/refresh")
                body = {"refresh_token": self.address.refresh_token}
                response = httpx2.post(url=url_route, json=body, timeout=_REQUEST_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                self.address.access_token = data["access_token"]
                # an OIDC account whose was just reconfirmed active gets a new longer-lived refresh token
                if (refresh_token := data.get("refresh_token")) is not None:
                    self.address.refresh_token = refresh_token
                    if self.remotes is not None:
                        self.save(reload=False)
                return True
            except (httpx2.RequestError, httpx2.HTTPStatusError) as e:
                log(f"Failed to retrieve an acces token for {self.id} : {e}", logging.DEBUG)
                self.address.access_token = ""

        # control: with an SSO remote an expired session requires an interactive browser to login
        if self.address.auth_method is AuthType.OIDC:
            msg = f"SSO session expired for {self.id}, interactive login required"
            log(msg, logging.INFO)
            self.last_error = RemoteSsoRequiredError(msg)
            raise self.last_error

        # control: without password login is not possible
        if not self.address.password:
            msg = f"No password saved for {self.id}, login required"
            log(msg, logging.INFO)
            self.last_error = RemoteAuthError(msg)
            raise self.last_error

        # if retrieving access token failed then renew refresh token
        try:
            url_route = self.address.build_url("/login")
            body = {"username": self.address.username, "password": self.address.password}
            response = httpx2.post(url=url_route, json=body, timeout=_REQUEST_TIMEOUT)
            response.raise_for_status()
            self.address.refresh_token = response.json()["refresh_token"]
            self.address.access_token = response.json()["access_token"]
            if self.remotes is not None:
                self.save(reload=False)
        except httpx2.HTTPStatusError as e:
            detail = detail_from_response(e.response, str(e))
            err_msg = f"Login failed for {self.id} - check username/password : {detail}"
            self.address.refresh_token, self.address.access_token = "", ""
            log(err_msg, logging.WARNING)
            self.last_error = RemoteAuthError(err_msg, cause=e)
            raise self.last_error
        except httpx2.RequestError as e:
            err_msg = f"Login failed for {self.id} : {e}"
            self.address.refresh_token, self.address.access_token = "", ""
            log(err_msg, logging.WARNING)
            self.last_error = RemoteRequestError(err_msg, cause=e)
            raise self.last_error

        self.last_error = None
        return True

    def get_authenticated(self, url: str, params: dict | None = None) -> AuthenticatedResponse:
        """GETs `url` using a valid Bearer token - logs in and retries once if the token is
        missing, expired, or invalidated (any of which the server reports as 401).
        Can raise RemoteInsecureChannelError, RemoteAuthError or RemoteRequestError or
        a non-401 error status returned as-is."""

        response = None

        if self.address.access_token_is_valid:
            response = httpx2.get(url=url, params=params, headers=self.auth_headers, timeout=_REQUEST_TIMEOUT)

        if response is None or response.status_code == 401:
            self.login()
            response = httpx2.get(url=url, params=params, headers=self.auth_headers, timeout=_REQUEST_TIMEOUT)

        return response

    def refresh_servers(self) -> list[RemoteServer]:
        """Fetches exposed SQL servers and user's own infos.
        On failure, logs a warning, sets last_error and keeps the previous list."""
        try:
            url_route = self.address.build_url(route="/servers")
            response = self.get_authenticated(url=url_route)
            response.raise_for_status()
            data = response.json()
        except RemoteError as e:
            log(f"Failed to retrieve remote servers for {self.id} - see cause above", logging.DEBUG)
            self.last_error = e
            return self.servers
        except httpx2.RequestError as e:
            msg = f"Remote unreachable, retrieving servers from {self.id} failed : {e}"
            log(msg, logging.WARNING)
            self.last_error = RemoteRequestError(msg, cause=e)
            return self.servers
        except httpx2.HTTPStatusError as e:
            detail = detail_from_response(e.response, str(e))
            msg = f"Couldn't retrieve remote servers from {self.id} : {detail}"
            log(msg, logging.WARNING)
            self.last_error = RemoteHTTPStatusError(msg, cause=e)
            return self.servers

        self.last_error = None
        self.servers = [RemoteServer(**s, remote=self) for s in data.get("servers", [])]
        self.user_infos = data.get("user_infos")

        return self.servers

    def save(self, reload: bool = True) -> bool:
        """Save remote to preferences. If a `previous_id` exists then it will allow overwriting.
        Set `reload` to False to skips re-reading `remotes_dict` from disk - safe only when
        url/port/username aren't changing (e.g. to persist a new refresh token)."""
        if self.remotes is None:
            err_msg = f"Can't save remote without a remotes collection set"
            log(err_msg, logging.ERROR)
            raise RuntimeError(err_msg)

        self.remotes.get_all_remotes(reload=reload)

        # controls
        if any((r for uuid, r in self.remotes.remotes_dict.items() if r.id == self.id and uuid != self.uuid)):
            raise LookupError("Remote already exists")

        try:
            RemoteAddress.from_dict(self.address.to_dict())  # check if address can be created from current values
        except ValueError:
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

        if self.address.managed:
            raise ValueError(f"{self.id} is deployed by your administrator and can't be deleted")

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
        auth_method: AuthType = AuthType.BASIC,
        password: str = "",
        uuid: str = "",
        description: str = "",
        refresh_token: str = "",
        allow_unsecure_channel: bool = False,
        managed: bool = False,
        ask_for_login: bool = True,
    ):
        self.uuid: str = uuid if not uuid == "" else str(uuid4())
        self.url: str = url
        self.port: int = port
        self.username: str = username
        self.auth_method: AuthType = auth_method
        self.password: str = password
        self.description: str = description
        self.refresh_token: str = refresh_token
        self.access_token: str = ""  # in memory only, to_dict / from_dict don't use it
        self.allow_unsecure_channel = allow_unsecure_channel
        self.managed: bool = managed  # deployed by the administrator (MANAGED_REMOTES_FILE), kept in sync with it
        self.ask_for_login: bool = ask_for_login  # whether the client asks to log in when no session is available

    @property
    def base_id(self) -> str:
        """Base identity of a remote as url:port"""
        for val in (self.url, self.port):
            if str(val).strip() == "":
                raise ValueError("All values to build id must be set")

        return f"{self.url.strip().rstrip('/').lower()}:{self.port}"

    @property
    def id(self) -> str:
        """Return as username@url:port"""
        if self.username.strip() == "":
            raise ValueError("All values to build id must be set")

        return f"{self.username.strip()}@{self.base_id}".lower()

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

    def channel_is_allowed(self, allow_plain_http: bool) -> bool:
        """True if credentials and tokens may be sent to this remote: always over HTTPS, and over plain HTTP to the
        machine itself (localhost, 127.0.0.0/8, ::1). Over plain HTTP elsewhere only if `allow_plain_http`, and then
        to a private network address (RFC 1918...) or when `allow_unsecure_channel` is set."""
        parsed_url = urlparse(self.url)

        if parsed_url.scheme == "https":
            return True

        host = parsed_url.hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None  # a host name

        if host == "localhost" or (address is not None and address.is_loopback):
            return True
        if not allow_plain_http:
            return False

        return (address is not None and address.is_private) or self.allow_unsecure_channel

    def sync_with(self, wanted: RemoteAddress, is_managed: bool | None = None) -> bool:
        """Sync infos with `wanted`. Force `managed` if `is_managed` not None. Returns if there was any change."""
        changed = False

        if is_managed is not None and self.managed != is_managed:
            self.managed = is_managed
            changed = True

        if (self.url, self.port, self.auth_method) != (wanted.url, wanted.port, wanted.auth_method):
            self.refresh_token = ""
            self.access_token = ""

        for field in MANAGED_FIELDS:
            if getattr(self, field) != getattr(wanted, field):
                setattr(self, field, getattr(wanted, field))
                changed = True

        return changed

    def _token_is_valid(self, token: str, min_remaining: timedelta = timedelta(seconds=5)) -> bool:
        """Return whether the token exists, well-formed, and won't expire within `min_remaining`."""
        try:
            decoded = jwt.decode(token, options={"verify_signature": False, "verify_exp": True})
        except jwt.InvalidTokenError:
            return False

        exp = datetime.fromtimestamp(decoded.get("exp", 0), tz=timezone.utc)

        return datetime.now(timezone.utc) < exp - min_remaining

    def align_username(self) -> None:
        """If access_token contains a sub (SSO mode) then use it to align username."""
        try:
            claims = jwt.decode(self.access_token, options={"verify_signature": False})
        except jwt.InvalidTokenError:
            return

        if (sub := claims.get("sub")) and sub != self.username:
            self.username = sub

    def build_url(self, route: str = "") -> str:
        """Builds the URL to access a Remote. Returns the base URL if `route` is empty."""
        return f"{self.url}:{self.port}{route}"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "uuid": self.uuid,
            "url": self.url,
            "port": self.port,
            "username": self.username,
            "auth_method": self.auth_method.value,
            "encrypted_pwd": REMOTES_CIPHER.encrypt(self.password),
            "description": self.description,
            "allow_unsecure_channel": self.allow_unsecure_channel,
            "managed": self.managed,
            "ask_for_login": self.ask_for_login,
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
            "auth_method": None,
            "password": pwd_key,
            "description": None,
            "allow_unsecure_channel": None,
            "managed": None,
            "ask_for_login": None,
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
            # special conversion for optional attributes
            elif key == "auth_method":
                auth_type = AuthType.from_value(str(val)) if val else None
                val = auth_type if auth_type is not None and auth_type is not AuthType.NONE else AuthType.BASIC
            elif key in ("allow_unsecure_channel", "managed"):
                val = str(val).lower() == "true" if val else False
            elif key == "ask_for_login":
                val = str(val).lower() != "false" if val not in (None, "") else True  # absent from a former file
            # convert None val for other optional attributes
            else:
                val = val if val else ""

            if key == "uuid" and not val:
                val = str(uuid4())  # generate an uuid if missing
            elif key in ("url", "username") and val == "":
                msg = f"{key} can't be empty to create a RemoteAddress"
                log(msg, logging.ERROR)
                raise ValueError(msg)
            elif key == "port":
                val = str(val)
                if val.isdigit() and val != "0":
                    val = int(val)
                else:
                    msg_not_an_int = f"port must be an integer to create a RemoteAddress, got {val}"
                    msg_port_0 = f"port can't be 0 to create a RemoteAddress"
                    msg = msg_port_0 if val == "0" else msg_not_an_int
                    log(msg, logging.ERROR)
                    raise ValueError(msg)
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
        self.last_error: RemoteError | None = None

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
    def execute(
        self, query_id: str, template_hash: str, display_format: DisplayFormat, display_parameters: dict[str, str]
    ) -> Generator[RemoteCursor]:
        """Executes query_id via POST /execute, streaming NDJSON rows.
        `display_format` is the one `parameters` were rendered with.
        The underlying HTTP connection closes as soon as the `with` block exits."""

        # check if server is available to avoid long timeout if it is not
        self.remote.check_health()

        # proceed to query execution as the server has been confirmed to be available
        url = self.address.build_url("/execute")
        body = {
            "server_id": self.id_on_remote,
            "query_id": query_id,
            "template_hash": template_hash,
            "display_format": display_format.to_dict(),
            "display_parameters": display_parameters,
        }

        max_login_retry = 1
        try:
            for i in range(max_login_retry + 1):
                with httpx2.stream(
                    "POST", url, json=body, headers=self._auth_headers, timeout=_EXEC_TIMEOUT
                ) as response:
                    if response.status_code == 401 and not i == max_login_retry:
                        self.remote.login()
                        continue

                    try:
                        response.raise_for_status()
                    except httpx2.HTTPStatusError as e:
                        response.read()  # buffer the body as the connection closes as soon as `with` ends,
                        raise RemoteHTTPStatusError(
                            detail_from_response(e.response, str(e)), cause=e
                        )  # after that e.response.json()/.text would raise ResponseNotRead

                    lines = response.iter_lines()
                    first_line = next(lines, None)
                    while first_line is not None and StreamKey.PING in json.loads(first_line):
                        first_line = next(lines, None)  # keep alive sent while the query is still running

                    if first_line is None:
                        col_names: tuple = ()
                    else:
                        first_record = json.loads(first_line)
                        self._raise_if_error(first_record)
                        col_names = first_record[StreamKey.COLUMNS]

                    yield RemoteCursor(tuple(col_names), self._iter_records(lines))
                    break
        except httpx2.TimeoutException as e:
            msg = f"Nothing received from {self.remote.id} for {_EXEC_TIMEOUT // 60} minutes, execution abandoned"
            log(msg, logging.WARNING)
            raise RemoteRequestError(msg, cause=e) from e
        except httpx2.RequestError as e:
            msg = f"Connection to {self.remote.id} lost during the execution : {e}"
            log(msg, logging.WARNING)
            raise RemoteRequestError(msg, cause=e) from e

    def _iter_records(self, lines: Iterator[str]) -> Generator[tuple]:
        for line in lines:
            if not line:
                continue

            record = json.loads(line)
            if StreamKey.PING in record:
                continue
            self._raise_if_error(record)

            yield tuple(decode_row_value(v) for v in record[StreamKey.ROW])

    def _raise_if_error(self, record: dict) -> None:
        if StreamKey.ERROR in record:
            error = record[StreamKey.ERROR]
            raise RemoteExecutionError(f"{error['type']}: {error['message']}")

    def get_serialized_queries(self, display_format: DisplayFormat) -> list[dict]:
        """Fetches queries exposed by this remote server. Called on demand and not eagerly.
        On failure, logs a warning, sets last_error and keeps the previous list rather than clearing it."""
        id_for_error_msg = f"{self.address.build_url()} (server_id={self.id_on_remote})"

        try:
            url_route = self.address.build_url(route="/queries")
            params = {"server_id": self.id_on_remote, "display_format": json.dumps(display_format.to_dict())}
            response = self.remote.get_authenticated(url=url_route, params=params)
            response.raise_for_status()
            serialized_queries = response.json()
        except RemoteError as e:
            log(f"Failed to retrieve serialized queries for {id_for_error_msg} - see cause above", logging.DEBUG)
            self.last_error = e
            return self.serialized_queries
        except httpx2.RequestError as e:
            msg = f"Remote unreachable, retrieving queries from {id_for_error_msg} failed : {e}"
            log(msg, logging.WARNING)
            self.last_error = RemoteRequestError(msg, cause=e)
            return self.serialized_queries
        except httpx2.HTTPStatusError as e:
            detail = detail_from_response(e.response, str(e))
            msg = f"Couldn't retrieve queries from {id_for_error_msg} : {detail}"
            log(msg, logging.WARNING)
            self.last_error = RemoteHTTPStatusError(msg, cause=e)
            return self.serialized_queries

        self.last_error = None
        self.serialized_queries = serialized_queries
        return self.serialized_queries

    def serialize(self) -> dict:
        """Only carries what execute() needs standalone: id_on_remote, description, address
        (RemoteAddress.to_dict() excludes the in-memory-only token), and user_infos. Everything else
        - serialized_queries/last_error, and the rest of `remote` (its Remotes collection, sibling
        servers) - is dropped, not needed for the execution round-trip - see Query.serialize()."""

        return {
            "id_on_remote": self.id_on_remote,
            "description": self.description,
            "address": self.address.to_dict(),
            "user_infos": self.remote.user_infos,
        }

    @classmethod
    def deserialize(cls, data: dict) -> Self:
        """Rebuilds id_on_remote, description, address, user_infos only - serialized_queries/last_error
        stay at their defaults, and the reconstructed Remote has no Remotes collection (remotes=None),
        since this is only used for the execution round-trip - see Query.serialize()."""

        address = RemoteAddress.from_dict(data["address"])
        remote = Remote(None, address)
        remote.user_infos = data.get("user_infos")
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
