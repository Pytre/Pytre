from __future__ import annotations

import csv
import io
import getpass
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from pykeepass.entry import Entry
from pykeepass.group import Group

from Pytre_Core.kee import Kee
from Pytre_Core.utils import detect_encoding, log as _log, setup_standalone_logging


_HASHER = PasswordHasher()
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(32))


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass
class OidcLink:
    issuer: str
    sub: str
    refresh_token: str = ""
    last_verified: datetime | None = None

    def to_dict(self, for_export: bool = False) -> dict[str, str]:
        """Only include issuer and sub if `for_export` is True"""
        if for_export:
            return {"issuer": self.issuer, "sub": self.sub}

        return {
            "issuer": self.issuer,
            "sub": self.sub,
            "refresh_token": self.refresh_token,
            "last_verified": self.last_verified.isoformat() if self.last_verified else "",
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> OidcLink:
        return OidcLink(
            issuer=data.get("issuer", ""),
            sub=data.get("sub", ""),
            refresh_token=data.get("refresh_token", ""),
            last_verified=datetime.fromisoformat(d) if (d := data.get("last_verified")) else None,
        )


class _CsvStatus(Enum):
    REQUIRED = "required"
    PASSWORD = "password"
    OPTIONAL = "optional"
    EXCLUDED = "excluded"


class _DbKind(Enum):
    NATIVE = "native"  # entry property / attribute
    CUSTOM = "custom"  # entry custom property
    TAGS = "tags"  # entry tags


class UserField(Enum):
    """Registry of User's predefined KeePass storage fields - single source of truth for where each one
    lives (db_kind/db_key), and for CSV whether they are included and CSV col name (csv_status/csv_header).
    Dynamic custom fields (attribs_cust) aren't part of this registry - they have no fixed
    identity."""

    ID = (_CsvStatus.REQUIRED, "id", _DbKind.NATIVE, "username")
    DESCRIPTION = (_CsvStatus.REQUIRED, "description", _DbKind.NATIVE, "title")
    ADMIN = (_CsvStatus.REQUIRED, "admin", _DbKind.CUSTOM, "superuser")
    GROUPS = (_CsvStatus.REQUIRED, "groups", _DbKind.TAGS, "")
    LOGIN_MSG = (_CsvStatus.REQUIRED, "login message", _DbKind.CUSTOM, "msg_login")

    AUTH_TYPE = (_CsvStatus.OPTIONAL, "auth", _DbKind.CUSTOM, "auth_type")
    OIDC_LINK = (_CsvStatus.OPTIONAL, "oidc link", _DbKind.CUSTOM, "oidc_link")

    PWD = (_CsvStatus.PASSWORD, "password", _DbKind.NATIVE, "password")
    PWD_HASH = (_CsvStatus.PASSWORD, "password hash", _DbKind.NATIVE, "password")
    PWD_CHANGED_AT = (_CsvStatus.EXCLUDED, "", _DbKind.CUSTOM, "password_changed_at")  # set via password_hash setter

    def __init__(self, csv_status: _CsvStatus, csv_header: str, db_kind: _DbKind, db_key: str):
        self.csv_status: _CsvStatus = csv_status
        self.csv_header: str = csv_header
        self.db_kind: _DbKind = db_kind
        self.db_key: str = db_key

    @property
    def label(self) -> str:
        """Human-readable header label for csv_export() - "login message" -> "Login Message"."""
        return self.csv_header.title()

    @classmethod
    def custom_keys(cls) -> frozenset[str]:
        """Storage keys of the CUSTOM-kind columns - used to tell predefined custom
        properties apart from genuinely dynamic ones (attribs_cust)."""
        return frozenset(c.db_key for c in cls if c.db_kind is _DbKind.CUSTOM and c.db_key)

    @classmethod
    def reserved_keys(cls) -> frozenset[str]:
        """All predefined storage keys (native and custom) - used to guard a dynamic custom
        attribute name against colliding with an internal field."""
        return frozenset(f.db_key for f in cls if f.db_key)


class Users:
    def __init__(self, kee: Kee):
        self.kee: Kee = kee
        self.kee_grp: Group | None = None
        self.kee_servers_grp: Group | None = None

        self.groups: set[str] = set()
        self.attribs_cust: list[str] = list()  # attributes not predefined

        self.get_all_groups()

    def open_db(self, reload: bool = False) -> bool:
        self.kee.open_db(reload)
        if self.kee.is_ko:
            return False
        self.kee_grp = self.kee.grp_users
        self.kee_servers_grp = self.kee.grp_servers
        return True

    def users_admin_exists(self, reload: bool = False) -> bool:
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot detect if admin exists")

        self.open_db(reload)

        admin_exists = False

        u_entry: Entry | None = None
        for u_entry in self.kee_grp.entries:
            if u_entry.get_custom_property(UserField.ADMIN.db_key) == "true":
                admin_exists = True
                break

        return admin_exists

    def get_all_users(self, reload: bool = True) -> list[User]:
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot get all users")

        self.open_db(reload)

        u_list: list[User] = []
        attribs_set: set[str] = set()
        for entry in self.kee_grp.entries:
            u_list.append(User(self, entry=entry))
            attribs_set.update(self._get_cust_attribs_list(entry))

        self.attribs_cust = sorted(attribs_set)

        return u_list

    def get_all_groups(self, reload: bool = False) -> set:
        self.open_db(reload)

        if self.kee_grp is None or self.kee_servers_grp is None:
            return set()

        self.groups = set()
        for entry in self.kee_grp.entries + self.kee_servers_grp.entries:
            tags = set(map(lambda val: val.lower().strip(), entry.tags)) if entry.tags else []
            self.groups.update(tags) if tags else None

        return self.groups

    def get_cust_attribs_list(self, reload: bool = False) -> list[str]:
        """Updates and return attribs_cust with not predefined attributes"""

        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot get custom attribs list")

        self.open_db(reload)

        attribs_set: set[str] = set()

        # retrieve set of custom entry from all entries
        for u_entry in self.kee_grp.entries:
            attribs_set.update(self._get_cust_attribs_list(u_entry))

        # create sorted list from set to update custom attributes
        self.attribs_cust = sorted(attribs_set)

        return self.attribs_cust

    def _get_cust_attribs_list(self, entry: Entry) -> set[str]:
        """return not predefined attributes"""

        attribs_set: set[str] = set()
        predefined_custom_keys = UserField.custom_keys()

        for property in entry.custom_properties:
            if property not in predefined_custom_keys:
                attribs_set.add(property)

        return attribs_set

    def users_add_groups(self, user_uuids: list[str] | tuple[str, ...], groups: list[str]):
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot add users groups")

        self.open_db()

        save: bool = False

        groups_to_add = [group for group in groups if not group == "all"]
        for uuid in user_uuids:
            user = self.find_user_by_uuid(uuid)
            if user is None:
                continue

            new_groups = list(set(user.groups + groups_to_add))
            if not set(new_groups) == set(user.groups):
                user.groups = new_groups
                user.save(save_db=False)  # update db without committing changes
                save = True

        if save:
            self.kee.save_db()  # commit db changes to disk

    def users_remove_groups(self, user_uuids: list[str] | tuple[str, ...], groups: list[str]):
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot remove users groups")

        self.open_db()

        save: bool = False

        for uuid in user_uuids:
            user = self.find_user_by_uuid(uuid)
            if user is None:
                continue

            new_groups: list[str] = [g for g in user.groups if g not in groups]
            if not set(new_groups) == set(user.groups):
                user.groups = new_groups
                user.save(save_db=False)  # update db without committing changes
                save = True

        if save:
            self.kee.save_db()  # commit db changes to disk

    def modify_custom_attribs(self, fields: list[str], new_value: str | None = None):
        """
        Modify custom fields for all users
        Args:
            fields (list[str]): List of custom field names to modify or create.
            new_value (str, optional): None set to an empty string if the field doesn't exist.
        """

        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot modify custom attribs")

        self.open_db()

        save: bool = False

        u_entry: Entry | None = None
        for u_entry in self.kee_grp.entries:
            for field in fields:
                if field not in u_entry.custom_properties:
                    u_entry.set_custom_property(field, new_value or "")
                    save = True
                elif new_value is not None and u_entry.get_custom_property(field) != new_value:
                    u_entry.set_custom_property(field, new_value)
                    save = True

        if save:
            self.kee.save_db()

        self.attribs_cust = list(set(self.attribs_cust + fields))

    def remove_custom_attribs(self, fields: list[str]):
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot remove custom attribs")

        self.open_db()

        save: bool = False
        u_entry: Entry | None = None
        for u_entry in self.kee_grp.entries:
            for field in fields:
                if field in u_entry.custom_properties:
                    u_entry.delete_custom_property(field)
                    save = True

        if save:
            self.kee.save_db()

        self.attribs_cust = [attr for attr in self.attribs_cust if attr not in fields]

    def csv_import(self, filepath: Path | str, delimiter: str = ";", quotechar: str = '"') -> bool:
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot import users")

        if not (delimiter) or len(delimiter) != 1 or delimiter == quotechar:
            raise ValueError(f"Delimiter must be a single character and different from quote character ({quotechar})")
        if not (quotechar) or len(quotechar) != 1:
            raise ValueError("Quote character must be a single character")
        if not Path(filepath).exists():
            raise FileNotFoundError(f"File to import users does not exist : {filepath}")

        self.open_db(True)
        if self.kee.db is None:
            raise RuntimeError("Database not opened, can't import csv")

        # retrieve existing users
        entries_dict = {}
        u_entry: Entry
        for u_entry in self.kee_grp.entries:
            entries_dict[(u_entry.username or "").upper()] = u_entry

        # define expected columns
        cols_std = [c.csv_header for c in UserField if c.csv_status is _CsvStatus.REQUIRED]
        cols_pwd = [c for c in UserField if c.csv_status is _CsvStatus.PASSWORD]
        cols_cust = {col: col.lower() for col in self.get_cust_attribs_list()}
        cols_required = cols_std + list(cols_cust.values())

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

            # control that there is not both a password and a password_hash
            cols_pwd_in_csv = [c for c in cols_pwd if c.csv_header in csv_reader.fieldnames]
            creds_in_csv = [c for c in cols_pwd_in_csv if c.db_kind is _DbKind.NATIVE]
            if len(creds_in_csv) > 1:
                raise ValueError("Import file can't have both a password and a password_hash column")

            # process rows
            for row_num, row in enumerate(csv_reader, start=2):
                username = row[UserField.ID.csv_header].strip()
                if not username:
                    log(f"Ligne {row_num} ignorée : aucun id renseigné", logging.WARNING)
                    continue

                # create or retrieve db entry
                if username.upper() in entries_dict.keys():
                    u_entry = entries_dict[username.upper()]
                    u_entry.username = username
                else:
                    u_entry = self.kee.db.add_entry(self.kee_grp, "", username, "")

                # update standard infos
                u_entry.title = row.get(UserField.DESCRIPTION.csv_header, "").strip()

                if row[UserField.ADMIN.csv_header].strip() == "1":
                    u_entry.set_custom_property(UserField.ADMIN.db_key, "true")
                else:
                    u_entry.set_custom_property(UserField.ADMIN.db_key, "false")

                login_msg_field = UserField.LOGIN_MSG
                u_entry.set_custom_property(login_msg_field.db_key, row[login_msg_field.csv_header].strip())

                # update optional infos
                auth_field = UserField.AUTH_TYPE
                if auth_field.csv_header in row:
                    value = row[auth_field.csv_header].strip()
                    auth_type = AuthType.from_value(value)
                    if auth_type is None:
                        log(f"Row {row_num} ignored : '{value}' is not a valid auth type", logging.WARNING)
                        continue
                    u_entry.set_custom_property(auth_field.db_key, auth_type.value)

                oidc_field = UserField.OIDC_LINK
                if oidc_field.csv_header in row:
                    saved_link = load_oidc_link(u_entry.get_custom_property(oidc_field.db_key) or "")
                    loaded_link = load_oidc_link(row[oidc_field.csv_header].strip() or "")

                    new_link: dict | str = ""
                    if not loaded_link:
                        new_link = ""
                    elif not saved_link or loaded_link.issuer != saved_link.issuer:
                        new_link = OidcLink(loaded_link.issuer, loaded_link.sub).to_dict()
                    elif loaded_link.sub != "" and loaded_link.sub != saved_link.sub:
                        new_link = OidcLink(loaded_link.issuer, loaded_link.sub).to_dict()

                    if new_link:
                        u_entry.set_custom_property(oidc_field.db_key, json.dumps(new_link), protect=True)

                # update password related infos
                for col in cols_pwd_in_csv:
                    value = row[col.csv_header].strip()
                    if not value == "":  # avoid overwriting password for an existing user when no value
                        u_entry.password = value if col is UserField.PWD_HASH else hash_password(value)

                # update groups using csv.reader to parse imported string
                grp_authorized = row[UserField.GROUPS.csv_header].strip()
                if grp_authorized:
                    grp_reader = csv.reader([grp_authorized], delimiter=delimiter, quotechar=quotechar)
                    u_entry.tags = [grp.strip() for grp in next(grp_reader) if grp.strip().lower() not in ["all", ""]]
                    # replace forbidden keepass caracters (colon and semi colon)
                    u_entry.tags = [grp.replace(",", "_").replace(";", "_") for grp in u_entry.tags]
                else:
                    u_entry.tags = []

                # update custom infos
                for prop, csv_header in cols_cust.items():
                    if csv_header in row:
                        u_entry.set_custom_property(prop, row[csv_header].strip())

            # commit changes
            self.kee.save_db()

            return True

    def csv_export(
        self, filepath: Path | str, delimiter: str = ";", overwrite: bool = False, pwd_infos: bool = False
    ) -> bool:
        quotechar = '"'

        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot export users")

        if not delimiter or len(delimiter) != 1 or delimiter == quotechar:
            raise ValueError(f"Delimiter must be a single character and different from quote character ({quotechar})")
        if Path(filepath).exists() and not overwrite:
            raise FileExistsError(f"File to export users already exists and overwrite is set to False : {filepath}")

        # Determine which attr to saves
        attr_std = [c for c in UserField if c.csv_status is _CsvStatus.REQUIRED]
        attr_optional = [c for c in UserField if c.csv_status is _CsvStatus.OPTIONAL]
        attr_cust = self.get_cust_attribs_list()

        if pwd_infos:
            attr_optional[1:1] = [UserField.PWD_HASH]

        attr_to_export = attr_std + attr_optional + attr_cust

        # Create and add header row to rows
        header = [attr.label if isinstance(attr, UserField) else attr for attr in attr_to_export]
        rows = [header]

        # Start to construct rows list which will be saved to file
        self.open_db(True)
        u_entry: Entry | None
        for u_entry in self.kee_grp.entries:
            row = []

            # Convert groups list to string using csv.writer (avoid problem for caracters similar to delimiter)
            tags = u_entry.tags if u_entry.tags else []
            if tags:
                output = io.StringIO()
                grp_writer = csv.writer(output, delimiter=delimiter, quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)
                grp_writer.writerow(tags)
                return_size = len(grp_writer.dialect.lineterminator)
                group = output.getvalue()[:-return_size]  # suppression du retour à la ligne
            else:
                group = ""

            # Retrieve values and append them to current row
            for attr in attr_to_export:
                if attr is UserField.GROUPS:
                    value = group
                elif attr is UserField.ADMIN:
                    value = u_entry.get_custom_property(attr.db_key) or ""
                    value = 1 if value.lower() == "true" else 0
                elif isinstance(attr, UserField) and attr.db_kind is _DbKind.NATIVE:
                    assert attr.db_key is not None
                    value = getattr(u_entry, attr.db_key) or ""
                elif attr is UserField.OIDC_LINK:
                    link = load_oidc_link(u_entry.get_custom_property(attr.db_key) or "")
                    value = json.dumps(link.to_dict(for_export=True)) if link else ""
                elif isinstance(attr, UserField):  # db_kind == "custom"
                    value = u_entry.get_custom_property(attr.db_key)
                else:
                    value = u_entry.get_custom_property(attr)  # dynamic custom attribute (plain string)

                value = value if value is not None else ""

                row.append(value)

            rows.append(row)

        with open(filepath, mode="w", encoding="utf-8-sig", newline="") as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=delimiter, quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerows(rows)

        return True

    def get_current_username(self) -> str:
        domain = os.environ.get("userdnsdomain") or ""
        name = getpass.getuser()
        domain_and_name = f"{domain}\\{name}" if domain else name
        return domain_and_name

    def find_entry_by_username(self, username: str) -> Entry | None:
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot find user entry by username")

        for entry in self.kee_grp.entries:
            if entry.username and username.upper() == entry.username.upper():
                return entry

        return None

    def find_user_by_uuid(self, uuid: str) -> User | None:
        if self.kee.db is None:
            raise RuntimeError("Database not opened, can't find user by uuid")
        entry = self.kee.db.find_entries(uuid=UUID(uuid), first=True)
        return User(self, entry=entry) if entry and isinstance(entry, Entry) else None

    def find_user_by_oidc_link(self, issuer: str, sub: str) -> User | None:
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot find user from oidc link")

        db_key = UserField.OIDC_LINK.db_key
        for entry in self.kee_grp.entries:
            link = load_oidc_link(entry.get_custom_property(db_key) or "")
            if link is not None and link.issuer == issuer and link.sub == sub:
                return User(self, entry=entry)

        return None


class AuthType(Enum):
    """Authentication types to identify how to interpret User.password_hash."""

    NONE = "none"
    BASIC = "basic"
    OIDC = "oidc"

    def __str__(self):
        return "OIDC" if self is AuthType.OIDC else self.value.title()

    @classmethod
    def from_value(cls, value: str) -> AuthType | None:
        """Return AuthType from value - case insensitive. On invalid value return None"""
        return next((auth for auth in AuthType if value.lower() == auth.value.lower()), None)


class User:
    def __init__(self, users: Users, username: str = "", entry: Entry | None = None, detect_user: bool = True):
        """If no `username`, no `entry` and if `detect_user` is False then no loading from keepass db occurs"""

        self.users: Users = users

        self.uuid: str = ""
        self.username: str = ""
        self._pwd_hash: str = ""
        self._pwd_changed_at: datetime | None = None
        self.auth_type: AuthType = AuthType.NONE
        self.oidc_link: OidcLink | None = None

        self.exists: bool = False
        self.is_authorized: bool = False

        self.title: str = ""
        self.admin: bool = False
        self.groups: list[str] = ["all"]  # by default user belongs to "all" group
        self.msg_login: str = ""
        self.attribs_cust: dict[str, str] = {}  # dict of attributes not predefined

        if entry:
            self._load_from_entry(entry)
        elif username or detect_user:
            self.username = username if username else self.users.get_current_username()
            self.load()

    @property
    def password_hash(self) -> str:
        """On change `password_changed_at` is also automatically changed so it stays in sync
        (used server-side to invalidate JWTs issued before this change)."""
        return self._pwd_hash

    @password_hash.setter
    def password_hash(self, password_hash: str):
        """Sets a new password hash and records when it changed so `password_changed_at`
        can't get out of sync (used server-side to invalidate JWTs issued before this change).
        A no-op reassignment (same value) leaves `password_changed_at` untouched."""
        if password_hash == self._pwd_hash:
            return

        self._pwd_hash = password_hash

        changed_at = datetime.now(timezone.utc)
        self._pwd_changed_at = changed_at.replace(microsecond=0)  # match JWT "iat" encoding as integer epoch second

    @property
    def password_changed_at(self) -> datetime | None:
        """Can't be changed  - it is automatically set when changing `password_hash` so it stays in sync
        (used server-side to invalidate JWTs issued before this change)."""
        return self._pwd_changed_at

    def user_info(self, attr: str) -> str:
        """Returns an attribute value exposed to a SQL query's USER_INFO(...)"""

        if attr in ("username", "title", "admin", "msg_login"):  # allowed predefined info
            return getattr(self, attr, "")

        return self.attribs_cust.get(attr, "")  # or custom info

    def to_dict(self) -> dict:
        attribs = {
            "uuid": self.uuid,
            "id": self.username,
            "auth_type": self.auth_type.name,
            "password_changed_at": self.password_changed_at or "",
            "exist_in_settings": self.exists,
            "is_authorized": self.is_authorized,
            "title": self.title,
            "superuser": self.admin,
            "grp_authorized": self.groups,
            "msg_login": self.msg_login,
        }

        for key, val in self.attribs_cust.items():
            attribs[key] = val

        return attribs

    def __repr__(self) -> str:
        return str(self.to_dict())

    def __str__(self) -> str:
        return str(self.to_dict())

    def load(self) -> None:
        if not self.users.open_db():
            return

        u_entry: Entry | None = self.users.find_entry_by_username(self.username)
        if u_entry:
            self._load_from_entry(u_entry)

    def _load_from_entry(self, u_entry: Entry) -> bool:
        if not u_entry:
            return False

        self.exists = True
        self.is_authorized = True

        # set attributes from keepass entry property / custom_property
        self.uuid = str(u_entry.uuid)
        self.username = str(u_entry.username)
        self._pwd_hash = str(u_entry.password or "")
        self.title = str(u_entry.title or "")
        for property in u_entry.custom_properties:
            value = u_entry.get_custom_property(property)
            value = value if value else ""

            if property == UserField.ADMIN.db_key:
                self.admin = True if value and value.lower() == "true" else False
            elif property == UserField.AUTH_TYPE.db_key:
                auth_type = AuthType.from_value(value)
                if auth_type is None:
                    err_msg = f"Unknown AuthType '{value}' for user '{self.username}', defaulting to NONE"
                    log(err_msg, logging.WARNING)
                self.auth_type = auth_type or AuthType.NONE
            elif property == UserField.PWD_CHANGED_AT.db_key:
                try:
                    self._pwd_changed_at = datetime.fromisoformat(value) if value else None
                except ValueError:
                    self._pwd_changed_at = None
            elif property == UserField.OIDC_LINK.db_key:
                self.oidc_link = load_oidc_link(value)
            elif property == UserField.LOGIN_MSG.db_key:
                self.msg_login = value
            else:
                self.attribs_cust[property] = value

        # Retrieve groups from keepass entry tags
        tags = u_entry.tags if u_entry.tags else []
        for tag in tags:
            group = tag.lower().strip()
            self.groups.append(group)

        return True

    def save(self, save_db: bool = True) -> bool:
        """Updates the user's entry in memory. If `save_db` is False then save_db() is not called -
        the caller is responsible for persisting the change."""

        if self.users.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot save user")

        if not self.username or self.username == "":
            raise ValueError("User must have an Id / Username")

        if self.auth_type not in AuthType:
            raise ValueError(f"User can't have an unknown auth type: {self.auth_type.value}")

        self.users.open_db(save_db)  # batching (save_db=False) shouldn't reload to avoid discarding earlier changes
        if self.users.kee.db is None:
            raise RuntimeError("Database not opened, can't save it")

        # control
        if next(
            (u for u in self.users.kee_grp.entries if u.username == self.username and str(u.uuid) != self.uuid), None
        ):
            raise ValueError(f"{self.username} est déjà utilisé comme identifiant")

        # Get existing entry or create a new one
        u_entry = self.users.kee.db.find_entries(uuid=UUID(self.uuid), first=True) if self.uuid else None
        if not u_entry:
            u_entry = self.users.kee.db.add_entry(self.users.kee_grp, "", "", "")
            self.uuid = str(u_entry.uuid)
        elif not isinstance(u_entry, Entry):
            raise TypeError("Unexpected type from find_entries() with first=True")

        # Update entry
        u_entry.username = self.username
        u_entry.password = self._pwd_hash
        u_entry.title = self.title

        admin_val = "true" if self.admin else "false"
        pwd_changed_at_val = self._pwd_changed_at.isoformat() if self._pwd_changed_at else ""
        oidc_link = json.dumps(self.oidc_link.to_dict()) if self.oidc_link else ""

        u_entry.set_custom_property(UserField.ADMIN.db_key, admin_val)
        u_entry.set_custom_property(UserField.LOGIN_MSG.db_key, self.msg_login)
        u_entry.set_custom_property(UserField.AUTH_TYPE.db_key, self.auth_type.value)
        u_entry.set_custom_property(UserField.PWD_CHANGED_AT.db_key, pwd_changed_at_val)
        u_entry.set_custom_property(UserField.OIDC_LINK.db_key, oidc_link, protect=True)

        for attr, val in self.attribs_cust.items():
            u_entry.set_custom_property(attr, val)

        # Sanitize grp, keepass tags forbid colon and semi colon
        u_entry.tags = [grp.replace(",", "_").replace(";", "_") for grp in self.groups if not grp == "all"]

        if save_db:
            self.users.kee.save_db()
        self.users.get_all_groups(reload=False)

        self.exists = True
        self.is_authorized = True

        return True

    def delete(self) -> bool:
        self.users.open_db()
        if self.users.kee.db is None:
            raise RuntimeError("Database not opened, can't delete user")

        u_entry = self.users.kee.db.find_entries(uuid=UUID(self.uuid), first=True)
        if u_entry is None:
            raise LookupError("User not found")
        elif not isinstance(u_entry, Entry):
            raise TypeError("Unexpected type from find_entries() with first=True")

        u_entry.delete()
        self.users.kee.save_db()

        return True

    def export_infos_for_remote(self) -> dict:
        """Export infos needed to correctly deserialize queries from a remote."""
        return {
            "username": self.username,
            "title": self.title,
            "admin": self.admin,
            "msg_login": self.msg_login,
            "attribs_cust": self.attribs_cust,
        }

    @classmethod
    def create_from_remote_infos(cls, users: Users, data: dict) -> User:
        """Create a user from a remote's infos - Used to deserialize its queries."""
        user = User(users, detect_user=False)
        user.username = data.get("username", "")
        user.title = data.get("title", "")
        user.admin = data.get("admin", False)
        user.msg_login = data.get("msg_login", "")
        user.attribs_cust = data.get("attribs_cust", {})

        return user


def get_dummy_hash() -> str:
    """Generate Precomputed hash to be used as a default hash so an unknown user login takes
    the same time to be rejected as a known one (prevent account enumeration via response timing)"""
    return _DUMMY_HASH


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """If `password_hash` is an empty string then Use a default hash so an unknown user login takes
    the same time to be rejected as a known one (prevent account enumeration via response timing)"""
    hash_to_use = password_hash or get_dummy_hash()
    try:
        return _HASHER.verify(hash_to_use, password)
    except (VerifyMismatchError, InvalidHash):
        return False


def load_oidc_link(json_dumps: str) -> OidcLink | None:
    """Load an OidcLink dict from a json.dumps()."""

    if json_dumps.strip() == "":
        return

    try:
        json_loads = json.loads(json_dumps.strip())
    except ValueError:
        return

    if not isinstance(json_loads, dict):
        return

    try:
        oidc_link = OidcLink.from_dict(json_loads)
    except (ValueError, AttributeError, TypeError):
        err_msg = f"OIDC link load error from '{json_loads}'"
        log(err_msg, logging.WARNING)
        return

    return oidc_link if oidc_link.issuer else None


if __name__ == "__main__":
    setup_standalone_logging()

    kee = Kee()
    users = Users(kee)
    user = User(users)

    print(f"{'=' * 50}\nSettings / Utilisateur courant\n{'=' * 50}")
    for key, val in user.to_dict().items():
        print(f"- {key}: {val}")

    print(f"Groups list : {users.groups}")
