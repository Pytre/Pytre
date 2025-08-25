from __future__ import annotations

import os
import getpass

import csv
from uuid import UUID
from pathlib import Path
from tkinter import messagebox

from pykeepass.entry import Entry
from pykeepass.group import Group

from kee import Kee
from singleton_metaclass import Singleton


class Users(metaclass=Singleton):
    def __init__(self):
        self.kee: Kee = Kee()
        self.kee_grp: Group = None
        self.kee_servers_grp: Group = None

        self.groups: set[str] = set()

        self.attribs_std: tuple[str] = ("username", "title", "admin", "msg_login")
        self.attribs_cust: list[str] = list()  # liste des attributs spéciaux

        self.get_all_groups()

    def open_db(self, reload: bool = False) -> bool:
        self.kee._open_db(reload)
        if self.kee.is_ko:
            return False
        self.kee_grp = self.kee.grp_users
        self.kee_servers_grp = self.kee.grp_servers
        return True

    def users_admin_exists(self, reload: bool = False) -> bool:
        self.open_db(reload)

        admin_exists = False

        u_entry: Entry | None = None
        for u_entry in self.kee_grp.entries:
            if u_entry.get_custom_property("superuser") == "true":
                admin_exists = True
                break

        return admin_exists

    def get_all_users(self, reload: bool = True) -> list:
        self.open_db(reload)

        u_list: list[User] = []
        attribs_set: set[str] = set()
        for entry in self.kee_grp.entries:
            u_list.append(User(entry=entry))
            attribs_set.update(self._get_cust_attribs_list(entry))

        self.attribs_cust = sorted(attribs_set)

        return u_list

    def get_all_groups(self, reload: bool = False) -> set:
        self.open_db(reload)

        self.groups = set()
        for entry in self.kee_grp.entries + self.kee_servers_grp.entries:
            tags = set(map(lambda val: val.lower().strip(), entry.tags)) if entry.tags else []
            self.groups.update(tags) if tags else None

        return self.groups

    def get_cust_attribs_list(self) -> list[str]:
        self.open_db(False)

        u_entry: Entry | None = None
        attribs_set: set[str] = set()

        for u_entry in self.kee_grp.entries:
            attribs_set.update(self._get_cust_attribs_list(u_entry))

        self.attribs_cust = sorted(attribs_set)

        return self.attribs_cust

    def _get_cust_attribs_list(self, entry: Entry) -> set[str]:
        attribs_set: set[str] = set()

        for property in entry.custom_properties:
            if property not in self.attribs_std + ("superuser",):
                attribs_set.add(property)

        return attribs_set

    def users_add_groups(self, usernames: list[str], groups: list[str]):
        self.open_db()

        save: bool = False

        groups = [group for group in groups if not group == "all"]
        u_entry: Entry | None = None
        for u_entry in self.kee_grp.entries:
            if u_entry.username not in usernames:
                continue

            new_groups = u_entry.tags if u_entry.tags else []
            new_groups = list(set(new_groups + groups))

            if not new_groups == u_entry.tags:
                u_entry.tags = new_groups
                save = True

        if save:
            self.kee.save_db()

    def users_remove_groups(self, usernames: list[str], groups: list[str]):
        self.open_db()

        save: bool = False

        u_entry: Entry | None = None
        for u_entry in self.kee_grp.entries:
            if u_entry.username not in usernames or not u_entry.tags:
                continue

            new_groups: list[str] = u_entry.tags
            for tag in u_entry.tags:
                if tag in groups:
                    new_groups.remove(tag)

            if not new_groups == u_entry.tags:
                u_entry.tags = new_groups
                save = True

        if save:
            self.kee.save_db()

    def csv_import(self, filename: Path, delimiter: str = ";") -> bool:
        self.open_db(True)

        entries_dict = {}
        u_entry: Entry
        for u_entry in self.kee_grp.entries:
            entries_dict[u_entry.username] = u_entry.username.upper()

        cols_std = ["username", "title", "superuser", "grp_authorized", "msg_login"]
        cols_cust = self.get_cust_attribs_list()
        cols_dict = {}
        for i, val in enumerate(cols_std + cols_cust):
            cols_dict[val] = i

        with open(filename, mode="r", encoding="latin-1") as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=delimiter, quotechar='"')

            for i, row in enumerate(csv_reader):
                if i == 0:
                    continue

                # mapping des colonnes
                row_dict = {}
                for key, val in cols_dict.items():
                    row_dict[key] = row[val]

                # contrôle si utilisateurs à modifier ou à créer
                if row_dict["username"].upper() in entries_dict.keys():
                    u_entry = entries_dict[row_dict["username"]]
                else:
                    u_entry = self.kee.db.add_entry(self.kee_grp, "", row_dict["username"], "")

                # modification des entry de la base
                u_entry.title = row_dict["title"]

                if row_dict["superuser"] == "true":
                    u_entry.set_custom_property("superuser", "true")
                else:
                    u_entry.set_custom_property("superuser", "false")

                u_entry.set_custom_property("msg_login", row_dict["msg_login"])

                for prop in cols_cust:
                    u_entry.set_custom_property(prop, row_dict[prop])

                groups = row_dict["grp_authorized"].split(delimiter)
                u_entry.tags = [group for group in groups if not group == "all"]

            # une fois que toutes les entries sont à jour, sauvegarde de la base
            self.kee.save_db()

            return True

    def csv_export(self, filename: Path, delimiter: str = ";", overwrite: bool = False) -> bool:
        if Path(filename).exists() and not overwrite:
            return False

        items_std = ["superuser", "group", "msg_login"]
        items_cust = self.get_cust_attribs_list()

        rows = [["Id", "Libellé"] + ["Admin", "Groupes", "Login Message"] + items_cust]
        self.open_db(True)
        u_entry: Entry | None
        for u_entry in self.kee_grp.entries:
            row = [u_entry.username, u_entry.title]

            tags = u_entry.tags if u_entry.tags else []
            group = delimiter.join(tags)

            properties_dict = {}
            for property in u_entry.custom_properties:
                value = u_entry.get_custom_property(property)
                properties_dict[property] = value

            for item in items_std + items_cust:
                if item == "group":
                    value = group
                else:
                    value = u_entry.get_custom_property(item)

                value = value if value is not None else ""
                row.append(value)

            rows.append(row)

        with open(filename, mode="w", encoding="latin-1", newline="") as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=delimiter, quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerows(rows)

        return True

    def get_current_username(self) -> str:
        domain = os.environ.get("userdnsdomain") or ""
        name = getpass.getuser()
        domain_and_name = f"{domain}\\{name}" if domain else name
        return domain_and_name

    def find_entry_by_username(self, username: str) -> Entry | None:
        for entry in self.kee_grp.entries:
            if entry.username and username.upper() == entry.username.upper():
                return entry

        return None

    def find_user_by_uuid(self, uuid: str) -> User | None:
        entry: Entry = self.kee.db.find_entries(uuid=UUID(uuid), first=True)
        return User(entry=entry) if entry else None


class User:
    def __init__(self, username: str = "", entry: Entry = None, detect_user: bool = True):
        """
        si pas de username et d'entry, et que detect_user est faux
        alors pas de chrgmt à partir de la base keepass
        """

        self.users: Users = Users()

        self.uuid: str = ""
        self.username: str = ""
        self.exists: bool = False
        self.is_authorized: bool = False
        self.title: str = ""
        self.admin: bool = False
        self.grp_authorized: list[str] = ["all"]  # par défaut un utilisateur appartient au groupe all
        self.msg_login_cust: str = ""
        self.msg_login: str = ""
        self.attribs_cust: dict[str, str] = {}  # dictionnaire des attributs spéciaux

        if entry:
            self._load_from_entry(entry)
        elif username or detect_user:
            self.username = username if username else self.users.get_current_username()
            self.load()

        if not self.users.users_admin_exists():
            msg = (
                "Aucun administrateur n'existe !\n"
                + "Vous allez être ajouté en tant qu'administrateur par défaut.\n"
                + "Pensez à mettre à jour les utilisateurs après."
            )
            messagebox.showinfo(title="Base créée", message=msg)
            self.admin = True
            self.save()

    def to_dict(self) -> dict:
        attribs = {
            "uuid": self.uuid,
            "id": self.username,
            "exist_in_settings": self.exists,
            "is_authorized": self.is_authorized,
            "title": self.title,
            "superuser": self.admin,
            "grp_authorized": self.grp_authorized,
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

        u_entry: Entry = self.users.find_entry_by_username(self.username)
        if u_entry:
            self._load_from_entry(u_entry)

    def _load_from_entry(self, u_entry: Entry) -> bool:
        if not u_entry:
            return False

        self.exists = True
        self.is_authorized = True

        # récup propriétés
        self.uuid = str(u_entry.uuid)
        self.username = u_entry.username
        self.title = u_entry.title
        for property in u_entry.custom_properties:
            value = u_entry.get_custom_property(property)
            value = value if value else ""

            if property == "superuser":
                self.admin = True if value and value.lower() == "true" else False
            elif property == "msg_login":
                self.msg_login_cust = value
            elif property in self.users.attribs_std:
                setattr(self, property, value)
            else:
                self.attribs_cust[property] = value

        # récup info groupes de l'utilisateur
        tags = u_entry.tags if u_entry.tags else []
        for tag in tags:
            group = tag.lower().strip()
            self.grp_authorized.append(group)

        # ajustement message d'accueil
        if self.msg_login_cust:
            self.msg_login = self.msg_login_cust
        elif self.title:
            self.msg_login = f"Bonjour {self.title.split(' ')[0]} !"
        else:
            self.msg_login = "Bonjour !"

        return True

    def save(self) -> bool:
        if not self.username or self.username == "":
            raise ValueError("User must have an Id / Username")

        self.users.open_db(True)

        # contrôle
        if [u for u in self.users.get_all_users() if u.username == self.username and not u.uuid == self.uuid]:
            raise ValueError(f"{self.username} est déjà utilisé comme identifiant")

        # récupération d'une entrée pour la sauvegarde
        u_entry: Entry = self.users.kee.db.find_entries(uuid=UUID(self.uuid), first=True) if self.uuid else None
        if not u_entry:
            u_entry: Entry = self.users.kee.db.add_entry(self.users.kee_grp, "", "", "")
            self.uuid = str(u_entry.uuid)

        # mise à jour de l'entrée
        u_entry.username = self.username
        u_entry.title = self.title
        admin_val = "true" if self.admin else "false"
        u_entry.set_custom_property("superuser", admin_val)
        u_entry.set_custom_property("msg_login", self.msg_login_cust)

        for attr, val in self.attribs_cust.items():
            u_entry.set_custom_property(attr, val)

        u_entry.tags = [grp for grp in self.grp_authorized if not grp == "all"]

        self.users.kee.save_db()

        self.exists = True
        self.is_authorized = True

        return True

    def delete(self) -> bool:
        self.users.open_db()

        u_entry: Entry = self.users.kee.db.find_entries(uuid=UUID(self.uuid), first=True)
        if u_entry is None:
            raise LookupError("User not found")

        u_entry.delete()
        self.users.kee.save_db()

        return True


class CurrentUser(User, metaclass=Singleton):
    pass


if __name__ == "__main__":
    user = CurrentUser()

    print(f"{'='*50}\nSettings / Utilisateur courant\n{'='*50}")
    for key, val in user.to_dict().items():
        print(f"- {key}: {val}")

    users = Users()
    print(f"Groups list : {users.groups}")
