"""
Tests for Pytre_Core/keyring.py
"""

import keyring
import keyring.errors
import pytest
from cryptography.fernet import Fernet

from Pytre_Core.keyring import KeyringCatalog, _decrypt, _delete_key, _encrypt, _get_cipher, _get_key, _set_key

SERVICE = "Pytre_Test"
USERNAME = "test_user"


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory stand-in for the OS keyring backend - avoids touching the real
    system credential store during tests."""
    store: dict[tuple[str, str], str] = {}

    def fake_get_password(service, username):
        return store.get((service, username))

    def fake_set_password(service, username, password):
        store[(service, username)] = password

    def fake_delete_password(service, username):
        if (service, username) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[(service, username)]

    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    return store


@pytest.fixture
def catalog(fake_keyring):
    """KeyringCatalog.REMOTES_KEY, with its `_cipher` cache reset before and after - the enum
    member is a singleton, so its cache would otherwise leak state between tests."""
    KeyringCatalog.REMOTES_ENCRYPTION_KEY._cipher = None
    yield KeyringCatalog.REMOTES_ENCRYPTION_KEY
    KeyringCatalog.REMOTES_ENCRYPTION_KEY._cipher = None


# ================================================================================
# get_key
# ================================================================================


@pytest.mark.unit
def test_get_key_returns_none_when_absent(fake_keyring):
    assert _get_key(SERVICE, USERNAME) is None


@pytest.mark.unit
def test_get_key_returns_stored_value(fake_keyring):
    fake_keyring[(SERVICE, USERNAME)] = "c3RvcmVkLWtleQ=="

    result = _get_key(SERVICE, USERNAME)

    assert result == "c3RvcmVkLWtleQ=="


@pytest.mark.unit
def test_get_key_propagates_keyring_errors(monkeypatch):
    def fake_get_password(service, username):
        raise keyring.errors.NoKeyringError("no backend available")

    monkeypatch.setattr(keyring, "get_password", fake_get_password)

    with pytest.raises(keyring.errors.NoKeyringError):
        _get_key(SERVICE, USERNAME)


# ================================================================================
# get_key(create_missing=True)
# ================================================================================


@pytest.mark.unit
def test_get_key_create_missing_generates_and_stores_a_new_key(fake_keyring):
    result = _get_key(SERVICE, USERNAME, create_missing=True)

    assert result is not None
    assert fake_keyring[(SERVICE, USERNAME)] == result


@pytest.mark.unit
def test_get_key_create_missing_does_not_regenerate_an_existing_key(fake_keyring):
    fake_keyring[(SERVICE, USERNAME)] = "c3RvcmVkLWtleQ=="

    result = _get_key(SERVICE, USERNAME, create_missing=True)

    assert result == "c3RvcmVkLWtleQ=="


@pytest.mark.unit
def test_get_key_create_missing_generated_key_is_a_valid_fernet_key(fake_keyring):
    result = _get_key(SERVICE, USERNAME, create_missing=True)

    assert result is not None
    cipher = Fernet(result)
    assert cipher.decrypt(cipher.encrypt(b"data")) == b"data"


# ================================================================================
# set_key
# ================================================================================


@pytest.mark.unit
def test_set_key_stores_the_value(fake_keyring):
    _set_key(SERVICE, USERNAME, b"a-new-key")

    assert fake_keyring[(SERVICE, USERNAME)] == "a-new-key"


@pytest.mark.unit
def test_set_key_overwrites_existing_value(fake_keyring):
    fake_keyring[(SERVICE, USERNAME)] = "old-key"

    _set_key(SERVICE, USERNAME, b"new-key")

    assert fake_keyring[(SERVICE, USERNAME)] == "new-key"


@pytest.mark.unit
def test_set_key_propagates_keyring_errors(monkeypatch):
    def fake_set_password(service, username, password):
        raise keyring.errors.PasswordSetError("backend refused")

    monkeypatch.setattr(keyring, "set_password", fake_set_password)

    with pytest.raises(keyring.errors.PasswordSetError):
        _set_key(SERVICE, USERNAME, b"value")


# ================================================================================
# delete_key
# ================================================================================


@pytest.mark.unit
def test_delete_key_removes_existing_entry(fake_keyring):
    fake_keyring[(SERVICE, USERNAME)] = "some-key"

    _delete_key(SERVICE, USERNAME)

    assert (SERVICE, USERNAME) not in fake_keyring


@pytest.mark.unit
def test_delete_key_is_a_noop_when_nothing_to_delete(fake_keyring):
    _delete_key(SERVICE, USERNAME)  # must not raise


@pytest.mark.unit
def test_delete_key_propagates_non_delete_keyring_errors(monkeypatch):
    def fake_delete_password(service, username):
        raise keyring.errors.NoKeyringError("no backend available")

    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    with pytest.raises(keyring.errors.NoKeyringError):
        _delete_key(SERVICE, USERNAME)


# ================================================================================
# _get_cipher
# ================================================================================


@pytest.mark.unit
def test_get_cipher_creates_a_key_when_none_exists(fake_keyring):
    cipher = _get_cipher(SERVICE, USERNAME, None)

    assert (SERVICE, USERNAME) in fake_keyring
    assert cipher.decrypt(cipher.encrypt(b"data")) == b"data"


@pytest.mark.unit
def test_get_cipher_returns_current_unchanged_when_given_and_not_reloading(fake_keyring):
    current = Fernet(Fernet.generate_key())

    result = _get_cipher(SERVICE, USERNAME, current)

    assert result is current
    assert (SERVICE, USERNAME) not in fake_keyring  # never touched the keyring


@pytest.mark.unit
def test_get_cipher_rebuilds_from_stored_key_when_reload_is_true(fake_keyring):
    stored_key = Fernet.generate_key()
    fake_keyring[(SERVICE, USERNAME)] = stored_key.decode("utf-8")
    stale = Fernet(Fernet.generate_key())

    result = _get_cipher(SERVICE, USERNAME, stale, reload=True)

    assert result is not stale
    assert result.decrypt(Fernet(stored_key).encrypt(b"data")) == b"data"


# ================================================================================
# _encrypt / _decrypt
# ================================================================================


@pytest.mark.unit
def test_encrypt_empty_text_returns_empty_string():
    cipher = Fernet(Fernet.generate_key())

    assert _encrypt(cipher, "") == ""


@pytest.mark.unit
def test_decrypt_empty_text_returns_empty_string():
    cipher = Fernet(Fernet.generate_key())

    assert _decrypt(cipher, "") == ""


@pytest.mark.unit
def test_encrypt_decrypt_round_trips_a_str():
    cipher = Fernet(Fernet.generate_key())

    encrypted = _encrypt(cipher, "s3cret")

    assert encrypted != "s3cret"
    assert _decrypt(cipher, encrypted) == "s3cret"


@pytest.mark.unit
def test_encrypt_accepts_bytes():
    cipher = Fernet(Fernet.generate_key())

    encrypted = _encrypt(cipher, b"s3cret")

    assert _decrypt(cipher, encrypted) == "s3cret"


@pytest.mark.unit
def test_decrypt_rejects_a_non_empty_invalid_token():
    cipher = Fernet(Fernet.generate_key())

    with pytest.raises(Exception):
        _decrypt(cipher, "not-a-valid-token")


# ================================================================================
# KeyringCatalog
# ================================================================================


@pytest.mark.unit
def test_keyring_catalog_member_has_service_and_username():
    assert KeyringCatalog.REMOTES_ENCRYPTION_KEY.service == "Pytre_Client"
    assert KeyringCatalog.REMOTES_ENCRYPTION_KEY.username == "remotes_encryption_key"


@pytest.mark.unit
def test_keyring_catalog_get_key_returns_none_when_absent(catalog):
    assert catalog.get_key() is None


@pytest.mark.unit
def test_keyring_catalog_get_key_create_missing_generates_and_stores(catalog, fake_keyring):
    result = catalog.get_key(create_missing=True)

    assert result is not None
    assert (catalog.service, catalog.username) in fake_keyring


@pytest.mark.unit
def test_keyring_catalog_set_key_stores_the_value(catalog, fake_keyring):
    value = Fernet.generate_key()

    catalog.set_key(value)

    assert fake_keyring[(catalog.service, catalog.username)] == value.decode("utf-8")


@pytest.mark.unit
def test_keyring_catalog_set_key_invalidates_cached_cipher(catalog, fake_keyring):
    """set_key() must not build a Fernet from `value` directly - `value` isn't always a Fernet
    key (e.g. SERVER_MASTER_KEY stores a plain password) - it must just drop any cached cipher
    so the next get_cipher() call rebuilds it from whatever's actually stored."""
    catalog.get_cipher()  # populate the cache first
    assert catalog._cipher is not None

    catalog.set_key(Fernet.generate_key())

    assert catalog._cipher is None


@pytest.mark.unit
def test_keyring_catalog_set_key_accepts_non_fernet_values(catalog, fake_keyring):
    """set_key() must work for catalog members that don't store Fernet keys at all (e.g. a
    plain KeePass master password) - it must never try to build a Fernet from `value`."""
    catalog.set_key("not-a-fernet-key")  # must not raise

    assert fake_keyring[(catalog.service, catalog.username)] == "not-a-fernet-key"


@pytest.mark.unit
def test_keyring_catalog_delete_key_removes_value_and_clears_cache(catalog, fake_keyring):
    catalog.set_key(Fernet.generate_key())

    catalog.delete_key()

    assert (catalog.service, catalog.username) not in fake_keyring
    assert catalog._cipher is None


@pytest.mark.unit
def test_keyring_catalog_get_cipher_caches_across_calls(catalog, fake_keyring):
    first = catalog.get_cipher()

    del fake_keyring[(catalog.service, catalog.username)]
    second = catalog.get_cipher()

    assert first is second


@pytest.mark.unit
def test_keyring_catalog_get_cipher_reload_rebuilds(catalog):
    first = catalog.get_cipher()

    second = catalog.get_cipher(reload=True)

    assert first is not second


@pytest.mark.unit
def test_keyring_catalog_encrypt_decrypt_round_trip(catalog):
    encrypted = catalog.encrypt("s3cret")

    assert encrypted != "s3cret"
    assert catalog.decrypt(encrypted) == "s3cret"


@pytest.mark.unit
def test_keyring_catalog_encrypt_decrypt_empty_text(catalog):
    assert catalog.encrypt("") == ""
    assert catalog.decrypt("") == ""
