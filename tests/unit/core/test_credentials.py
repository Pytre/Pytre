"""
Tests for Pytre/credentials.py

REGRESSION TESTS:
- Binary file mode for encryption/decryption (CRITICAL SECURITY FIX)
"""

import pytest
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

from Pytre_Core.credentials import (
    create_key_and_export,
    retrieve_key,
    crypt_to_file,
    decrypt_from_file,
    crypted_file_pwd_change,
    crypted_file_pwd_get,
)


# ================================================================================
# REGRESSION TESTS - Binary File Mode
# ================================================================================


@pytest.mark.security
@pytest.mark.regression
def test_decrypt_reads_binary_mode(tmp_path, test_encryption_key):
    """
    Regression test: must use 'rb' not 'r'

    CRITICAL: decrypt_from_file() must open file in binary mode.
    Previous bug: opened in text mode, causing TypeError when decrypt() received string.
    """
    # Setup: Create encrypted file
    file_path = tmp_path / "encrypted.bin"
    cipher = Fernet(test_encryption_key)
    encrypted = cipher.encrypt(b"secret data")
    file_path.write_bytes(encrypted)

    # Test: Should NOT raise TypeError about str vs bytes
    result = decrypt_from_file(file_path, test_encryption_key)

    # Verify
    assert result == "secret data"
    assert isinstance(result, str)


@pytest.mark.security
@pytest.mark.regression
def test_retrieve_key_reads_binary_mode(tmp_path):
    """
    Regression test: retrieve_key must use 'rb'

    CRITICAL: Key file must be read as bytes, not string.
    """
    # Setup: Create key file
    key_file = tmp_path / "test.key"
    test_key = Fernet.generate_key()
    key_file.write_bytes(test_key)

    # Test
    result = retrieve_key(key_file)

    # Verify: Should return bytes
    assert isinstance(result, bytes)
    assert result == test_key


@pytest.mark.security
def test_encryption_roundtrip(tmp_path, test_encryption_key):
    """
    Encrypt then decrypt should return original text.

    This validates the complete encryption/decryption cycle with binary mode.
    """
    file_path = tmp_path / "test.enc"
    original = "test password with unicode: éàç"

    # Encrypt
    crypt_to_file(file_path, test_encryption_key, original)

    # Decrypt
    result = decrypt_from_file(file_path, test_encryption_key)

    # Verify
    assert result == original


@pytest.mark.security
def test_encryption_handles_unicode(tmp_path, test_encryption_key):
    """Test encryption/decryption with various unicode characters"""
    file_path = tmp_path / "unicode.enc"
    original = "Spécial chars: é à ç ñ ü 中文 🔐"

    crypt_to_file(file_path, test_encryption_key, original)
    result = decrypt_from_file(file_path, test_encryption_key)

    assert result == original


@pytest.mark.security
def test_encryption_handles_empty_string(tmp_path, test_encryption_key):
    """Test encryption/decryption with empty string"""
    file_path = tmp_path / "empty.enc"
    original = ""

    crypt_to_file(file_path, test_encryption_key, original)
    result = decrypt_from_file(file_path, test_encryption_key)

    assert result == original


# ================================================================================
# Decryption Error Handling
# ================================================================================


def test_decrypt_nonexistent_file_raises_error(test_encryption_key):
    """decrypt_from_file should raise FileNotFoundError for missing file"""
    nonexistent = Path("/nonexistent/file.enc")

    with pytest.raises(FileNotFoundError):
        decrypt_from_file(nonexistent, test_encryption_key)


def test_decrypt_corrupted_file_raises_invalidtoken(tmp_path, test_encryption_key):
    """decrypt_from_file should raise InvalidToken for corrupted data"""
    corrupted_file = tmp_path / "corrupted.enc"
    corrupted_file.write_bytes(b"this is not encrypted data")

    with pytest.raises(InvalidToken):
        decrypt_from_file(corrupted_file, test_encryption_key)


def test_decrypt_with_wrong_key_raises_invalidtoken(tmp_path):
    """Decrypting with wrong key should raise InvalidToken"""
    file_path = tmp_path / "encrypted.bin"

    # Encrypt with one key
    key1 = Fernet.generate_key()
    cipher1 = Fernet(key1)
    encrypted = cipher1.encrypt(b"secret")
    file_path.write_bytes(encrypted)

    # Try to decrypt with different key
    key2 = Fernet.generate_key()

    with pytest.raises(InvalidToken):
        decrypt_from_file(file_path, key2)


# ================================================================================
# Key Creation and Export
# ================================================================================


def test_create_key_and_export_creates_file(tmp_path):
    """create_key_and_export should create a valid key file"""
    key_file = tmp_path / "new.key"

    create_key_and_export(key_file)

    assert key_file.exists()
    assert key_file.stat().st_size > 0


def test_create_key_generates_valid_fernet_key(tmp_path):
    """Generated key should be valid for Fernet encryption"""
    key_file = tmp_path / "test.key"

    create_key_and_export(key_file)
    key = retrieve_key(key_file)

    # Should not raise error
    cipher = Fernet(key)

    # Should be able to encrypt/decrypt
    test_data = b"test"
    encrypted = cipher.encrypt(test_data)
    decrypted = cipher.decrypt(encrypted)

    assert decrypted == test_data


# ================================================================================
# Key file (settings database password)
# ================================================================================


@pytest.fixture
def key_file(tmp_path, monkeypatch, test_encryption_key):
    """KEY_FILE in tmp_path, encrypted with a test key rather than the developer's credentials_secrets.py"""
    import Pytre_Core.credentials as cred_module

    pwd_file = tmp_path / "Pytre.key"
    monkeypatch.setattr(cred_module, "KEY_FILE", pwd_file)
    monkeypatch.setattr(cred_module, "_secret_key", lambda: test_encryption_key)
    return pwd_file


def test_crypted_file_pwd_change_creates_file(key_file):
    crypted_file_pwd_change("password123")

    assert key_file.exists()


def test_crypted_file_pwd_get_returns_the_recorded_password(key_file):
    crypted_file_pwd_change("password1")
    crypted_file_pwd_change("password2")

    assert crypted_file_pwd_get() == "password2"


def test_crypted_file_pwd_change_records_only_the_current_password(key_file, test_encryption_key):
    crypted_file_pwd_change("password1")
    crypted_file_pwd_change("password2")

    assert decrypt_from_file(key_file, test_encryption_key) == "password2"


def test_crypted_file_pwd_get_reads_first_line_of_a_legacy_history_file(key_file, test_encryption_key):
    """Key files written by older versions hold a password history, the current password first"""
    crypt_to_file(key_file, test_encryption_key, "current\nold1\nold2")

    assert crypted_file_pwd_get() == "current"


def test_crypted_file_pwd_get_without_key_file_returns_empty_string(key_file):
    assert crypted_file_pwd_get() == ""


# ================================================================================
# credentials_secrets.py is optional (pure client mode)
# ================================================================================


@pytest.fixture
def without_credentials_secrets(monkeypatch):
    """Simulates a missing Pytre_Core/credentials_secrets.py - importing it raises ImportError"""
    import sys

    monkeypatch.setitem(sys.modules, "Pytre_Core.credentials_secrets", None)


@pytest.mark.usefixtures("without_credentials_secrets")
def test_credentials_module_imports_without_credentials_secrets():
    import importlib

    import Pytre_Core.credentials as cred_module

    importlib.reload(cred_module)  # module level code must not need the secret key


@pytest.mark.usefixtures("without_credentials_secrets")
def test_crypted_file_pwd_get_without_key_file_doesnt_need_credentials_secrets(tmp_path, monkeypatch):
    import Pytre_Core.credentials as cred_module

    monkeypatch.setattr(cred_module, "KEY_FILE", tmp_path / "Pytre.key")

    assert crypted_file_pwd_get() == ""


@pytest.mark.usefixtures("without_credentials_secrets")
def test_crypted_file_pwd_change_without_credentials_secrets_raises_explicit_error(tmp_path, monkeypatch):
    import Pytre_Core.credentials as cred_module

    monkeypatch.setattr(cred_module, "KEY_FILE", tmp_path / "Pytre.key")

    with pytest.raises(RuntimeError, match="credentials_secrets.py is missing"):
        crypted_file_pwd_change("password123")


# ================================================================================
# Edge Cases
# ================================================================================


def test_crypt_with_multiline_text(tmp_path, test_encryption_key):
    """Test encryption of multiline text"""
    file_path = tmp_path / "multiline.enc"
    original = """Line 1
Line 2
Line 3
With special chars: éàç"""

    crypt_to_file(file_path, test_encryption_key, original)
    result = decrypt_from_file(file_path, test_encryption_key)

    assert result == original


def test_crypt_with_very_long_text(tmp_path, test_encryption_key):
    """Test encryption of very long text"""
    file_path = tmp_path / "long.enc"
    original = "A" * 10000

    crypt_to_file(file_path, test_encryption_key, original)
    result = decrypt_from_file(file_path, test_encryption_key)

    assert result == original
    assert len(result) == 10000
