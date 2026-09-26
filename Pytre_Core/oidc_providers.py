import logging
from enum import Enum
from urllib.parse import urlparse
from uuid import UUID

from pykeepass.entry import Entry
from pykeepass.group import Group

from Pytre_Core.kee import Kee
from Pytre_Core.utils import log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class _DbKind(Enum):
    NATIVE = "native"  # entry property / attribute
    CUSTOM = "custom"  # entry custom property


class OidcProviderField(Enum):
    """Registry of OIDC provider's predefined KeePass storage fields - single source of truth
    for where each one lives (db_kind/db_key)."""

    ENABLED = (_DbKind.CUSTOM, "enabled")

    LABEL = (_DbKind.NATIVE, "notes")
    ISSUER = (_DbKind.NATIVE, "title")
    CLIENT_ID = (_DbKind.NATIVE, "username")
    CLIENT_SECRET = (_DbKind.NATIVE, "password")

    SCOPES = (_DbKind.CUSTOM, "scopes")
    USERNAME_CLAIM = (_DbKind.CUSTOM, "username_claim")
    PUBLIC_BASE_URL = (_DbKind.CUSTOM, "public_base_url")

    def __init__(self, db_kind: _DbKind, db_key: str):
        self.db_kind: _DbKind = db_kind
        self.db_key: str = db_key


class OidcProviders:
    def __init__(self, kee: Kee) -> None:
        self.kee: Kee = kee
        self.kee_grp: Group | None = None

        self.get_all_providers()

    def open_db(self, reload: bool = False) -> bool:
        self.kee.open_db(reload)
        if self.kee.is_ko:
            return False
        self.kee_grp = self.kee.grp_oidc
        return True

    def get_all_providers(self, reload: bool = False) -> dict[str, OidcProvider]:
        self.open_db(reload)

        if self.kee_grp is None:
            return {}

        oidcs = (OidcProvider(entry=entry, providers=self) for entry in self.kee_grp.entries)

        return {oidc.issuer: oidc for oidc in oidcs}

    def get_usable_providers(self, reload: bool = False) -> dict[str, OidcProvider]:
        """Returns usable OIDC Providers."""
        return {k: o for k, o in self.get_all_providers(reload).items() if o.is_usable}

    def find_entry_by_uuid(self, uuid: str) -> Entry | None:
        if self.kee.db is None:
            raise RuntimeError("Database not opened, can't find OIDC provider entry by uuid")
        if self.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot find OIDC provider entry by uuid")

        entry = self.kee.db.find_entries(uuid=UUID(uuid), first=True)

        return entry if entry and isinstance(entry, Entry) else None

    def find_provider_by_uuid(self, uuid: str) -> OidcProvider | None:
        entry = self.find_entry_by_uuid(uuid)
        return OidcProvider(self, entry=entry) if entry else None


class OidcProvider:
    def __init__(self, providers: OidcProviders, entry: Entry | None = None) -> None:
        self.providers: OidcProviders = providers

        self.uuid: str = ""
        self.enabled: bool = False
        self.raw_label: str = ""
        self.issuer: str = ""
        self.client_id: str = ""
        self.client_secret: str = ""
        self.scopes: str = "openid"
        self.username_claim: str = "preferred_username"  # claim used to find the Pytre user at first login
        self.public_base_url: str = ""  # base of the redirect URLs, explicit to stay right with a reverse proxy

        if entry:
            self._load_from_entry(entry)

    @property
    def is_usable(self) -> bool:
        """A usable OIDC provider is enabled and has its issuer, client id and client secret set."""
        return all(attr for attr in (self.enabled, self.issuer, self.client_id, self.client_secret))

    @property
    def label(self) -> str:
        """Provide a label even when None has been defined."""
        return self.raw_label or urlparse(self.issuer).netloc or self.issuer

    @label.setter
    def label(self, value) -> None:
        self.raw_label = value

    def _load_from_entry(self, entry: Entry):
        self.uuid = str(entry.uuid)
        self.raw_label = str(getattr(entry, OidcProviderField.LABEL.db_key) or "")
        self.issuer = str(getattr(entry, OidcProviderField.ISSUER.db_key) or "")
        self.client_id = str(getattr(entry, OidcProviderField.CLIENT_ID.db_key) or "")
        self.client_secret = str(getattr(entry, OidcProviderField.CLIENT_SECRET.db_key) or "")

        enabled = entry.get_custom_property(OidcProviderField.ENABLED.db_key) or ""
        self.enabled = enabled.lower() == "true"

        self.scopes = entry.get_custom_property(OidcProviderField.SCOPES.db_key) or self.scopes
        self.username_claim = entry.get_custom_property(OidcProviderField.USERNAME_CLAIM.db_key) or self.username_claim
        self.public_base_url = (
            entry.get_custom_property(OidcProviderField.PUBLIC_BASE_URL.db_key) or self.public_base_url
        )

    def reload(self) -> None:
        self.providers.open_db(True)
        entry = self.providers.find_entry_by_uuid(self.uuid)
        if entry is not None:
            self._load_from_entry(entry)

    def save(self, save_db: bool = True) -> bool:
        """Updates the OIDC provider's entry in memory. If `save_db` is False then save_db() is not called -
        the caller is responsible for persisting the change."""

        if self.providers.kee_grp is None:
            raise ValueError("No KeePass group selected, cannot save OIDC provider")

        self.providers.open_db(
            save_db
        )  # batching (save_db=False) shouldn't reload to avoid discarding earlier changes
        if self.providers.kee.db is None:
            raise RuntimeError("Database not opened, can't save it")

        # control
        if any(attr is None or attr.strip() == "" for attr in (self.issuer, self.client_id)):
            raise ValueError("OIDC provider must have an issuer and a client id")

        if [
            o
            for o in self.providers.get_all_providers().values()
            if o.issuer == self.issuer and not o.uuid == self.uuid
        ]:
            raise ValueError(f"{self.issuer} est déjà défini")

        # Get existing entry or create a new one
        entry = self.providers.kee.db.find_entries(uuid=UUID(self.uuid), first=True) if self.uuid else None
        if not entry:
            entry = self.providers.kee.db.add_entry(self.providers.kee_grp, "", "", "")
            self.uuid = str(entry.uuid)
        elif not isinstance(entry, Entry):
            raise TypeError("Unexpected type from find_entries() with first=True")

        # Update entry
        setattr(entry, OidcProviderField.LABEL.db_key, self.raw_label)
        setattr(entry, OidcProviderField.ISSUER.db_key, self.issuer)
        setattr(entry, OidcProviderField.CLIENT_ID.db_key, self.client_id)
        setattr(entry, OidcProviderField.CLIENT_SECRET.db_key, self.client_secret)
        entry.set_custom_property(OidcProviderField.ENABLED.db_key, "true" if self.enabled else "false")
        entry.set_custom_property(OidcProviderField.SCOPES.db_key, self.scopes)
        entry.set_custom_property(OidcProviderField.USERNAME_CLAIM.db_key, self.username_claim)
        entry.set_custom_property(OidcProviderField.PUBLIC_BASE_URL.db_key, self.public_base_url)

        if save_db:
            self.providers.kee.save_db()

        return True

    def delete(self) -> bool:
        self.providers.open_db()
        if self.providers.kee.db is None:
            raise RuntimeError("Database not opened, can't delete OIDC provider")

        entry = self.providers.kee.db.find_entries(uuid=UUID(self.uuid), first=True)
        if entry is None:
            raise LookupError("OIDC provider not found")
        elif not isinstance(entry, Entry):
            raise TypeError("Unexpected type from find_entries() with first=True")

        entry.delete()
        self.providers.kee.save_db()

        return True
