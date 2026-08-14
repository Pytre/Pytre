import keyring
import keyring.errors
import logging
from cryptography.fernet import Fernet
from enum import Enum

from Pytre_Core.utils import log as _log

ENCODING = "utf-8"


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


# ------------------------------------------------------------------------------------------
# Catalog of (service, username) pairs used by Pytre
# ------------------------------------------------------------------------------------------
class KeyringCatalog(Enum):
    REMOTES_ENCRYPTION_KEY = ("Pytre_Client", "remotes_encryption_key")
    SERVER_MASTER_KEY = ("Pytre_Server", "master_key")

    def __init__(self, service: str, username: str):
        self.service: str = service
        self.username: str = username
        self._cipher: Fernet | None = None

    # ------------------------------------------------------------------------------------------
    # system keyring methods / delegated to module functions
    # ------------------------------------------------------------------------------------------
    def get_key(self, create_missing: bool = False) -> str | None:
        """Returns the stored key or None if it does not exists.
        When `create_missing` is True a new key is generated and returned if none exists."""
        return _get_key(self.service, self.username, create_missing=create_missing)

    def set_key(self, value: str | bytes) -> None:
        """Stores or updates `value` under (service, username)."""
        _set_key(self.service, self.username, value)
        self._cipher = None  # remove invalid cache

    def delete_key(self) -> None:
        """Deletes the stored key if it exists."""
        _delete_key(self.service, self.username)
        self._cipher = None  # remove invalid cache

    # ------------------------------------------------------------------------------------------
    # Encryption methods / delegated to module functions
    # ------------------------------------------------------------------------------------------
    def get_cipher(self, reload: bool = False) -> Fernet:
        self._cipher = _get_cipher(self.service, self.username, self._cipher, reload=reload)
        return self._cipher

    def encrypt(self, text: str | bytes) -> str:
        return _encrypt(self.get_cipher(), text)

    def decrypt(self, text: str | bytes) -> str:
        return _decrypt(self.get_cipher(), text)


# ------------------------------------------------------------------------------------------
# Functions to create / retrieve / set / delete keys from system keyring
# ------------------------------------------------------------------------------------------
def _get_key(service: str, username: str, create_missing: bool = False) -> str | None:
    """Returns the stored key or None if it does not exists.
    When `create_missing` is True a new key is generated and returned if none exists."""
    try:
        value = keyring.get_password(service, username)
    except keyring.errors.KeyringError as e:
        log(f"Error while retrieving key from keyring ({service}/{username}) : {e}", logging.ERROR)
        raise

    if value is not None:
        return value

    if create_missing:
        new_key = Fernet.generate_key().decode(ENCODING)
        _set_key(service, username, new_key)
        return new_key

    return None


def _set_key(service: str, username: str, value: str | bytes):
    """Stores or updates `value` under (service, username)."""
    str_txt = value if isinstance(value, str) else value.decode(ENCODING)
    try:
        keyring.set_password(service, username, str_txt)
    except keyring.errors.KeyringError as e:
        log(f"Error while storing key in keyring ({service}/{username}) : {e}", logging.ERROR)
        raise


def _delete_key(service: str, username: str) -> None:
    """Deletes the stored key if it exists."""
    try:
        keyring.delete_password(service, username)
    except keyring.errors.PasswordDeleteError:
        pass  # nothing to delete
    except keyring.errors.KeyringError as e:
        log(f"Error while deleting key from keyring ({service}/{username}) : {e}", logging.ERROR)
        raise


# ------------------------------------------------------------------------------------------
# Functions to retrieve cipher and to encrypt / decrypt
# ------------------------------------------------------------------------------------------
def _get_cipher(service: str, username: str, current: Fernet | None, reload: bool = False) -> Fernet:
    """Build a Fernet using key stored on system keyring (adding one if none exists yet).
    Returns `current` unless it's None or `reload` is True."""

    if not reload and current is not None:
        return current

    key = _get_key(service, username, create_missing=True)
    assert key is not None

    return Fernet(key)


def _encrypt(cipher: Fernet, text: str | bytes) -> str:
    if not text:
        return ""

    bytes_txt = text.encode(ENCODING) if isinstance(text, str) else text
    return cipher.encrypt(bytes_txt).decode(ENCODING)


def _decrypt(cipher: Fernet, text: str | bytes) -> str:
    if not text:
        return ""
    return cipher.decrypt(text).decode(ENCODING)
